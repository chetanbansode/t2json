# 📖 Usage Guide

`t2json` is a powerful CLI tool that fetches detailed song credits from Tidal and exports them as structured JSON.

Built for music enthusiasts, editors, and audio engineers who care about metadata.

---

## 📦 Installation

```bash
pip install t2json

🚀 Basic Usage

Launch the CLI:

```bash
t2json

🔍 Search by Song Name
```bash
t2json "song name"

Example:
```bash
t2json "Stay The Kid LAROI"

🔗 Fetch by Track URL or ID
```bash
t2json https://tidal.com/browse/track/ID

OR:
```bash
t2json TRACK_ID


💿 Fetch by Album
```bash
t2json https://tidal.com/browse/album/ID


📜 Fetch by Playlist
```bash
t2json https://tidal.com/browse/playlist/ID


🎧 Fetch from Audio File
```bash
t2json "C:\Music\song.flac"


📂 Fetch from Folder
```bash
t2json "C:\Music\"


⚙️ Settings
```bash
t2json --settings