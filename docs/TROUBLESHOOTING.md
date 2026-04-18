# 🛠️ Troubleshooting

If `t2json` is not working as expected, check the solutions below.

---

## ❌ Command not found

### Problem
`t2json` is not recognized as a command.

### Solution
Make sure it is installed:

```bash
pip install t2json
```
---

## 🔗 Invalid URL or ID
### Problem
Tool fails when using a link or ID.

### Solution
- Make sure the URL is valid and complete
- Ensure it is a Tidal link (not Spotify/YouTube)
- Double-check the track/album/playlist ID

---

## ⚠️ No results found

### Problem
Search returns nothing.

### Solution
- Try a more specific search query
- Include artist name
- Use track URL instead of search

Example:
```bash
t2json "Stay The Kid LAROI"
```

---

## 📭 Missing credits
### Problem
Some fields like producer/composer are missing.

### Solution
- Not all tracks have full metadata on Tidal
- Try another version of the track
- Use official releases instead of remixes

---

## 🐌 Slow performance
### Solution
- Check internet speed
- Disable optional features like genre lookup
- Avoid very large playlists

---

## 🎵 Last.fm issues (genre missing)
### Problem
Genre is missing or delayed.

### Solution
- Last.fm API may be rate-limited
- Some tracks are not available in Last.fm database
- Try again later or ignore genre

---




























