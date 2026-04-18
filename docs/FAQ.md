# ❓ FAQ

## 🔧 General

### What is t2json?
`t2json` is a CLI tool that fetches detailed song credits from Tidal and exports them as structured JSON.

---

### What kind of data does it fetch?
It can fetch:
- Title
- Artist
- Album
- Year
- ISRC
- Producer
- Composer
- Lyricist
- Engineers
- Performers
- Instruments (guitar, bass, etc.)
- And more (depending on availability)

---

### Does it download music?
No.  
`t2json` only fetches metadata (song credits). It does NOT download audio or video.

---

### Is this tool legal?
Yes.  
It only retrieves publicly available metadata from Tidal.  
However, users must comply with Tidal’s terms of service.

---

### Is it affiliated with Tidal?
No.  
This project is not affiliated with or endorsed by Tidal.

---

## 🚀 Usage

### How do I start the tool?
Run:

```bash
t2json
```

---

### Can I use URLs?

Yes, you can use: Track URL, Album URL, and Playlist URL

---

### Where are settings saved?
Settings are saved locally on your system and persist across sessions

---

## 📁 Output

### Is the output compatible with other tools?
Yes.
The JSON format is compatible with tools like Kid3.

---

### Can I customize output format?
Not currently, but structured JSON is provided for easy parsing

---

## ⚠️ Issues & Limitations

### Why are some credits missing?
Not all tracks on Tidal have complete metadata

---

### Why is genre missing?
Genre is fetched via Last.fm (optional), which may be slow, fail or be limited.

---

## 🧠 Advanced

### Does it support batch processing?
Yes: folders, playlists, and albums.

---

## 💡 Tips

### What is the best way to get accurate data?
Use track, album URLs instead of search.

## 🔐 Safety

### Does this tool collect user data?
No. All operations are local except API requests.

---

### Is my data stored anywhere?
No external storage is used.



























































