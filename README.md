# Subtitle Translator (internal)

Docker-based web app that translates subtitle files (`.srt`, `.vtt`) using an
Ollama LLM backend. No login, intended for internal LAN use only.

## Features

- **Projects** group related uploads. Each project has a name, optional
  description, a default target language, and an optional default model.
- **Batch or single-file upload**. Files are queued; progress is shown per
  file (detecting → translating % → done).
- **Video subtitle extraction**. Browse a bind-mounted media folder, pick an
  `.mkv` / `.mp4` / `.webm` / etc., tick the subtitle tracks to extract.
  Bundled `ffmpeg` / `ffprobe` in the api container handle the demux.
  Extracted tracks land in the project and can then be translated on demand.
- **MKVToolNix GUI** (jlesage/mkvtoolnix) runs as a sidecar on port 5800 for
  interactive edits when the in-app picker isn't enough. Linked from the
  drawer's "Tools" section.
- **Language detection** via Ollama on the first few cues of each file.
- **Curated target languages** (~30 common languages, including Hungarian).
- **Downloadable translated files** once complete, named
  `<basename>.<target-lang>.<ext>` — same format as the source.
- **Settings page** configures the Ollama base URL + optional bearer token,
  then fetches the available models and offers a dropdown.

## Layout

```
api/    FastAPI + SQLite worker, bundled ffmpeg/ffprobe
web/    React (Vite) SPA served by nginx
data/   Created at runtime — SQLite DB, uploads, translated output
        (partitioned per-project: data/uploads/<id>/ and data/translated/<id>/)
media/  Default bind-mount for video browsing (override via MEDIA_PATH in .env)
```

## Running

```
cp .env.example .env   # optional — change ports / MEDIA_PATH
docker compose up -d --build
```

Open `http://<host>:8081` (SPA) and `http://<host>:5800` (MKVToolNix GUI).

First-run steps:

1. **Settings** → enter Ollama URL (e.g. `http://192.168.1.50:11434`) → Save.
2. The **Model** dropdown populates from Ollama's `/api/tags` — pick a default
   and save again.
3. **Projects** → create a project.
4. Either drag `.srt`/`.vtt` files into the dropzone, **or** click
   **Add from video…** to browse `/media`, pick a video (MKV/MP4/WebM/…),
   tick its subtitle tracks, and extract them into the project.
5. Extracted rows appear with status `extracted`. Pick a target language at
   the top of the project page, then click **Translate** on any row to
   queue it for detect → translate → download.

## Supported subtitle stream formats

ffprobe `codec_name` — handled across MKV, MP4, WebM, MOV, AVI, TS, …

| codec_name          | Format | Extract | Translate |
|---------------------|--------|---------|-----------|
| `subrip`            | SRT    | ✓       | ✓         |
| `webvtt`            | VTT    | ✓       | ✓         |
| `mov_text`          | SRT    | ✓ (transmux) | ✓     |
| `ass` / `ssa`       | ASS    | shown   | not yet   |
| `hdmv_pgs_subtitle` | PGS    | shown   | no (bitmap) |
| `dvd_subtitle`      | VobSub | shown   | no (bitmap) |
| `dvb_subtitle`      | DVB    | shown   | no (bitmap) |

## Development

Backend tests:

```
cd api
pip install -r requirements.txt
pytest
```

Frontend dev (against a running API container):

```
cd web
npm install
npm run dev
```
