# Subtitle Translator (internal)

Docker-based web app that translates subtitle files (`.srt`, `.vtt`) using an
Ollama LLM backend. No login, intended for internal LAN use only.

## Features

- **Projects** group related uploads. Each project has a name, optional
  description, a default target language, and an optional default model.
- **Batch or single-file upload**. Files are queued; progress is shown per
  file (detecting → translating % → done).
- **MKV subtitle extraction**. Browse a bind-mounted media folder, pick an
  `.mkv`, tick the subtitle tracks you want translated. Bundled
  `mkvtoolnix` CLI in the api container does the demux.
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
api/    FastAPI + SQLite worker, mkvtoolnix CLI
web/    React (Vite) SPA served by nginx
data/   Created at runtime — SQLite DB, uploads, translated output
media/  Default bind-mount for MKV browsing (override via MEDIA_PATH in .env)
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
   **Add from MKV…** to browse `/media`, pick an MKV, tick its subtitle
   tracks, and extract them.
5. Watch progress → download translated files.

## Supported subtitle track formats inside MKVs

| codec_id            | Format | Extract | Translate |
|---------------------|--------|---------|-----------|
| `S_TEXT/UTF8`       | SRT    | ✓       | ✓         |
| `S_TEXT/WEBVTT`     | VTT    | ✓       | ✓         |
| `S_TEXT/ASS` / `SSA`| ASS    | shown   | not yet   |
| `S_HDMV/PGS`        | PGS    | shown   | no (bitmap) |
| `S_VOBSUB`          | VobSub | shown   | no (bitmap) |

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
