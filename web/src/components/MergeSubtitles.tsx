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
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  // Any change to the inputs invalidates a previously-shown overlap report, so
  // the operator can't confirm a merge against a stale collision count.
  useEffect(() => {
    setReport(null)
  }, [modeA, fileIdA, uploadA, modeB, fileIdB, uploadB])

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
      ? report.clean
        ? 'Create merged subtitle'
        : `Create anyway — combine ${report.overlap_count} overlap${
            report.overlap_count === 1 ? '' : 's'
          }`
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
          track into one union subtitle. Overlapping cues are combined. The
          result lands as a new SRT row — translate it from the file list.
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

        {report && <ReportView report={report} />}

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

function ReportView({ report }: { report: MergeReport }) {
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
          {report.overlap_count === 1 ? '' : 's'}. Each overlap will be combined
          into a single cue.
          {report.long_combined > 0 && (
            <>
              {' '}
              {report.long_combined} of them span an unusually wide range — check
              for an over-merge in the list below.
            </>
          )}
        </div>
      )}

      {report.combined.length > 0 && (
        <div
          style={{
            marginTop: 8,
            maxHeight: 220,
            overflowY: 'auto',
            border: '1px solid var(--border, #ddd)',
            borderRadius: 6,
          }}
        >
          {report.combined.map((c, i) => (
            <div
              key={i}
              style={{ padding: '6px 8px', borderBottom: '1px solid var(--border, #eee)' }}
            >
              <div className="small muted">
                {c.start} → {c.end}
                {c.count > 2 ? ` · ${c.count} cues` : ''}
                {c.long ? ' · wide' : ''}
              </div>
              <div className="small" style={{ whiteSpace: 'pre-wrap' }}>
                {c.texts.join('\n')}
              </div>
            </div>
          ))}
          {report.truncated > 0 && (
            <div className="small muted" style={{ padding: '6px 8px' }}>
              + {report.truncated} more overlap{report.truncated === 1 ? '' : 's'} not shown
            </div>
          )}
        </div>
      )}
    </div>
  )
}
