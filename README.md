# VOD Set Splitter

A Windows app for cutting a long tournament broadcast into one video per set, ready for YouTube, with a thumbnail
for each.

It finds where sets change by watching the scoreboard, then you check each set in the built-in player, fix any
start or end, and mark it done. When a set is marked done, it reads the player names and round off the scoreboard.
**Export videos** saves every set as its own video, plus a 1280×720 thumbnail with both players' characters.

Nothing leaves your PC.

## Setting it up

You need Windows 10 or 11, [Python](https://www.python.org) 3.11 or newer, and [ffmpeg](https://www.gyan.dev/ffmpeg/builds/)
on your PATH. [Tesseract](https://github.com/UB-Mannheim/tesseract/wiki) is optional. It reads the names off the scoreboard.

```
py -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python scripts\make_shortcuts.py
```

The last line adds **VOD Set Splitter** to the Start Menu and the desktop. Open it from either, then right-click its
taskbar button and choose **Pin to taskbar**. `run.vbs` opens it too.

## Using it

1. **Open video**, or drag a video onto the window. MP4, MKV, MOV and TS all work.
2. **Find sets** splits the video wherever the scoreboard changes.
3. Click a set in the list. The bar under the video covers just that set and a little either side, for precise scrubbing.
   The thin bar below that is the whole video.
4. At the right moment, **Set start here** or **Set end here**. **End previous set here** ends the set before at the
   playhead, and **Add set here** splits a set in two.
5. Pick each player's character and skin. The thumbnail beside them updates as you go.
6. **Mark done** locks the set's times, reads its names off the scoreboard, starts the next set one second later, and
   moves on to it. Titles can still be edited after.
7. **Export videos** when the list is ready.

Every change saves by itself and can be undone with the **Undo** button that appears, or with Ctrl+Z. Reopening a
video brings back its sets, and **Recent** on the first screen lists the videos you've worked on.
**Open project** opens a project file from the old version.

### Keys

| Key | Does |
|---|---|
| Space | Play or pause |
| ← → | Back or forward 5 seconds (Shift: 30 seconds) |
| , . | One frame back or forward |
| ↑ ↓ | Previous or next set |
| I / O | Set start here / Set end here |
| D | Mark done |
| Ctrl+Z | Undo |

## Your own setup

Everything is in **Settings**, saves as soon as you change it, and is kept in `%APPDATA%\VOD Set Splitter`
(never in this folder).

| Setting | What it's for |
|---|---|
| **Character images** | A folder of character renders for the thumbnails. Use `Fox/Default.png`, `Fox/Red.png` folders, or flat names like `Fox - Red.png`. Transparent PNG or WebP works best. See `character-assets/README.md`. |
| **Background** | The picture behind the thumbnails. A blue forest is included. |
| **Tesseract** | Found by itself if it's installed in the usual place. Otherwise choose `tesseract.exe`. |
| **Scoreboard area** | **Choose on the video**, then drag a box around your broadcast's scoreboard. Find sets watches that box, and names are read from it. |
| **Check every / Change needed / Settle time / Shortest set** | How Find sets decides a set has changed: how often it looks, how much the scoreboard must change, how long it waits for the change to settle, and the shortest a set can be. |
| **Cuts** | **Exact** re-encodes for frame-accurate cuts. **Fast** copies the video as it is, which is much quicker, but each cut lands on the nearest keyframe. |

## Command line

```
set PYTHONPATH=src
.venv\Scripts\python -m vod_set_splitter.cli scan "D:\Videos\tournament.mp4" --output sets.json
.venv\Scripts\python -m vod_set_splitter.cli export sets.json "D:\Videos\Tournament Sets"
```

Add `--fast-copy` to export without re-encoding.

## For developers

- `src/vod_set_splitter/desktop.py`: the app. It runs the page in `ui/index.html` with pywebview, and serves the video to it from a local web server.
- `core.py`: finding sets, editing their times, and exporting with ffmpeg.
- `metadata.py`: reading names and rounds off the scoreboard with Tesseract.
- `thumbnail.py`: drawing thumbnails.
- `windows_shell.py`: the taskbar identity, icon and shortcuts.

Run the tests with `set PYTHONPATH=src` then `.venv\Scripts\python -m unittest discover -s tests`.
`tests\demo.py` opens the app off screen on a video and saves screenshots, using a copy of your settings.

## License

MIT
