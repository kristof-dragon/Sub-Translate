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
| `hdmv_pgs_subtitle` | PGS    | ✓ (OCR) | ✓         |
| `dvd_subtitle`      | VobSub | shown   | no (bitmap) |
| `dvb_subtitle`      | DVB    | shown   | no (bitmap) |

PGS (Blu-ray bitmap subtitles) are demuxed as `.sup`, run through
tesseract via [pgsrip](https://github.com/ratoaq2/pgsrip), and the
recovered SRT is fed back into the normal translate pipeline. An
optional per-cue Ollama "OCR cleanup" pass can be enabled in Settings
to fix common OCR mistakes before translation. VobSub and DVB still
extract to disk for archival, but in-app translation is deferred to a
future release.

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

## Changelog

### v1.4.0

- **PGS OCR** — Blu-ray PGS bitmap subtitle tracks now extract, OCR, and
  translate end-to-end. Extraction lands the row at `ocr_queued`; a new
  CPU-bound worker picks it up, runs pgsrip (tesseract under the hood),
  writes the recovered SRT, and lands at `ocr_done` for the operator to
  click **Translate**. A `Retry OCR` button is offered on `ocr_error`
  rows.
- **Optional OCR cleanup pass** — Settings exposes a toggle plus a
  separate cleanup-model dropdown. When on, each OCRed cue is sent
  through Ollama once with a "fix OCR errors, don't translate" prompt
  before translation. A small fast model (e.g. `llama3.2:3b`) is
  usually enough — translation can use a larger one independently.
- **OCR progress bar** — independent `ocr_progress_pct` counter so the
  OCR phase has visible progress without overwriting the translation
  bar later in the pipeline.
- **Container**: `tesseract-ocr` plus 13 bundled language packs (eng,
  hun, spa, fra, deu, ita, por, rus, jpn, kor, chi-sim, chi-tra, ara)
  + `imagemagick` baked into the API image. `pgsrip==0.1.12` added to
  `requirements.txt`.
- **DB**: additive `source_format` and `ocr_progress_pct` columns on
  the File table; `ocr_llm_cleanup` and `ocr_llm_model` on Settings.
  Existing deployments pick these up automatically on startup.

VobSub and DVB are still deferred — they need bespoke per-frame
rendering work that no maintained Python library currently provides.

### v1.3.1

- **Fix**: drop the `:ro` flag on the media bind mount in
  `docker-compose.yml`. v1.3.0's bulk export silently failed to write
  subtitles next to source videos because the container couldn't
  write to `/media`. Re-deploying (`docker compose up -d`) picks up
  the new mount.
- **Select-all / deselect-all** in the project file list: tri-state
  master checkbox in the header on desktop, labeled toolbar button
  on mobile (header is hidden under 700 px).

### v1.3.0

- **Rename translated files** — pencil-icon inline editor on each
  completed row. The `.{target_lang}.{format}` suffix is preserved as
  a read-only tail so Plex/Jellyfin auto-detection keeps working;
  `os.replace()` handles the on-disk move atomically.
- **Bulk export back to the media folder** — tick-box column + "Export
  selected" button. Subtitles that were extracted from a video go back
  next to their source automatically; uploaded files prompt once for a
  destination folder via a new FolderPicker reusing `/api/browse`.
  Mixed selections dispatch both passes in one click. Skip-existing
  policy (no overwrite); summary modal reports written vs. skipped.
- **Mobile-responsive ProjectDetail** — file rows restack as three-tier
  cards below 700 px, with inline "Detected/Target" labels replacing
  the hidden column headers.
- **Slide-in drawer on narrow screens** — the left nav auto-hides below
  700 px and toggles from a hamburger in the topbar; backdrop click
  closes it. Desktop layout unchanged.
- **DB**: additive `source_video_path` column on the File table so
  extracted subtitles remember their origin for the auto-export pass.

### v1.2.0

- **Async extraction queue** — `ffmpeg` demux runs on a dedicated worker so
  operators can close the picker, queue another video, and keep browsing
  while earlier tracks are still being extracted.
- **Ollama `think=false` toggle** in Settings — sends `think: false` on
  reasoning-capable models (deepseek-r1, qwq, gpt-oss) so the chain-of-
  thought prelude is skipped and the translated output comes back
  immediately.
- **Configurable request timeout** — per-call Ollama HTTP timeout (10 s –
  2 h) for big local models on slow hardware.
- **Context-window override (`num_ctx`)** — optional `options.num_ctx`
  forwarded to Ollama. The Settings page surfaces a derived "context
  sent" flag so the operator can see whether a context value is attached
  to each `/api/generate` call.
- **Fix**: skip macOS AppleDouble (`._*`) sidecars in `/api/browse` so
  bind-mounted media folders from macOS hosts no longer 500.
- **DB**: additive SQLite migration at startup — existing deployments
  pick up the new Settings columns without wiping data.

### v1.1.0

- Swap `mkvtoolnix-cli` for `ffmpeg`/`ffprobe` in the api container;
  extraction now supports MKV, MP4, WebM, MOV, AVI, TS, … not just MKV.
- Decouple extraction from translation — extracted tracks land in the
  project as `extracted` and are translated on demand via a per-row
  button, instead of auto-queueing.
- Per-project storage layout (`data/uploads/<id>/`,
  `data/translated/<id>/`).
- LLM status dot in the topbar (green/red/yellow); click opens Settings.

### v1.0.0

- Initial internal-LAN release. Projects, batch SRT/VTT upload, Ollama
  language detection, chunked translation, per-file progress, download.
