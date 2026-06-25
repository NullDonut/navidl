# navidl.py — Setup & Usage Guide

**navidl.py** downloads YouTube playlists as high-quality audio, enriches metadata (handles multi-artist, album, year, genre), embeds cover art, and organises files for **Navidrome** on Android.

---

## 1. Install Termux + Prerequisites (on the Android device)

```bash
# Install Termux from F-Droid (not Play Store), then:
pkg update && pkg upgrade -y

# Core dependencies
pkg install python ffmpeg -y

# Python packages
pip install mutagen requests

# yt-dlp (standalone binary, no Python dependency issues)
pkg install yt-dlp -y
```

## 2. Create output & git folders

```bash
mkdir -p ~/navidrome-music

# Git-ready: init once, then just add + push after each download
cd ~/navidrome-music && git init && cd ~
```

## 3. Place the script

Copy `navidl.py` to your Android device (via `scp`, `termux-url-opener`, or direct download):

```bash
# Example via curl
curl -LO https://your-server/navidl.py
# Or copy from another device
scp user@pc:/path/to/navidl.py ~/
```

Make it executable:

```bash
chmod +x ~/navidl.py
```

## 4. Run it

```bash
python3 ~/navidl.py "https://youtube.com/playlist?list=... "

# With custom album name:
python3 ~/navidl.py "URL" --album "My Awesome Mix"

# Limit to first 10 tracks:
python3 ~/navidl.py "URL" --album "Chill Vibes" --limit 10

# Skip MusicBrainz (faster, less metadata):
python3 ~/navidl.py "URL" --no-musicbrainz

# Custom output directory:
python3 ~/navidl.py "URL" --output ~/storage/music/Imports

# Git push after download:
cd ~/navidrome-music && git add -A && git commit -m "Add playlist" && git push && cd ~

# Override artist for all tracks:
python3 ~/navidl.py "URL" --artist "Various Artists"
```

### Output structure (Navidrome-ready)

```
~/navidrome-music/
├── Artist Name/
│   ├── Album Name/
│   │   ├── cover.jpg
│   │   ├── 01 - Song Title.nfo.json      ← debug info
│   │   ├── 01 - Song Title.opus
│   │   ├── 02 - Another Song.opus
│   │   └── ...
├── Artist1; Artist2/                     ← multi-artist
│   └── Album Name/
│       └── 03 - Collaboration.opus
└── ...
```

## 5. Set up Navidrome on Android

### Option A: Termux (recommended)

```bash
# In Termux:
pkg install navidrome -y   # if available, otherwise:
# Download binary from https://github.com/navidrome/navidrome/releases

mkdir -p ~/.config/navidrome
```

Create `~/.config/navidrome/navidrome.toml`:

```toml
MusicFolder = "/data/data/com.termux/files/home/navidrome-music"
Address = "0.0.0.0"
Port = 4533

# Scan every 30 minutes
ScanSchedule = "@every 30m"

# Show all artists, including Various Artists
VariousArtists = "Various Artists"
```

Create a start script `start-navidrome.sh`:

```bash
#!/data/data/com.termux/files/usr/bin/bash
navidrome --configfile ~/.config/navidrome/navidrome.toml
```

```bash
chmod +x ~/start-navidrome.sh
~/start-navidrome.sh
```

You can also run it as a Termux:Boot service for auto-start.

### Option B: Docker (if your Android has root / custom ROM)

```bash
docker run -d \
  --name navidrome \
  -p 4533:4533 \
  -v ~/navidrome-music:/music \
  -v ~/.config/navidrome:/data \
  deluan/navidrome:latest
```

## 6. Access

Open browser → `http://<android-device-ip>:4533`

Or set up a reverse proxy (Nginx/Caddy) if you want Subsonic clients (Sonixd, Sublime Music) connecting from outside.

## 7. Tips

| Problem | Solution |
|---|---|
| **Output folder missing** | `mkdir -p ~/navidrome-music` before first run |
| **yt-dlp Python error** | Use `pkg install yt-dlp` (standalone binary) instead of pip |
| **MusicBrainz rate limit** | Script already sleeps 1.2s between lookups; disable with `--no-musicbrainz` |
| **Wrong artist detection** | Use `--artist "Name" --album "Album"` to override |
| **Want MP3 instead of Opus** | Change `--audio-format opus` → `--audio-format mp3` in the script's `download_track()` |
| **Songs out of order** | YouTube playlist order is fetched as-is; reorder playlist on YouTube first |
| **Navidrome doesn't scan** | Set `ScanSchedule` shorter or trigger manually via Navidrome web UI → Refresh |

## 8. Automation (one-shot from share menu)

Save as `~/bin/termux-url-opener` (Termux will auto-launch it when you share a YouTube link):

```bash
#!/data/data/com.termux/files/usr/bin/bash
termux-wake-lock
python3 ~/navidl.py "$1" --album "Quick Import"
cd ~/navidrome-music && git add -A && git commit -m "Quick import $(date +%Y-%m-%d)" 2>/dev/null
termux-toast "Download + git committed!"
termux-wake-unlock
```

Then on Android: share a YouTube playlist URL → Termux → picks up the script automatically.

---

## File reference

| File | Purpose |
|---|---|
| `navidl.py` | Main downloader/tagger script |
| `SETUP_GUIDE.md` | This guide |
| `~/navidrome-music/` | Sorted music output (git-ready) |
| `~/.config/navidrome/navidrome.toml` | Navidrome config |

---

*Happy listening with Navidrome!*
