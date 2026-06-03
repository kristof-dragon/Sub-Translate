import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import type { MergeReport, MergeResult, SubtitleFile } from '../types'

interface Props {
  projectId: number
  /** All rows in the project — used to populate the "existing file" pickers. */
  files: SubtitleFile[]
  onCancel: () => void
  /** Called once a merged row has been created. */
  onDone: (created: MergeResult) => void
}

type SlotMode = 'file' | 'upload'

// A row is mergeable when it's a text subtitle that already has a source file on
// disk (extracted / OCR'd / translated / failed) — i.e. not mid-flight and not a
// still-bitmap track awaiting OCR.
function isMergeable(f: SubtitleFile): boolean {
  return (
    (f.format === 'srt' || f.format === 'vtt') &&
    ['extracted', 'ocr_done', 'done', 'error'].includes(f.status)
  )
}

/**
 * Overlay to stitch a "forced" (foreign-dialogue-only) subtitle and a
 * "standard"/full subtitle into a single union SRT, before translation. Each
 * slot is either an existing project file or a fresh upload. Overlapping cues
 * are combined into one cue (server-side); the overlap report is surfaced here
 * so a collision can never be merged silently.
 */
export default function MergeSubtitles({ projectId, files, onCancel, onDone }: Props) {
  const eligible = useMemo(() => files.filter(isMergeable), [files])

  const [modeA, setModeA] = useState<SlotMode>(eligible.length ? 'file' : 'upload')
  const [fileIdA, setFileIdA] = useState<number | ''>('')
  const [uploadA, setUploadA] = useState<File | null>(null)

  const [modeB, setModeB] = useState<SlotMode>(eligible.length ? 'file' : 'upload')
  const [fileIdB, setFileIdB] = useState<number | ''>('')
  const [uploadB, setUploadB] = useState<File | null>(null)

  const [outputName, setOutputName] = useState('')
  const [report, setReport] = useState<MergeReport | null>(null)
  // Per-clash, per-side exclusions, keyed by the clash's index in
  // report.combined. Empty = keep everything (combine every clash) = default.
  const [dropForced, setDropForced] = useState<Set<number>>(new Set())
  const [dropFull, setDropFull] = useState<Set<number>>(new Set())
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  // Any change to the inputs invalidates a previously-shown report (and the
  // per-clash choices made against it), so the operator can't confirm a merge
  // against a stale collision list.
  useEffect(() => {
    setReport(null)
    setDropForced(new Set())
    setDropFull(new Set())
  }, [modeA, fileIdA, uploadA, modeB, fileIdB, uploadB])

  const toggleForced = (idx: number) =>
    setDropForced((prev) => {
      const next = new Set(prev)
      if (next.has(idx)) next.delete(idx)
      else next.add(idx)
      return next
    })
  const toggleFull = (idx: number) =>
    setDropFull((prev) => {
      const next = new Set(prev)
      if (next.has(idx)) next.delete(idx)
      else next.add(idx)
      return next
    })

  const appendSlot = (
    fd: FormData,
    suffix: 'a' | 'b',
    mode: SlotMode,
    fileId: number | '',
    upload: File | null,
  ): string | null => {
    const label = suffix === 'a' ? 'forced' : 'standard'
    if (mode === 'file') {
      if (!fileId) return `Pick a file for the ${label} slot`
      fd.append(`file_id_${suffix}`, String(fileId))
    } else {
      if (!upload) return `Choose a file to upload for the ${label} slot`
      fd.append(`upload_${suffix}`, upload)
    }
    return null
  }

  // Fresh FormData per request — a body with File parts is consumed by fetch.
  const buildForm = (): FormData | null => {
    const fd = new FormData()
    const ea = appendSlot(fd, 'a', modeA, fileIdA, uploadA)
    if (ea) {
      setErr(ea)
      return null
    }
    const eb = appendSlot(fd, 'b', modeB, fileIdB, uploadB)
    if (eb) {
      setErr(eb)
      return null
    }
    return fd
  }

  const runPreview = async (): Promise<MergeReport | null> => {
    const fd = buildForm()
    if (!fd) return null
    setBusy(true)
    setErr('')
    try {
      const rep = await api.mergePreview(projectId, fd)
      setReport(rep)
      // Fresh report -> start with every clash kept (all ticked).
      setDropForced(new Set())
      setDropFull(new Set())
      if (!outputName.trim()) setOutputName(rep.default_name)
      return rep
    } catch (e: unknown) {
      setErr(String(e))
      return null
    } finally {
      setBusy(false)
    }
  }

  const handleCreate = async () => {
    // Always preview first so the overlap warning can never be skipped: the
    // first click on a not-yet-previewed merge only surfaces the report; if it's
    // clean we go straight through, otherwise the operator confirms with a
    // second click (button now reads "Create anyway").
    let rep = report
    if (!rep) {
      rep = await runPreview()
      if (!rep) return
      if (!rep.clean) return
    }
    const fd = buildForm()
    if (!fd) return
    if (outputName.trim()) fd.append('output_name', outputName.trim())
    if (dropForced.size) fd.append('drop_forced', JSON.stringify(Array.from(dropForced)))
    if (dropFull.size) fd.append('drop_full', JSON.stringify(Array.from(dropFull)))
    setBusy(true)
    setErr('')
    try {
      const created = await api.mergeCommit(projectId, fd)
      onDone(created)
    } catch (e: unknown) {
      setErr(String(e))
    } finally {
      setBusy(false)
    }
  }

  const primaryLabel = busy
    ? 'Working…'
    : report
      ? 'Create merged subtitle'
      : 'Check overlap & create'

  return (
    <div className="modal-backdrop" onClick={onCancel}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="row between" style={{ marginBottom: 12 }}>
          <h3 style={{ margin: 0 }}>Merge subtitles</h3>
          <button onClick={onCancel}>Cancel</button>
        </div>

        <div className="small muted" style={{ marginBottom: 12 }}>
          Stitch a “forced” track (foreign dialogue only) and a “standard”/full
          track into one union subtitle. Where the two overlap, you choose per
          clash which side(s) to keep. The result lands as a new SRT row —
          translate it from the file list.
        </div>

        {err && <div className="error-msg">{err}</div>}

        <Slot
          title="A — Forced (foreign dialogue only)"
          mode={modeA}
          onMode={setModeA}
          fileId={fileIdA}
          onFileId={setFileIdA}
          onUpload={setUploadA}
          uploadName={uploadA?.name ?? ''}
          eligible={eligible}
        />
        <Slot
          title="B — Standard / full"
          mode={modeB}
          onMode={setModeB}
          fileId={fileIdB}
          onFileId={setFileIdB}
          onUpload={setUploadB}
          uploadName={uploadB?.name ?? ''}
          eligible={eligible}
        />

        <div style={{ marginTop: 8 }}>
          <label>Output name</label>
          <div className="row" style={{ gap: 6, alignItems: 'center' }}>
            <input
              value={outputName}
              placeholder="(auto — fills in after Check overlap)"
              onChange={(e) => setOutputName(e.target.value)}
            />
            <span className="small muted">.srt</span>
          </div>
        </div>

        {report && (
          <ReportView
            report={report}
            dropForced={dropForced}
            dropFull={dropFull}
            onToggleForced={toggleForced}
            onToggleFull={toggleFull}
          />
        )}

        <div className="row between" style={{ marginTop: 14 }}>
          <button type="button" onClick={runPreview} disabled={busy}>
            {busy ? 'Checking…' : 'Check overlap'}
          </button>
          <button
            type="button"
            className="primary"
            onClick={handleCreate}
            disabled={busy}
          >
            {primaryLabel}
          </button>
        </div>
      </div>
    </div>
  )
}

function Slot({
  title,
  mode,
  onMode,
  fileId,
  onFileId,
  onUpload,
  uploadName,
  eligible,
}: {
  title: string
  mode: SlotMode
  onMode: (m: SlotMode) => void
  fileId: number | ''
  onFileId: (id: number | '') => void
  onUpload: (f: File | null) => void
  uploadName: string
  eligible: SubtitleFile[]
}) {
  return (
    <div className="card stack" style={{ marginBottom: 10, padding: 12 }}>
      <div style={{ fontWeight: 600 }}>{title}</div>
      <div className="row" style={{ gap: 16 }}>
        <label className="row" style={{ gap: 6, alignItems: 'center' }}>
          <input
            type="radio"
            checked={mode === 'file'}
            onChange={() => onMode('file')}
            style={{ width: 'auto' }}
          />
          Existing file
        </label>
        <label className="row" style={{ gap: 6, alignItems: 'center' }}>
          <input
            type="radio"
            checked={mode === 'upload'}
            onChange={() => onMode('upload')}
            style={{ width: 'auto' }}
          />
          Upload
        </label>
      </div>

      {mode === 'file' ? (
        eligible.length === 0 ? (
          <div className="small muted">
            No mergeable subtitles in this project yet — switch to Upload, or
            extract/upload the tracks first.
          </div>
        ) : (
          <select
            value={fileId === '' ? '' : String(fileId)}
            onChange={(e) => onFileId(e.target.value ? Number(e.target.value) : '')}
          >
            <option value="">Select…</option>
            {eligible.map((f) => (
              <option key={f.id} value={f.id}>
                {f.original_filename} ({f.status})
              </option>
            ))}
          </select>
        )
      ) : (
        <div>
          <input
            type="file"
            accept=".srt,.vtt"
            onChange={(e) => onUpload(e.target.files?.[0] ?? null)}
          />
          {uploadName && <div className="small muted">{uploadName}</div>}
        </div>
      )}
    </div>
  )
}

function ReportView({
  report,
  dropForced,
  dropFull,
  onToggleForced,
  onToggleFull,
}: {
  report: MergeReport
  dropForced: Set<number>
  dropFull: Set<number>
  onToggleForced: (idx: number) => void
  onToggleFull: (idx: number) => void
}) {
  return (
    <div style={{ marginTop: 12 }}>
      <div className="small muted">
        Forced: {report.forced_cues} cues · Standard: {report.full_cues} cues →{' '}
        {report.result_cues} cues
      </div>

      {report.clean ? (
        <div className="small" style={{ color: 'var(--success)', marginTop: 6 }}>
          ✓ No overlaps — clean union.
        </div>
      ) : (
        <div className="error-msg" style={{ marginTop: 6 }}>
          ⚠ The two files overlap in {report.overlap_count} place
          {report.overlap_count === 1 ? '' : 's'}. For each clash, tick the
          side(s) to keep — both ticked are combined into one cue; one side kept
          stays as-is; none drops the clash.
          {report.long_combined > 0 && (
            <>
              {' '}
              {report.long_combined} clash
              {report.long_combined === 1 ? '' : 'es'} span an unusually wide
              range — possible over-merge, check below.
            </>
          )}
        </div>
      )}

      {report.combined.length > 0 && (
        <div
          style={{
            marginTop: 8,
            maxHeight: 240,
            overflowY: 'auto',
            border: '1px solid var(--border, #ddd)',
            borderRadius: 6,
          }}
        >
          {report.combined.map((c, i) => {
            const forced = c.members.filter((m) => m.track === 'forced')
            const full = c.members.filter((m) => m.track === 'full')
            const keepForced = !dropForced.has(i)
            const keepFull = !dropFull.has(i)
            const survives =
              (keepForced && forced.length > 0) || (keepFull && full.length > 0)
            return (
              <div
                key={i}
                style={{
                  padding: '6px 8px',
                  borderBottom: '1px solid var(--border, #eee)',
                }}
              >
                <div className="small muted">
                  Clash {i + 1}: {c.start} → {c.end}
                  {c.long ? ' · wide' : ''}
                </div>
                {forced.length > 0 && (
                  <ClashSide
                    label="Forced"
                    color="#b45309"
                    texts={forced.map((m) => m.text)}
                    checked={keepForced}
                    onToggle={() => onToggleForced(i)}
                  />
                )}
                {full.length > 0 && (
                  <ClashSide
                    label="Full"
                    color="#1d4ed8"
                    texts={full.map((m) => m.text)}
                    checked={keepFull}
                    onToggle={() => onToggleFull(i)}
                  />
                )}
                {!survives && (
                  <div className="small" style={{ color: 'var(--error)' }}>
                    This clash will be dropped entirely.
                  </div>
                )}
              </div>
            )
          })}
          {report.truncated > 0 && (
            <div className="small muted" style={{ padding: '6px 8px' }}>
              + {report.truncated} more clash{report.truncated === 1 ? '' : 'es'} not
              shown (kept as-is / combined)
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function ClashSide({
  label,
  color,
  texts,
  checked,
  onToggle,
}: {
  label: string
  color: string
  texts: string[]
  checked: boolean
  onToggle: () => void
}) {
  return (
    <label
      className="row"
      style={{ gap: 6, alignItems: 'flex-start', marginTop: 4, opacity: checked ? 1 : 0.5 }}
    >
      <input
        type="checkbox"
        checked={checked}
        onChange={onToggle}
        style={{ width: 'auto', marginTop: 3 }}
      />
      <span className="small" style={{ whiteSpace: 'pre-wrap' }}>
        <b style={{ color }}>{label}</b>{' '}
        <span style={{ textDecoration: checked ? 'none' : 'line-through' }}>
          {texts.join('\n')}
        </span>
      </span>
    </label>
  )
}
