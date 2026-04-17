# Bitmap subtitle OCR — plan (revised)

Bring bitmap subtitle formats into the existing translate pipeline by
OCR'ing each to SRT before it reaches the translation worker.

**Revised scope (after pgsrip/dvb/vobsub investigation):**

- **v1.4.0 — PGS only**, via pgsrip. With an optional vision-LLM
  cleanup pass that uses Ollama to correct OCR errors per cue.
- **v1.5.0 (later) — DVB + VobSub.** Deferred until we have real
  sample files in hand and time to write a PyAV-based frame extractor
  for the formats that don't have a maintained Python tool.

Reason for the split: the hard part is *frame extraction*, not OCR.
PGS has pgsrip — a battle-tested package that gives us frames AND
calls tesseract for us. DVB and VobSub need bespoke frame-extraction
work that's brittle without real test files to iterate against.

## Current state

- `api/app/video.py:35` — `CODEC_MAP` already extracts PGS / DVB /
  VobSub to `.sup` / `.sub` files via `ffmpeg -c:s copy`, but flags
  them `supported: False` so the worker refuses them.
- `api/app/worker.py:97` rejects anything that isn't `srt` / `vtt`.
- `File.format` (`api/app/models.py:59`) is the only "what kind of
  subtitle is this" field.
- Frontend video picker disables checkboxes for unsupported codecs.

The extraction half is already done — we stop at the bitmap file.

## Approach

Insert one new pipeline stage between `extracted` and `queued`:

```
extracting → extracted → ocr_queued → ocr_running (n%) → ocr_done
            └─ existing ─┘                              │
                                       (manual click)  ↓
                              detecting → translating → done
```

After OCR, the row's `stored_original_path` is rewritten to point at
the OCR'd `.srt` and `format` is updated to `srt`, so the translate
worker never needs to know the file came from a bitmap source. The
operator clicks **Translate** as today (matching the existing
`extracted` → manual Translate flow).

## LLM cleanup pass (optional, text-only in v1.4.0)

OCR errors are predictable but persistent: `l` vs `I`, `0` vs `O`,
broken italics, dropped diacritics. An LLM can correct these
per-cue. The LLM's task is narrow ("fix this transcription"), not
free-form reading, so hallucination risk stays low.

Settings:

- `ocr_llm_cleanup` (0/1) — off by default
- `ocr_llm_model` — model to use for cleanup; falls back to
  `default_model` if blank

When on, after pgsrip emits the SRT we send each cue's text to
Ollama with the correction prompt and replace the cue text with the
cleaned version.

**Text-only in v1.4.0.** pgsrip's internal flow batches subtitle
items into composite tesseract calls and only dumps PNGs in batch-
keyed groups, not per cue, so per-cue vision cleanup needs a separate
render pipeline using pgsrip's lower-level `Pgs` class. That's
deferred to v1.5.0 once the basic pipeline is verified against real
.sup samples in Docker.

Cost: roughly doubles OCR time. Captures most of the value of
vision cleanup since modern LLMs are strong at OCR-error correction
even without seeing the source image.

## Architecture changes

### `api/Dockerfile`

Add system packages:

- `tesseract-ocr`
- `tesseract-ocr-{eng,hun,spa,fra,deu,ita,por,rus,jpn,kor,
  chi-sim,chi-tra,ara}`
- `imagemagick` — pgsrip dependency

Approx image size impact: tesseract core ~25 MB, ImageMagick ~30 MB,
~10 MB per language pack → total ~150 MB.

### `api/requirements.txt`

- `pgsrip` (PGS → SRT, wraps tesseract)
- `Pillow` (PNG handling for the LLM cleanup pass)

`babelfish` comes in transitively via pgsrip.

### New module: `api/app/ocr.py`

```python
def ocr_pgs(sup_path: Path, lang_hint: str | None,
            progress_cb: Callable[[int, int], Awaitable[None]] | None
            ) -> tuple[Path, list[Path]]:
    """Run pgsrip on the .sup, return (srt_path, frame_pngs).

    frame_pngs is the list of intermediate PNGs in pgsrip's temp dir,
    one per cue, kept around so the LLM cleanup pass can pair them
    with the cues. Caller is responsible for cleanup.
    """

async def llm_cleanup_cues(client: OllamaClient, model: str,
                           cues: list[Cue], frames: list[Path],
                           lang_hint: str | None,
                           progress_cb) -> list[Cue]:
    """Per-cue vision-LLM correction. Returns cues with text replaced."""
```

A top-level `ocr_file(file_row, settings, progress_cb)` dispatches on
`source_format`. v1.4.0 only handles `"pgs"`.

### `CODEC_MAP` updates (`api/app/video.py:35`)

`hdmv_pgs_subtitle` flips to `supported: True`. Extraction args
unchanged — same `-c:s copy` to the same `.sup` output. DVB and
VobSub stay `supported: False` for v1.4.0.

### DB changes (`api/app/models.py`)

Additive columns on `File`:

- `source_format: str` — `"pgs" | ""`. Empty for files that arrived
  as text. Records origin so the OCR worker can dispatch and so the
  UI can label "OCR'd from PGS".
- `ocr_progress_pct: int` — separate from `progress_pct` because OCR
  and translate run in distinct stages.

Status enum extended on the existing `status` column:

- `ocr_queued` — extraction finished, OCR worker hasn't started
- `ocr_running` — OCR in flight
- `ocr_error` — OCR failed; user can retry
- `ocr_done` — OCR finished; row is now an SRT, awaiting manual
  Translate click

Additive columns on `Settings`:

- `ocr_llm_cleanup: int` — 0/1
- `ocr_llm_model: str` — model name; "" means use `default_model`

Migration: SQLite additive — same pattern v1.2.0 used for new
Settings columns and v1.3.0 used for `source_video_path`.

### Worker changes (`api/app/worker.py`)

Add a third FIFO queue parallel to `extract_queue` and `job_queue`:

```python
ocr_queue: asyncio.Queue[int] = asyncio.Queue()
```

OCR is CPU-bound on tesseract; running it in its own queue keeps it
from blocking ffmpeg extraction (I/O-bound) or translate (Ollama
HTTP-bound).

Flow:

1. `_process_extraction` finishes. If `source_format` is bitmap, set
   `status="ocr_queued"` (not `"extracted"`) and push the file id
   onto `ocr_queue`.
2. New `ocr_worker_loop` pulls from `ocr_queue`, runs
   `ocr.ocr_file(...)` in a thread for the pgsrip part and inline
   for the async LLM cleanup, writes the SRT to
   `data/ocr/<project_id>/<file_id>_<stem>.srt`, repoints
   `stored_original_path`, sets `format="srt"`, and sets
   `status="ocr_done"`. Does NOT auto-queue translate.
3. Operator clicks Translate as today; row enqueues onto `job_queue`.

Progress: `ocr.ocr_file` accepts a `progress_cb(done, total)` and the
worker pushes `ocr_progress_pct` updates over the existing SSE stream.

### Frontend changes

- `pages/ProjectDetail` — render new statuses ("OCR queued",
  "OCR n%", "OCR done", "OCR failed") using the existing progress bar
  pattern. The "Translate" button is enabled when `status="ocr_done"`
  exactly as it is for `status="extracted"` today.
- Video picker — PGS rows become tickable now that
  `supported: True`. No code change beyond what the API returns.
- File row sublabel showing OCR origin ("from PGS") so the operator
  knows the SRT isn't the original.
- Settings page — new section "OCR" with the cleanup toggle and
  model field.

### README changes

Codec table flips PGS row to ✓ for translate (note: "OCR via
pgsrip"). DVB and VobSub stay marked as not yet supported. Add a
short "OCR cleanup" subsection under Settings.

## Risks and unknowns

- **Image size.** Tesseract + ImageMagick + ~13 language packs adds
  ~150 MB to the api image. Acceptable.
- **OCR speed.** A 2-hour Blu-ray PGS track is typically 30 s – 2 min
  on modern hardware via tesseract. Acceptable for a queue-and-wait
  UX. The optional LLM cleanup pass roughly doubles this on a fast
  vision model; significantly more on a slow one.
- **Quality.** Tesseract is strong on Latin scripts, weaker on
  stylised fonts. The LLM cleanup pass mitigates this.
- **Track language hint.** ffprobe usually returns
  `streams[].tags.language`, but not always. The `Sup` class in
  pgsrip parses the language from the FILENAME (`file.eng.sup`), so
  the worker needs to write the extracted .sup with a language
  suffix derived from the track tag, falling back to a configurable
  default.
- **pgsrip's intermediate frames.** Plan assumes pgsrip's temp
  folder contains usable per-cue PNGs that can be paired with the
  emitted SRT cues by index. Needs verification during
  implementation; if pgsrip's temp output isn't suitable for the
  cleanup pass, the cleanup pass needs its own frame-render path
  (read .sup directly with a PGS parser).

## Tests

- `tests/test_ocr.py` — module-level: stub pgsrip, verify
  `ocr_file()` writes the SRT to the right path and updates the File
  row correctly. Real-OCR test deferred until a sample .sup is in
  the repo.
- `tests/test_worker.py` extension — queue a file with
  `source_format="pgs"`, monkeypatch ocr + Ollama, assert it lands
  at `ocr_done` (not auto-queued).
- Migration test — startup adds the new columns idempotently.

## Out of scope for v1.4.0

- DVB and VobSub support — deferred to v1.5.0.
- OCR review/edit UI page — relying on trust + the existing rename
  flow for v1.4.0.
- ASS / SSA support — those are text but our SRT/VTT parsers don't
  handle the style overrides yet. Separate task.
