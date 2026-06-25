#!/usr/bin/env python3
"""
navidl.py — YouTube Playlist → Navidrome Audio Downloader & Tagger
==============================================================
Downloads YouTube playlists at highest audio quality, enriches metadata
(via MusicBrainz + title parsing), handles multi-artist, embeds cover
art, and organises files in Navidrome-friendly structure.

Usage:
  python3 navidl.py <playlist-url> [--album "Album Name"] [--artist "Artist"] [--no-musicbrainz]
"""

import argparse, json, logging, os, re, shutil, subprocess, sys, tempfile, time, urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import mutagen
import requests

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
log = logging.getLogger("navidl")

# ──────────────────────────────── Config ────────────────────────────────

OUTPUT_BASE = Path.home() / "navidrome-music"     # git-ready sorted output
TMP_DIR     = Path(tempfile.gettempdir()) / "navidl_dl"

# Navidrome convention: Artist / Album / TrackNum - Title.ext
# If multiple artists: Artist1; Artist2 / Album / TrackNum - Title.ext
NAVIDROME_FORMAT = "{artist}/{album}/{track:02d} - {title}"

# Override these per-run with --album and --artist
DEFAULT_ARTIST = "Unknown Artist"
DEFAULT_ALBUM   = "YouTube Playlist"

# ───────────────────────────── Dataclasses ──────────────────────────────

@dataclass
class Track:
    yt_id: str
    title: str          # raw YouTube title
    uploader: str       # YouTube channel
    duration: int       # seconds
    webpage_url: str
    thumb_url: str
    # enriched
    artist: str = ""
    song: str = ""
    album: str = ""
    track_num: int = 0
    track_total: int = 0
    year: int = 0
    genre: str = ""
    artists: list[str] = field(default_factory=list)  # multi-artist support
    # local paths
    dl_path: Path = None
    final_path: Path = None
    cover_path: Path = None

# ──────────────────────────── Dependencies ──────────────────────────────

DEPENDENCIES = {
    "ffmpeg": "ffmpeg",
    "yt-dlp": "yt-dlp",
}

def check_deps():
    missing = []
    for name, cmd in DEPENDENCIES.items():
        if not shutil.which(cmd):
            missing.append(name)
    if missing:
        print("Missing dependencies: " + ", ".join(missing))
        print("Install with: pkg install " + " ".join(missing))
        sys.exit(1)

# ────────────────────────── YouTube Download ────────────────────────────

def get_playlist_info(url: str) -> list[dict]:
    """Fetch playlist metadata from yt-dlp without downloading."""
    cmd = [
        "yt-dlp",
        "--flat-playlist",
        "--dump-json",
        "--no-warnings",
        url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log.error("yt-dlp failed: %s", result.stderr[:500])
        sys.exit(1)
    entries = []
    for line in result.stdout.strip().split("\n"):
        if line:
            entries.append(json.loads(line))
    log.info("Found %d tracks in playlist", len(entries))
    return entries


def download_track(info: dict, output_dir: Path) -> Path:
    """Download best audio as opus, return path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    tmpl = str(output_dir / "%(id)s.%(ext)s")
    cmd = [
        "yt-dlp",
        "-f", "bestaudio/best",
        "--extract-audio",
        "--audio-format", "opus",
        "--audio-quality", "0",
        "--output", tmpl,
        "--no-warnings",
        "--print", "after_move:filepath",
        f"https://youtube.com/watch?v={info['id']}",
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, text=True)
    # stderr goes to terminal — yt-dlp shows download progress there
    sys.stdout.flush()
    if result.returncode != 0:
        log.error("Download failed for %s", info["id"])
        return None
    path = Path(result.stdout.strip().split("\n")[-1].strip())
    if not path.exists():
        # fallback: scan directory for matching file
        matches = list(output_dir.glob(f"{info['id']}.*"))
        if matches:
            path = matches[0]
        else:
            log.error("Downloaded file not found: %s", path)
            return None
    return path


def get_video_details(video_id: str) -> dict:
    """Get detailed metadata for a single video."""
    cmd = [
        "yt-dlp",
        "--dump-json",
        "--no-warnings",
        f"https://youtube.com/watch?v={video_id}",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return {}
    return json.loads(result.stdout)

# ──────────────────────── Metadata Parsing ──────────────────────────────

ARTIST_SEPARATORS = re.compile(
    r'\s+(?:feat\.?|featuring|ft\.?|ft|with|vs\.?|versus|and|&)\s+',
    re.IGNORECASE
)

TITLE_SPLIT = re.compile(r'^\s*(.+?)\s*[–—-]\s*(.+)\s*$')

YOUTUBE_ARTIST_KEYWORDS = [
    "Topic", "VEVO", "Official", "Music", "Audio", "Lyric",
]


def parse_youtube_title(raw_title: str, uploader: str) -> tuple[str, str, list[str]]:
    """
    Parse raw YouTube title into (artist, song, multi_artists).
    Tries common patterns:
      - "Artist – Song"
      - "Artist – Song (feat. ...)"
      - uploader-based fallback
    """
    title = raw_title.strip()
    artists = []
    artist = ""
    song = ""

    # Try "Artist – Song" split
    m = TITLE_SPLIT.match(title)
    if m:
        candidate_artist = m.group(1).strip()
        candidate_song = m.group(2).strip()
        # Clean up common suffixes from song
        candidate_song = re.sub(r'\s*\(.*?(?:official|audio|lyric|video|music).*?\)\s*$', '', candidate_song, flags=re.IGNORECASE)
        candidate_song = re.sub(r'\s*\[.*?\]\s*$', '', candidate_song).strip()
        # Clean up artist
        candidate_artist = re.sub(r'\s*-+\s*Topic\s*$', '', candidate_artist, flags=re.IGNORECASE).strip()

        # Detect multi-artist in candidate_artist
        parts = ARTIST_SEPARATORS.split(candidate_artist)
        artists = [p.strip() for p in parts if p.strip()]
        artist = artists[0] if artists else candidate_artist
        song = candidate_song

        # If it's a "Topic" channel, the split is reversed: "Song – Artist"
        if any(kw.lower() in uploader.lower() for kw in ["topic"]):
            artist = candidate_song
            song = candidate_artist
            parts = ARTIST_SEPARATORS.split(artist)
            artists = [p.strip() for p in parts if p.strip()]

    else:
        # Fallback: use uploader as artist, full title as song
        artist = uploader
        song = title
        # Clean "Topic" suffix from uploader
        artist = re.sub(r'\s*-+\s*Topic\s*$', '', artist, flags=re.IGNORECASE).strip()
        artists = [artist]

    return artist, song, artists


def musicbrainz_lookup(artist_hint: str, song_hint: str) -> Optional[dict]:
    """
    Search MusicBrainz for track metadata.
    Returns dict with artist, song, album, year, genre if found.
    """
    # Rate-limit: be nice to MusicBrainz
    time.sleep(1.2)

    query = urllib.parse.quote(f'artist:"{artist_hint}" recording:"{song_hint}"')
    url = f"https://musicbrainz.org/ws/2/recording/?query={query}&fmt=json&limit=3"

    headers = {"User-Agent": "Navidl/1.0 (noreply@example.com)"}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.debug("MusicBrainz lookup error: %s", e)
        return None

    recordings = data.get("recordings", [])
    if not recordings:
        return None

    rec = recordings[0]
    result = {
        "song": rec.get("title", song_hint),
        "artists": [],
        "album": "",
        "year": 0,
        "genre": "",
    }

    # Artist credits
    for credit in rec.get("artist-credit", []):
        if isinstance(credit, dict):
            name = credit.get("name") or credit.get("artist", {}).get("name", "")
            if name:
                result["artists"].append(name)
        elif isinstance(credit, str):
            if credit.strip() in (",", "&", "feat.", "featuring"):
                pass  # separator

    # Album info
    releases = rec.get("releases", [])
    if releases:
        rel = releases[0]
        result["album"] = rel.get("title", "")
        date = rel.get("date", "")
        if date:
            result["year"] = int(date[:4])
        # Tags/genre
        for tag in rel.get("tags", []):
            if tag.get("count", 0) >= 1:
                result["genre"] = tag.get("name", "")
                break

    return result


# ──────────────────────── Cover Art ─────────────────────────────────────

def download_cover(thumb_url: str, output_path: Path) -> bool:
    """Download thumbnail/cover image."""
    if not thumb_url:
        return False
    try:
        resp = requests.get(thumb_url, timeout=30)
        resp.raise_for_status()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(resp.content)
        log.info("Cover saved: %s", output_path)
        return True
    except Exception as e:
        log.warning("Cover download failed: %s", e)
        return False


def embed_cover(audio_path: Path, cover_path: Path) -> bool:
    """Embed cover art into audio file using mutagen."""
    try:
        audio = mutagen.File(str(audio_path))
        if audio is None:
            log.warning("Cannot open audio for tagging: %s", audio_path)
            return False

        img_data = cover_path.read_bytes()

        if isinstance(audio, mutagen.mp3.MP3):
            from mutagen.id3 import APIC, ID3
            try:
                audio.tags.add(APIC(
                    encoding=3, mime="image/jpeg", type=3,
                    desc="Cover", data=img_data
                ))
            except AttributeError:
                pass
        elif isinstance(audio, mutagen.oggopus.OggOpus):
            from mutagen.flac import Picture
            import base64
            pic = Picture()
            pic.type = 3
            pic.mime = "image/jpeg"
            pic.width = 0
            pic.height = 0
            pic.depth = 0
            pic.colors = 0
            pic.data = img_data
            audio["metadata_block_picture"] = [base64.b64encode(pic.write()).decode()]
        elif isinstance(audio, mutagen.flac.FLAC):
            from mutagen.flac import Picture
            pic = Picture()
            pic.type = 3
            pic.mime = "image/jpeg"
            pic.width = 0
            pic.height = 0
            pic.depth = 0
            pic.colors = 0
            pic.data = img_data
            audio.add_picture(pic)
        elif isinstance(audio, mutagen.mp4.MP4):
            from mutagen.mp4 import MP4Cover
            audio["covr"] = [MP4Cover(img_data, MP4Cover.FORMAT_JPEG)]

        audio.save()
        return True
    except Exception as e:
        log.warning("Cover embed failed: %s", e)
        return False

# ──────────────────────── Tagging ───────────────────────────────────────

def tag_file(track: Track, audio_path: Path):
    """Apply metadata tags to audio file using mutagen."""
    try:
        audio = mutagen.File(str(audio_path))
        if audio is None:
            log.warning("Cannot open for tagging: %s", audio_path)
            return
    except Exception as e:
        log.warning("Tagging error: %s", e)
        return

    artist_str = "; ".join(track.artists) if track.artists else track.artist

    # Common tags (Vorbis comments / ID3)
    tag_map = {
        "title": track.song or track.title,
        "artist": artist_str,
        "album": track.album,
        "date": str(track.year) if track.year else "",
        "tracknumber": str(track.track_num),
        "tracktotal": str(track.track_total),
        "genre": track.genre,
        "website": track.webpage_url,
    }

    if isinstance(audio, mutagen.mp3.MP3):
        _tag_mp3(audio, tag_map)
    else:
        _tag_vorbis(audio, tag_map)

    audio.save()
    log.info("Tagged: %s", audio_path.name)


def _tag_mp3(audio, tags: dict):
    """Tag MP3 with ID3v2.4."""
    try:
        from mutagen.id3 import ID3, TIT2, TPE1, TALB, TDRC, TRCK, TCMP, TCON, WOAS
    except ImportError:
        return

    mapping = {
        "title":      ("TIT2", TIT2),
        "artist":     ("TPE1", TPE1),
        "album":      ("TALB", TALB),
        "date":       ("TDRC", TDRC),
        "tracknumber":("TRCK", TRCK),
        "genre":      ("TCON", TCON),
    }

    for key, (frame_id, frame_cls) in mapping.items():
        val = tags.get(key, "")
        if val:
            try:
                audio.tags.add(frame_cls(encoding=3, text=val))
            except Exception:
                pass

    # Website
    if tags.get("website"):
        try:
            from mutagen.id3 import WOAS
            audio.tags.add(WOAS(url=tags["website"]))
        except Exception:
            pass


def _tag_vorbis(audio, tags: dict):
    """Tag Ogg/FLAC with Vorbis comments."""
    vorbis_map = {
        "title": "TITLE",
        "artist": "ARTIST",
        "album": "ALBUM",
        "date": "DATE",
        "tracknumber": "TRACKNUMBER",
        "tracktotal": "TRACKTOTAL",
        "genre": "GENRE",
        "website": "WEBSITE",
    }

    for key, vorbis_key in vorbis_map.items():
        val = tags.get(key, "")
        if val:
            audio[vorbis_key] = val

    # Multi-artist: also write ARTISTS (PICARD style)
    if tags.get("artist"):
        audio["ARTISTS"] = tags["artist"]


# ──────────────────────── File Organisation ─────────────────────────────

def navidrome_path(track: Track) -> Path:
    """Build destination path per Navidrome convention."""
    artist_dir = "; ".join(track.artists) if track.artists else (track.artist or DEFAULT_ARTIST)
    album_dir = track.album or DEFAULT_ALBUM

    # Sanitise folder/file names
    artist_dir = _sanitise(artist_dir)
    album_dir = _sanitise(album_dir)

    ext = track.dl_path.suffix if track.dl_path else ".opus"
    filename = f"{track.track_num:02d} - {_sanitise(track.song or track.title)}{ext}"

    return OUTPUT_BASE / artist_dir / filename


def _sanitise(name: str) -> str:
    """Remove characters illegal in FAT32/Android filenames."""
    sanitised = re.sub(r'[<>:"/\\|?*]', '_', name)
    sanitised = re.sub(r'\s+', ' ', sanitised).strip()
    sanitised = re.sub(r'\.+$', '', sanitised)  # trailing dots
    return sanitised or "Unknown"


# ──────────────────────── Main Pipeline ─────────────────────────────────

def process_track(entry: dict, index: int, total: int, album: str, use_mb: bool) -> Optional[Track]:
    """Full pipeline for one track."""
    yt_id = entry["id"]
    log.info("[%d/%d] Processing: %s", index + 1, total, entry.get("title", yt_id))

    # ── 1. Get full details ──
    details = get_video_details(yt_id)
    if not details:
        log.warning("Skipping %s — no details", yt_id)
        return None

    raw_title = details.get("title", "")
    uploader = details.get("uploader", details.get("channel", ""))
    duration = details.get("duration", 0)
    thumb_url = details.get("thumbnail", "")

    track = Track(
        yt_id=yt_id,
        title=raw_title,
        uploader=uploader,
        duration=duration,
        webpage_url=details.get("webpage_url", f"https://youtu.be/{yt_id}"),
        thumb_url=thumb_url,
        album=album,
        track_num=index + 1,
        track_total=total,
    )

    # ── 2. Parse YouTube title ──
    artist, song, artists = parse_youtube_title(raw_title, uploader)
    track.artist = artist
    track.song = song
    track.artists = artists

    # ── 3. MusicBrainz enrichment ──
    if use_mb:
        mb_data = musicbrainz_lookup(artist, song)
        if mb_data:
            log.info("  MusicBrainz: %s — %s", mb_data.get("artists", [artist]), mb_data.get("song", song))
            if mb_data.get("artists"):
                track.artists = mb_data["artists"]
                track.artist = mb_data["artists"][0]
            if mb_data.get("song"):
                track.song = mb_data["song"]
            if mb_data.get("album"):
                track.album = mb_data["album"]
            if mb_data.get("year"):
                track.year = mb_data["year"]
            if mb_data.get("genre"):
                track.genre = mb_data["genre"]

    # ── 4. Download audio ──
    track.dl_path = download_track(details, TMP_DIR)
    if not track.dl_path:
        return None
    log.info("  Downloaded: %s", track.dl_path.name)

    # ── 5. Determine final path ──
    track.final_path = navidrome_path(track)

    # ── 6. Download cover ──
    cover_path = track.final_path.parent / "cover.jpg"
    track.cover_path = cover_path
    if not cover_path.exists():
        download_cover(thumb_url, cover_path)
    else:
        log.info("  Cover exists: %s", cover_path)

    # ── 7. Tag ──
    tag_file(track, track.dl_path)

    # ── 8. Embed cover ──
    if cover_path.exists():
        embed_cover(track.dl_path, cover_path)

    # ── 9. Move to final location ──
    track.final_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(track.dl_path), str(track.final_path))
    log.info("  → %s", track.final_path)

    # ── 10. Write .nfo for debugging ──
    nfo_path = track.final_path.with_suffix(".nfo.json")
    nfo_data = {
        "yt_id": track.yt_id,
        "raw_title": track.title,
        "parsed_artist": track.artist,
        "parsed_song": track.song,
        "artists": track.artists,
        "album": track.album,
        "year": track.year,
        "genre": track.genre,
        "url": track.webpage_url,
    }
    nfo_path.write_text(json.dumps(nfo_data, indent=2))
    log.info("  NFO written: %s", nfo_path.name)

    return track


def main():
    parser = argparse.ArgumentParser(
        description="Download YouTube playlist as audio, enrich metadata, organise for Navidrome."
    )
    parser.add_argument("url", help="YouTube playlist URL")
    parser.add_argument("--album", "-a", default="", help="Override album name")
    parser.add_argument("--artist", "-r", default="", help="Override artist name (for all tracks)")
    parser.add_argument("--no-musicbrainz", action="store_true", help="Skip MusicBrainz enrichment")
    parser.add_argument("--output", "-o", default=None, help="Output base directory (default: ~/storage/music)")
    parser.add_argument("--start", type=int, default=1, help="Starting track number (default: 1)")
    parser.add_argument("--limit", type=int, default=0, help="Max tracks to process (default: all)")

    args = parser.parse_args()

    global OUTPUT_BASE
    if args.output:
        OUTPUT_BASE = Path(args.output)

    check_deps()
    OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)

    use_mb = not args.no_musicbrainz
    album = args.album or DEFAULT_ALBUM

    # Fetch playlist
    entries = get_playlist_info(args.url)
    if not entries:
        log.error("No tracks found in playlist.")
        sys.exit(1)

    if args.limit:
        entries = entries[: args.limit]

    total = len(entries)
    success = 0

    for i, entry in enumerate(entries):
        try:
            track = process_track(entry, i + args.start - 1, total, album, use_mb)
            if track:
                success += 1
        except KeyboardInterrupt:
            log.info("Interrupted — stopping.")
            break
        except Exception as e:
            log.error("Error processing %s: %s", entry.get("id", "?"), e)

    # Cleanup temp
    if TMP_DIR.exists():
        shutil.rmtree(TMP_DIR, ignore_errors=True)

    log.info("Done! %d/%d tracks processed successfully.", success, total)
    log.info("Music saved to: %s", OUTPUT_BASE)


if __name__ == "__main__":
    main()
