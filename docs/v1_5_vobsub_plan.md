# VobSub OCR — plan (v1.5.0)

Add VobSub (`dvd_subtitle`) bitmap-subtitle OCR by extending the
v1.4.0 OCR pipeline to a second source format. DVB (`dvb_subtitle`)
remains deferred — still no sample on hand and still no maintained
Python tooling for it.

## Why VobSub now

- The operator has a real VobSub `.sub` sample to iterate against,
  unlike DVB.
- The pipeline scaffolding already exists end-to-end:
  `extracting → ocr_queued → ocr_running → ocr_done → Translate`,
  with SSE progress, retry button, optional LLM cleanup pass, and the
  status badges in the UI. v1.5.0 is *plug a second format in*, not
  *redesign anything*.

## Investigation needed before any code (Phase 0)

These have to land *before* picking an architecture, because the
chosen tool determines requirements + Dockerfile changes. I will not
guess at tool names in the plan — that's a violation of the
"never speculative" rule and the prior v1.4.0 work proved bitmap-OCR
tooling claims need to be checked, not assumed.

1. **Tool / library selection.** Identify a maintained Python module
   *or* CLI binary that takes a VobSub `.sub` + `.idx` pair (or just
   `.sub`) and produces cue text + timings. Acceptance bar: at least
   one commit in the last 18 months, runs against the operator's
   sample, and doesn't require commercial licensing.
2. **ffmpeg output shape.** Verify what `ffmpeg -c:s copy ... out.sub`
   actually writes for `dvd_subtitle`. VobSub on DVD is a `.sub` (MPEG
   PS with subpicture data) + `.idx` (text index) pair; whether
   ffmpeg emits both depends on muxer choice. May need a different
   muxer or a post-step to derive `.idx`.
3. **Container deps.** Whatever Phase 0.1 picks tells us if we need
   new system packages. The v1.4.0 image already has `tesseract-ocr`
   + 13 language packs + `imagemagick`, so a tesseract-based path is
   nearly free. PyAV-based paths add `libav*-dev` and `python3-dev`.

If Phase 0.1 finds nothing acceptable, the fallback is rolling our
own: PyAV decode → render each subpicture to a PIL image → tesseract.
That's significant bespoke work and would justify pushing v1.5.0
scope down or splitting it.

## Architecture (reusing v1.4.0 scaffolding)

### `api/app/video.py`

- `CODEC_MAP["dvd_subtitle"]`: `("sub", False, "copy")` → likely
  `("sub", True, "copy")` (extension may change after Phase 0.2).
- `BITMAP_CODECS` set gains `dvd_subtitle`.
- `source_format_for()` returns `"vobsub"` for `dvd_subtitle`,
  `"pgs"` for `hdmv_pgs_subtitle`, `""` otherwise.
- `extract_track()` may need to grow a "produce both .sub and .idx"
  branch depending on Phase 0.2.

### `api/app/ocr.py`

Add a sibling to `ocr_pgs_sup`:

```python
def ocr_vobsub(sub_path: Path, lang_hint: str | None) -> list[Cue]:
    """OCR a VobSub .sub (with sibling .idx if Phase 0.2 requires)."""
```

`llm_cleanup_cues` is already format-agnostic — reused as-is, just
labelled "VobSub" in the prompt.

`lang_hint_from_filename` already handles the
`<stem>.<lang>.streamN.<ext>` pattern that `routers/video.py` writes,
which is the same naming convention VobSub will get — no change.

### `api/app/worker.py`

`_process_extraction` already dispatches on `source_format`. Add a
`vobsub` branch that mirrors the `pgs` branch — same `ocr_queued`
landing, same `ocr_queue.put(file_id)`.

`_process_ocr` switches on `source_format`:
- `"pgs"` → `ocr.ocr_pgs_sup(...)` (existing)
- `"vobsub"` → `ocr.ocr_vobsub(...)` (new)
- anything else → fail loud with the existing defensive raise

The post-OCR write-out (cues → SRT → flip status to `ocr_done`) is
shared and untouched.

### `api/Dockerfile` + `api/requirements.txt`

Determined by Phase 0 outcome. Best case (tesseract + an existing
small Python wrapper): no Dockerfile changes, one new
`requirements.txt` line. Worst case (roll our own with PyAV): add
PyAV + dev headers, ~50 MB image growth.

### Frontend

**Zero changes required.** All the v1.4.0 UI work — status pills,
OCR progress bar, Retry OCR button, Translate-from-`ocr_done` — is
generic over `source_format`. The video picker will start showing
VobSub tracks as tickable the moment the API flips
`supported: True`.

### Database

Nothing additive needed — `source_format` already accepts arbitrary
strings, and `ocr_progress_pct` / OCR statuses landed in v1.4.0.

### README + changelog

- Codec table: VobSub row flips to `✓ (OCR via <tool>)`.
- New v1.5.0 changelog entry calling out:
  - "VobSub now translatable end-to-end"
  - DVB still deferred and why
  - Whatever the v1.4.0 verification status ends up being by then
    (smoke test result, any v1.4.x patches)

## Tests

- `tests/test_video.py` — flip `dvd_subtitle` codec assertion; add
  `source_format_for("dvd_subtitle") == "vobsub"`.
- `tests/test_ocr.py` — add `ocr_vobsub` tests with the chosen tool
  stubbed (mirrors the pgsrip stubbing pattern). The
  `llm_cleanup_cues` tests already cover the cleanup leg generically.
- **Real-OCR test feasible this time.** With the operator's sample we
  can run the chosen pipeline against it manually and verify the
  output before tagging v1.5.0. If licensing allows, a trimmed clip
  could even land as a test fixture.

## Implementation sequencing

1. Phase 0 investigation (above) — decide tooling.
2. Plan revision if Phase 0 changes the architecture (e.g. fallback
   to roll-our-own).
3. Operator drops the VobSub sample at a path we agree on (e.g.
   `data/uploads/_vobsub_sample/`); we run the chosen tool by hand
   first and confirm we like the output.
4. Code: video.py + ocr.py + worker.py changes, plus tests.
5. Build the api image, run end-to-end against the sample inside the
   container, fix anything that surfaces.
6. README + changelog + version bumps + tag v1.5.0.

## Out of scope

- **DVB.** No sample, no maintained tooling. Stays
  `supported: False`. Revisit when a sample appears.
- **Vision-LLM cleanup.** Still gated on per-cue PNG access work
  flagged in v1.4.0's roadmap. Text-only cleanup remains the offering.
- **ASS / SSA.** Text format but our SRT/VTT parsers don't handle
  style overrides. Separate task.
- **OCR review/edit UI.** Same call as v1.4.0 — relying on operator
  trust + the existing rename flow.

## Risks

- **No suitable tool found in Phase 0.1.** This is the dominant
  unknown. PGS had pgsrip; VobSub has no equivalent that I've
  verified. If Phase 0 confirms that, the honest outcome is either
  (a) defer v1.5.0 again, or (b) commit to the roll-our-own PyAV
  path with a longer timeline.
- **Image size growth.** Acceptable budget: another ~50 MB. Beyond
  that we should reconsider.
- **OCR quality on DVD-era subtitles.** VobSub bitmaps are typically
  lower-res than PGS (720×480 vs 1920×1080), which hurts tesseract
  accuracy. The LLM cleanup pass becomes more valuable here, not less.
