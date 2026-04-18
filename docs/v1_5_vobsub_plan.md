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

## Phase 0 findings (2026-04-18)

**Tool selected: [subtile-ocr](https://github.com/gwen-lg/subtile-ocr)
0.x (Rust CLI, GPL-3.0).** Last push 2026-01-26. Direct
`VobSub → SRT` pipeline, mirrors how pgsrip integrates today.

Why this one:
- Actively maintained — only candidate with a 2026 commit.
- Invocation is trivial: `subtile-ocr -l <tess_lang> -o out.srt
  in.idx` (reads the sibling `.sub` automatically).
- Tesseract language codes are the same ones we already normalise
  to in `ocr.normalize_lang_for_tesseract` — zero new mapping work.
- GPL-3.0 only attaches to derivative works; shelling out to a GPL
  binary from MIT-licensed code is fine per the FSF's own analysis.
- Better preprocessing than its predecessor (`vobsubocr`, last
  commit 2023) and the abandoned `VobSub2SRT` (last commit 2017).

Rejected alternatives (full table in commit history of this doc):
- `vobsubocr` — stale (2023), `subtile-ocr` is the active fork.
- `VobSub2SRT` — dead (2017), broken on tesseract 5.x.
- No maintained Python wrapper exists. There is **no pgsrip
  equivalent** for VobSub.

**ffmpeg confirmation.** `ffmpeg -i in.mkv -map 0:s:0 -c:s copy
out.sub` against a `dvd_subtitle` track writes BOTH `out.sub` and
`out.idx` automatically. No extra muxer flags required.

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
    """OCR a VobSub .sub via subtile-ocr; return parsed cues.

    Expects a sibling .idx next to sub_path (ffmpeg writes the pair
    automatically). Shells out to:
        subtile-ocr -l <tess_lang> -o <tmp>.srt <sub_path>.idx
    Then reads the SRT back through our existing parser.
    """
```

Implementation outline:
1. Resolve idx_path = `sub_path.with_suffix(".idx")`. Fail loud with
   "missing .idx sidecar" if not present.
2. Stage outputs in a tempdir like `ocr_pgs_sup` does.
3. `subprocess.run(["subtile-ocr", "-l", lang3, "-o", out_srt,
   str(idx_path)], check=True, capture_output=True)`. Surface stderr
   in the exception message on failure so the operator sees what
   tesseract complained about.
4. Read `out_srt`, parse via `parse_srt`, return cues.

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

### `api/Dockerfile`

Multi-stage build to keep the runtime image lean:

1. **Builder stage** — `rust:bookworm` base, install
   `libtesseract-dev` + `libleptonica-dev` + `clang`, run
   `cargo install --root /opt/subtile subtile-ocr`. Throwaway stage.
2. **Runtime stage** (existing image) — `COPY --from=builder
   /opt/subtile/bin/subtile-ocr /usr/local/bin/`. Add
   `libtesseract5` + `libleptonica6` runtime libs (likely already
   pulled in transitively by `tesseract-ocr` from v1.4.0; verify).

Estimated runtime image growth: ~5 MB (the binary itself). Builder
stage is ~1.5 GB but never ends up in the published image.

### `api/requirements.txt`

No changes. We're shelling out to a CLI, not importing a Python
library.

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

- **Image build time.** The Rust builder stage adds ~2 minutes to a
  cold `docker build` (cargo + dep crates + leptess link step).
  Cached after the first build. Acceptable for a self-hosted app.
- **subtile-ocr is single-maintainer.** Active today, but bus-factor
  one. Mitigated by the shell-out integration — if it goes stale we
  swap CLIs without touching anything outside `ocr.py`.
- **OCR quality on DVD-era subtitles.** VobSub bitmaps are typically
  lower-res than PGS (720×480 vs 1920×1080), which hurts tesseract
  accuracy. The LLM cleanup pass becomes more valuable here, not less.
- **ffmpeg .idx output dependency.** ffmpeg must produce both .sub +
  .idx for the existing extract code to work unchanged. Verified in
  Phase 0 against the docs; needs a sanity check against the
  operator's actual sample as part of step 5 below.
