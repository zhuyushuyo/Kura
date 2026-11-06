# Changelog

## 3.0.1 — 2026-11-06

### Fixed
- Windows: "Show in folder" opened *Documents* instead of the download's folder. Explorer is now called with a
  correctly quoted `/select,"…"` command and normalised `\` separators (paths chosen in Qt dialogs use `/`).
- The completion notification no longer runs under the "Show in folder" button with long titles: notifications
  have a fixed width, wrap their text and put the button below it. They also show the folder the file was saved to.
- Save folders are stored with the OS's own separators; text from web pages is always shown as plain text.

## 3.0.0 — 2026-11-06

Renamed from *YouTube Downloader Professional* to **Kura（蔵）** and rebuilt.

### Engine
- yt-dlp is installed with its `default` extras (yt-dlp-ejs) and updated automatically on start (stable / nightly).
- Deno (official PyPI build) is installed as the JavaScript runtime that yt-dlp needs for YouTube.
- Downloads run in a local work folder; only finished files are moved to the save folder (safe with Google Drive).
- Settings, library, logs, browser data and the Python environment live in a per-computer folder
  (`%LOCALAPPDATA%\Kura`, `~/Library/Application Support/Kura`) instead of the app folder.
- Progress is computed from byte counts across video + audio (no more 0→100% twice).
- ffmpeg is detected at start-up with a guided setup (Windows download / Homebrew / pick a file);
  no silent fallback to low quality.

### Security
- Built-in browser runs with the Chromium sandbox; camera/microphone/location/notification requests are denied.
- Cookies from the built-in browser are passed to yt-dlp in memory (no temporary cookie file, no extended expiry).
- Guest mode is the default; Windows Chrome/Edge cookie borrowing removed (app-bound encryption).

### New features
- Download queue: multiple URLs, drag & drop, clipboard monitoring, link context menu, concurrency, pause/resume,
  cancel, retry, restore after restart.
- Playlists and channels: pick from a list, skip already-downloaded videos, polite delays, optional subfolder.
- Preview panel: thumbnail, duration, max resolution, HDR, chapters, subtitles, estimated size, conversion plan;
  manual format picker.
- Clip by time range or chapter.
- New presets: Edit 1080p (H.264/AAC MP4), Edit 4K (GPU H.264/HEVC conversion), Best (MKV), M4A, MP3 320k, WAV,
  plus custom presets. Thumbnail/metadata/chapters embedded.
- Subtitles converted to clean SRT (rolling auto-caption duplicates removed), embedded or saved, TXT/CSV transcripts.
- Citations (APA 7, MLA 9, Chicago, Japanese), metadata JSON, library CSV export.
- Library with thumbnails and search; re-download with another preset.
- Japanese error guide with causes and fixes; one-click error report (secrets removed).
- File-name rule with tokens, forbidden-character replacement, collision-safe names, Windows path-length guard.
- New UI: navigation rail, browser + download panel, queue, library, single settings page, status bar,
  toasts and OS notifications, shortcuts, light/dark/system themes everywhere.
- uv-based launchers for Windows and macOS (fast second start).
- Headless CLI (`python -m kura.core.cli`) and automated tests.
