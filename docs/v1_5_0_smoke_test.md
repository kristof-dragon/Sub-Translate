# v1.5.0 — VobSub OCR end-to-end smoke test

> **Paste this file into a fresh Claude Code session.** It is fully
> self-contained: do not assume any prior conversation context.

## Why this session exists

`v1.5.0` (released 2026-04-18) added VobSub bitmap-subtitle OCR via
[subtile-ocr](https://github.com/gwen-lg/subtile-ocr) on top of the
v1.4.0 PGS pipeline. It shipped with **unit tests only** — the
end-to-end Docker path has not yet been exercised against a real DVD
`.sub` sample. Both the README v1.5.0 changelog entry and the GitHub
release notes carry an explicit verification-status warning.

Goal of this session: rebuild the API image with the new Rust builder
stage, run `subtile-ocr` against the operator's DVD `.sub` sample
inside the running container, and walk the file through the UI from
`extracted → ocr_queued → ocr_running → ocr_done → translated`. Land a
`v1.5.1` patch if anything surfaces.

DO NOT pile new architecture on top of `v1.5.0` until this session is
complete.

## Background — what was shipped

- `api/app/ocr.py::ocr_vobsub(sub_path, lang_hint)` shells out to
  `subtile-ocr -l <tess_lang> -o <out>.srt <sub_path>.idx`. Expects
  the `.idx` sidecar that `ffmpeg` writes alongside `.sub` for
  `dvd_subtitle` tracks.
- `api/app/worker.py::_process_ocr` dispatches on
  `f.source_format`: `pgs` → `ocr_pgs_sup`, `vobsub` → `ocr_vobsub`.
- `api/app/worker.py::_process_extraction` lands both `pgs` and
  `vobsub` extractions at `ocr_queued` and pushes them to
  `ocr_queue`.
- `api/app/video.py::CODEC_MAP["dvd_subtitle"]` = `("sub", True,
  "copy")`; `extract_track` validates that ffmpeg wrote both `.sub`
  and `.idx` for `dvd_subtitle` tracks.
- `api/app/routers/files.py::retry_ocr` accepts both `pgs` and
  `vobsub` source formats.
- `api/Dockerfile` has a multi-stage build: a `rust:1-bookworm`
  builder compiles `subtile-ocr 0.2.6` (pinned) against
  `libtesseract-dev` + `libleptonica-dev`; the ~5 MB binary is
  `COPY`d into the runtime image. Runtime image gained
  `libleptonica-dev` so the binary's dynamic dep resolves.
- Frontend: zero changes — all UI was already generic over
  `source_format`.

Implementation plan and Phase 0 tool selection notes:
[docs/v1_5_vobsub_plan.md](v1_5_vobsub_plan.md).

## Pre-flight — before starting

Operator: confirm or update these in your first message to Claude.

1. **Sample path.** Where is the DVD `.sub` (and matching `.idx`)
   sample on disk? Paste the path. If it is inside the bind-mounted
   media folder you can also provide the surrounding `.mkv`/`.mp4`
   path so step 3 can exercise the UI extraction flow end-to-end.
2. **Cold rebuild OK?** First `docker compose build api` will add
   roughly **2 minutes** for the Rust builder stage (cargo + leptess
   link). Subsequent rebuilds hit the cache. Confirm this is fine
   before kicking off step 1.
3. **Currently-running deployment.** Are the existing `api` / `web`
   containers safe to take down for the rebuild, or do you need a
   maintenance window?

## Step 1 — Rebuild the API image

```sh
docker compose build api
docker compose up -d api
docker compose logs -f api    # tail until "worker loop started" appears
```

What to watch for:

- The `subtile-builder` stage should `cargo install --version 0.2.6
  subtile-ocr` cleanly. If it fails, the most likely culprits are:
  - `crates.io` rate-limit / network — retry.
  - Upstream pinned-version yank — bump the version in
    `api/Dockerfile` only after re-checking
    <https://crates.io/crates/subtile-ocr/versions> for the newest
    stable release. **Do not speculate; verify the version
    actually exists.**
- The runtime `apt-get` step should pull `libleptonica-dev` cleanly.
- The final `COPY --from=subtile-builder` should land
  `/usr/local/bin/subtile-ocr` in the runtime image.

## Step 2 — Sanity-check `subtile-ocr` inside the container

Replace `/path/to/sample.idx` with the path the operator provided in
pre-flight (mount-relative to the api container — recall the bind
mount is `/media`).

```sh
docker compose exec api subtile-ocr --version
docker compose exec api subtile-ocr -l eng \
  -o /tmp/out.srt /path/to/sample.idx
docker compose exec api head -20 /tmp/out.srt
```

What "good" looks like:

- `--version` prints `subtile-ocr 0.2.6` (or whatever pinned version
  the Dockerfile carries).
- The OCR run prints progress to stderr but exits 0.
- `/tmp/out.srt` contains plausible cue text — typos are expected
  (DVD subtitles are 720×480; tesseract accuracy is lower than on
  PGS), spelling errors are fine. The shape should look like real
  SRT (`1\n00:00:01,000 --> 00:00:02,000\nSomeText\n\n…`).

If subtile-ocr exits non-zero or produces an empty file, capture the
full stderr and stop here — fix that before moving on.

## Step 3 — End-to-end via the UI

Only do this step if the source `.sub`/`.idx` lives inside the
bind-mounted media folder, ideally next to the originating
`.mkv`/`.mp4` so the extraction flow can be exercised too. If the
operator only has the loose `.sub`/`.idx` files, **skip this step**
and rely on step 2 + an alternative: place the `.sub` and `.idx` into
`data/uploads/<some-project-id>/` directly with a manual DB row. Step
3 is the preferred path when possible.

UI flow:

1. Open <http://localhost:8081> (or the configured host).
2. Pick or create a project; set its default target language and
   model.
3. Click **Add from video…**, browse to the DVD-rip video, tick the
   `dvd_subtitle` track (it should now show as ✓ supported, not as
   "shown / no").
4. The row should walk through:
   - `extracting` → ffmpeg writes `.sub` + `.idx` under
     `data/uploads/<pid>/` (verify both files exist on host).
   - `ocr_queued` → row badge appears, OCR queue picks it up.
   - `ocr_running` with progress bar 0–50% (subtile-ocr) then
     50–100% if the optional Ollama cleanup pass is enabled.
   - `ocr_done` → row reaches "ready to translate" state.
5. Click **Translate** → `queued → detecting → translating → done`.
6. Click the download icon, open the SRT, eyeball the output.

Things to watch in the API logs:

- `worker.py` should log the dispatch: "ocr_queued for file …",
  "ocr_running …".
- subtile-ocr stderr is captured into the `RuntimeError` message
  on failure. Surface any stderr to the operator.
- SSE stream should push `ocr_progress_pct` updates the UI consumes
  (you can verify in the browser devtools Network tab → EventSource).

## Step 4 — Report findings

For each issue surfaced, capture:

- The status the row got stuck at.
- Any error rendered on the row (`f.error` is shown in the UI).
- Relevant `docker compose logs api` lines.

Decision tree:

- **Everything green end-to-end:** remove the verification-status
  warning from the v1.5.0 README changelog entry and the v1.5.0
  GitHub release notes. Save a memory note that v1.5.0 has been
  verified end-to-end. Optionally tag the verification as
  `v1.5.0-verified` in the release body.
- **Bug found:** land a `v1.5.1` patch.
  - Reproduce locally via the failing path.
  - Fix in the smallest scoped change.
  - Add a unit test that would have caught it (within the
    constraints of subprocess stubbing — see `tests/test_ocr.py`'s
    existing `_make_vobsub_pair` pattern).
  - Bump version in `api/app/main.py`, `web/package.json`,
    `web/package-lock.json` to `1.5.1`. Add a changelog entry.
  - Commit, tag, push, `gh release create v1.5.1`.
  - Verify the patch end-to-end with the same smoke test.

## Sidebar — sourcing a Blu-ray PGS sample for v1.4.0 verification

The operator asked how to find a Blu-ray rip with PGS subtitles, since
the v1.4.0 PGS path is also unverified end-to-end. PGS subtitles are
the standard for Blu-ray discs (1920×1080, image-based). Options for
a sample:

- Any Blu-ray you own — rip with [MakeMKV](https://www.makemkv.com/)
  (free during beta), tick the subtitle tracks. PGS will appear as
  `hdmv_pgs_subtitle` in `ffprobe`.
- Public-domain Blu-ray remuxes occasionally exist on the Internet
  Archive — search for "blu-ray remux" + a public-domain title (e.g.
  *His Girl Friday*, *Night of the Living Dead*).
- A trimmed ~1-minute clip is enough; smaller is faster to iterate.

Once a `.sup` is in hand, the same smoke-test protocol applies — the
PGS track lands at `extracted → ocr_queued → ocr_running → ocr_done
→ translated`, just routed through `pgsrip` instead of `subtile-ocr`.

## Out of scope for this session

- DVB subtitles. Still no maintained tooling. Do not attempt.
- ASS / SSA. Text format but our parsers don't handle style overrides.
  Separate task.
- OCR review/edit UI. Operator-trust + rename flow remains the
  current offering, deliberately.
- Vision-LLM cleanup. Needs a per-cue PNG render pipeline neither
  backend exposes; on the longer-term roadmap.

## Working-style reminders

- This is a verification session, not a feature session. Stay tight.
- **Never make speculative or haphazard suggestions** — read files
  before flagging anything as broken. If you're not sure, say so
  rather than asserting.
- If `subtile-ocr` needs a version bump for any reason, **verify the
  candidate version actually exists on crates.io** before editing the
  Dockerfile. Do not guess. (This caught a real mistake during the
  v1.5.0 implementation.)
- Plan before building if a fix turns out to be non-trivial.
