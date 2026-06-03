import { useMemo, useState } from 'react'
import { api } from '../api'
import FolderPicker from './FolderPicker'
import type {
  ExportRequestItem,
  ExportResult,
  ExportVersion,
  SubtitleFile,
} from '../types'

interface Props {
  projectId: number
  files: SubtitleFile[]
  onClose: () => void
}

type Dest = 'video' | 'download' | 'folder'

interface RowChoice {
  include: boolean
  version: ExportVersion
}

// A finished translation is exportable.
function hasTranslated(f: SubtitleFile): boolean {
  return f.status === 'done' && f.translated_available
}
// The source subtitle is exportable once it's a text file on disk — i.e. not a
// still-bitmap track mid-OCR (format stays sup/sub until ocr_done) and not an
// in-flight extraction (no file written yet).
function hasSource(f: SubtitleFile): boolean {
  return (f.format === 'srt' || f.format === 'vtt') && f.status !== 'extracting'
}
// The source file's label: a merged row's source is the "Unified" subtitle.
function sourceLabel(f: SubtitleFile): string {
  return f.source_format === 'merged' ? 'Unified' : 'Original'
}

/**
 * Project-level export overlay (no row pre-selection). Lists every subtitle with
 * something to export; per file you pick the version (Original/Unified/Translated)
 * and one destination: next to the source video, Download, or a chosen /media
 * folder.
 */
export default function ExportSubtitles({ projectId, files, onClose }: Props) {
  const rows = useMemo(
    () => files.filter((f) => hasSource(f) || hasTranslated(f)),
    [files],
  )

  const [choices, setChoices] = useState<Record<number, RowChoice>>(() => {
    const init: Record<number, RowChoice> = {}
    for (const f of rows) {
      init[f.id] = { include: true, version: hasTranslated(f) ? 'translated' : 'source' }
    }
    return init
  })
  const [dest, setDest] = useState<Dest>('video')
  const [pickingFolder, setPickingFolder] = useState(false)
  const [summary, setSummary] = useState<ExportResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const setChoice = (id: number, patch: Partial<RowChoice>) =>
    setChoices((prev) => ({ ...prev, [id]: { ...prev[id], ...patch } }))

  const selected = rows.filter((f) => choices[f.id]?.include)
  const buildItems = (): ExportRequestItem[] =>
    selected.map((f) => ({ file_id: f.id, version: choices[f.id].version }))

  const downloadSelected = () => {
    for (const f of selected) {
      const url =
        choices[f.id].version === 'translated'
          ? api.downloadUrl(f.id)
          : api.originalDownloadUrl(f.id)
      const a = document.createElement('a')
      a.href = url
      a.download = ''
      document.body.appendChild(a)
      a.click()
      a.remove()
    }
    onClose()
  }

  const exportToServer = async (target?: string) => {
    setBusy(true)
    setErr('')
    try {
      setSummary(await api.exportFiles(projectId, { items: buildItems(), target }))
    } catch (e: unknown) {
      setErr(String(e))
    } finally {
      setBusy(false)
    }
  }

  const handleExport = async () => {
    if (selected.length === 0) {
      setErr('Pick at least one subtitle to export')
      return
    }
    setErr('')
    if (dest === 'download') return downloadSelected()
    if (dest === 'folder') return setPickingFolder(true)
    await exportToServer(undefined) // next to source video
  }

  const exportLabel = busy
    ? 'Exporting…'
    : dest === 'download'
      ? `Download (${selected.length})`
      : dest === 'folder'
        ? `Choose folder… (${selected.length})`
        : `Export to video folder (${selected.length})`

  // Folder-picker sub-step.
  if (pickingFolder) {
    return (
      <FolderPicker
        title="Export subtitles to…"
        hint="Files that already exist at the destination are skipped, not overwritten."
        onPick={(target) => {
          setPickingFolder(false)
          void exportToServer(target)
        }}
        onCancel={() => setPickingFolder(false)}
      />
    )
  }

  // Results summary (after a server export).
  if (summary) {
    return <ExportSummaryModal result={summary} onClose={onClose} />
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="row between" style={{ marginBottom: 12 }}>
          <h3 style={{ margin: 0 }}>Export subtitles</h3>
          <button onClick={onClose}>Cancel</button>
        </div>

        {err && <div className="error-msg">{err}</div>}

        {rows.length === 0 ? (
          <div className="empty">Nothing to export yet.</div>
        ) : (
          <>
            <div className="small muted" style={{ marginBottom: 6 }}>
              Tick the subtitles to export and pick a version for each.
            </div>
            <div
              style={{
                maxHeight: 280,
                overflowY: 'auto',
                border: '1px solid var(--border, #ddd)',
                borderRadius: 6,
                marginBottom: 12,
              }}
            >
              {rows.map((f) => {
                const c = choices[f.id]
                const srcOk = hasSource(f)
                const transOk = hasTranslated(f)
                return (
                  <div
                    key={f.id}
                    style={{
                      padding: '6px 8px',
                      borderBottom: '1px solid var(--border, #eee)',
                      opacity: c.include ? 1 : 0.55,
                    }}
                  >
                    <label className="row" style={{ gap: 8, alignItems: 'center' }}>
                      <input
                        type="checkbox"
                        checked={c.include}
                        onChange={() => setChoice(f.id, { include: !c.include })}
                        style={{ width: 'auto' }}
                      />
                      <span style={{ overflowWrap: 'anywhere' }}>{f.original_filename}</span>
                    </label>
                    <div className="row" style={{ gap: 14, marginLeft: 26, marginTop: 2 }}>
                      {srcOk && transOk ? (
                        <>
                          <VersionRadio
                            label={sourceLabel(f)}
                            checked={c.version === 'source'}
                            onPick={() => setChoice(f.id, { version: 'source' })}
                          />
                          <VersionRadio
                            label="Translated"
                            checked={c.version === 'translated'}
                            onPick={() => setChoice(f.id, { version: 'translated' })}
                          />
                        </>
                      ) : (
                        <span className="small muted">
                          {transOk ? 'Translated' : sourceLabel(f)}
                        </span>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>

            <div style={{ marginBottom: 6, fontWeight: 600 }}>Destination</div>
            <div className="stack" style={{ gap: 4, marginBottom: 12 }}>
              <DestRadio
                label="Next to the source video"
                hint="Only for subtitles extracted from a video; others are skipped."
                checked={dest === 'video'}
                onPick={() => setDest('video')}
              />
              <DestRadio
                label="Download"
                hint="Save to your browser's downloads."
                checked={dest === 'download'}
                onPick={() => setDest('download')}
              />
              <DestRadio
                label="Choose a folder…"
                hint="Browse the media folder and pick a destination."
                checked={dest === 'folder'}
                onPick={() => setDest('folder')}
              />
            </div>

            <div className="row between">
              <span className="small muted">
                {selected.length} selected · existing files are skipped, not overwritten
              </span>
              <button
                type="button"
                className="primary"
                onClick={handleExport}
                disabled={busy || selected.length === 0}
              >
                {exportLabel}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

function VersionRadio({
  label,
  checked,
  onPick,
}: {
  label: string
  checked: boolean
  onPick: () => void
}) {
  return (
    <label className="row small" style={{ gap: 4, alignItems: 'center' }}>
      <input type="radio" checked={checked} onChange={onPick} style={{ width: 'auto' }} />
      {label}
    </label>
  )
}

function DestRadio({
  label,
  hint,
  checked,
  onPick,
}: {
  label: string
  hint: string
  checked: boolean
  onPick: () => void
}) {
  return (
    <label className="row" style={{ gap: 8, alignItems: 'flex-start' }}>
      <input
        type="radio"
        checked={checked}
        onChange={onPick}
        style={{ width: 'auto', marginTop: 3 }}
      />
      <span>
        {label}
        <span className="small muted" style={{ display: 'block' }}>{hint}</span>
      </span>
    </label>
  )
}

function ExportSummaryModal({
  result,
  onClose,
}: {
  result: ExportResult
  onClose: () => void
}) {
  const { written, skipped } = result
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="row between" style={{ marginBottom: 12 }}>
          <h3 style={{ margin: 0 }}>Export summary</h3>
          <button onClick={onClose}>Close</button>
        </div>

        <div className="small muted" style={{ marginBottom: 8 }}>
          {written.length} written · {skipped.length} skipped
        </div>

        {written.length > 0 && (
          <div style={{ marginBottom: 12 }}>
            <div className="small" style={{ fontWeight: 600, marginBottom: 4 }}>Written</div>
            <ul className="export-summary-list">
              {written.map((w) => (
                <li key={w.file_id}>
                  <div>{w.name}</div>
                  <div className="small muted">/media/{w.path}</div>
                </li>
              ))}
            </ul>
          </div>
        )}

        {skipped.length > 0 && (
          <div>
            <div className="small" style={{ fontWeight: 600, marginBottom: 4 }}>Skipped</div>
            <ul className="export-summary-list">
              {skipped.map((s) => (
                <li key={s.file_id}>
                  <div>{s.name}</div>
                  <div className="small muted">
                    {s.path ? `/media/${s.path}` : ''}
                    {s.reason ? ` — ${s.reason}` : ''}
                  </div>
                </li>
              ))}
            </ul>
          </div>
        )}

        {written.length === 0 && skipped.length === 0 && (
          <div className="empty">Nothing was exported.</div>
        )}
      </div>
    </div>
  )
}
