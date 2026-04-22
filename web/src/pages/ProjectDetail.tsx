import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api'
import VideoBrowser from '../components/VideoBrowser'
import FolderPicker from '../components/FolderPicker'
import UploadDropzone from '../components/UploadDropzone'
import type {
  AppSettings,
  EventMessage,
  ExportResult,
  Language,
  OllamaModel,
  Project,
  SubtitleFile,
} from '../types'

// The export flow is a small state machine rather than a pile of booleans:
//   idle     → nothing in progress
//   picking  → FolderPicker is open because uploaded files need a target
//   summary  → results dialog is open showing written/skipped items
type ExportPhase =
  | { kind: 'idle' }
  | {
      kind: 'picking'
      uploadedIds: number[]
      carriedResult: ExportResult
    }
  | { kind: 'summary'; result: ExportResult }

const EMPTY_RESULT: ExportResult = { written: [], skipped: [] }

export default function ProjectDetail() {
  const { id } = useParams()
  const projectId = Number(id)

  const [project, setProject] = useState<Project | null>(null)
  const [files, setFiles] = useState<SubtitleFile[]>([])
  const [languages, setLanguages] = useState<Language[]>([])
  const [models, setModels] = useState<OllamaModel[]>([])
  const [settings, setSettings] = useState<AppSettings | null>(null)

  const [targetLang, setTargetLang] = useState('')
  const [model, setModel] = useState('')
  const [err, setErr] = useState('')
  const [uploading, setUploading] = useState(false)
  const [videoOpen, setVideoOpen] = useState(false)
  const [savingDefaults, setSavingDefaults] = useState(false)
  const [savedDefaults, setSavedDefaults] = useState(false)

  // Bulk-export state: file ids ticked for export + the active phase.
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())
  const [exportPhase, setExportPhase] = useState<ExportPhase>({ kind: 'idle' })
  const [exporting, setExporting] = useState(false)

  // Load project + its files + surrounding context in parallel.
  const reload = useCallback(async () => {
    try {
      const [p, fs] = await Promise.all([
        api.getProject(projectId),
        api.listFiles(projectId),
      ])
      setProject(p)
      setFiles(fs)
      if (!targetLang) setTargetLang(p.default_target_lang || '')
      if (!model) setModel(p.default_model || '')
    } catch (e: unknown) {
      setErr(String(e))
    }
  }, [projectId, targetLang, model])

  useEffect(() => {
    reload()
    api.listLanguages().then(setLanguages).catch(() => {})
    api.listModels().then((r) => setModels(r.models)).catch(() => setModels([]))
    api.getSettings().then(setSettings).catch(() => {})
  }, [reload])

  // SSE subscription — updates progress rows live without polling.
  useEffect(() => {
    const es = new EventSource('/api/events')
    es.onmessage = (ev) => {
      try {
        const data: EventMessage = JSON.parse(ev.data)
        setFiles((rows) =>
          rows.map((r) => {
            if (r.id !== data.file_id) return r
            const next: SubtitleFile = {
              ...r,
              status: data.status ?? r.status,
              progress_pct:
                data.progress_pct !== undefined ? data.progress_pct : r.progress_pct,
              ocr_progress_pct:
                data.ocr_progress_pct !== undefined
                  ? data.ocr_progress_pct
                  : r.ocr_progress_pct,
              detected_lang: data.detected_lang ?? r.detected_lang,
              error: data.error ?? r.error,
              format: data.format ?? r.format,
            }
            if (data.status === 'done') {
              next.translated_available = true
            }
            return next
          }),
        )
      } catch {
        /* ignore malformed */
      }
    }
    es.onerror = () => {
      // EventSource auto-reconnects; nothing to do.
    }
    return () => es.close()
  }, [])

  const effectiveModel = useMemo(
    () => model || project?.default_model || settings?.default_model || '',
    [model, project, settings],
  )

  const handleUpload = async (picked: File[]) => {
    if (!targetLang) {
      setErr('Pick a target language first')
      return
    }
    if (!effectiveModel) {
      setErr('No model available — configure one in Settings')
      return
    }
    setErr('')
    setUploading(true)
    try {
      await api.uploadFiles(projectId, picked, targetLang, model || undefined)
      await reload()
    } catch (e: unknown) {
      setErr(String(e))
    } finally {
      setUploading(false)
    }
  }

  const handleDelete = async (fid: number) => {
    if (!confirm('Delete this file?')) return
    await api.deleteFile(fid)
    setFiles((rows) => rows.filter((r) => r.id !== fid))
  }

  const handleRename = async (fid: number, stem: string) => {
    const updated = await api.renameFile(fid, stem)
    setFiles((rows) => rows.map((r) => (r.id === fid ? updated : r)))
  }

  const toggleSelected = (fid: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(fid)) next.delete(fid)
      else next.add(fid)
      return next
    })
  }

  // Ids of every row that *could* be ticked — i.e. translation done and the
  // translated file is actually on disk. Used for the master-checkbox state
  // and for the select-all toggle.
  const exportableIds = useMemo(
    () =>
      files
        .filter((f) => f.status === 'done' && f.translated_available)
        .map((f) => f.id),
    [files],
  )

  const exportableCount = useMemo(
    () => exportableIds.filter((id) => selectedIds.has(id)).length,
    [exportableIds, selectedIds],
  )

  // Tri-state master-checkbox indicator for the file-list header:
  //   'none'  → nothing selectable is ticked
  //   'some'  → a subset is ticked (renders as indeterminate)
  //   'all'   → every exportable row is ticked
  const masterState: 'none' | 'some' | 'all' = useMemo(() => {
    if (exportableIds.length === 0) return 'none'
    if (exportableCount === 0) return 'none'
    if (exportableCount === exportableIds.length) return 'all'
    return 'some'
  }, [exportableCount, exportableIds.length])

  const toggleSelectAll = () => {
    // Clicking the master toggle in any "partial" state promotes to "all"
    // (matches how most table UIs behave — one click selects everything);
    // clicking when everything is already ticked clears the selection.
    if (masterState === 'all') {
      setSelectedIds(new Set())
    } else {
      setSelectedIds(new Set(exportableIds))
    }
  }

  const mergeResults = (a: ExportResult, b: ExportResult): ExportResult => ({
    written: [...a.written, ...b.written],
    skipped: [...a.skipped, ...b.skipped],
  })

  const handleExport = async () => {
    // Filter the selection to files that could actually be exported so we
    // don't waste a round-trip on a status-is-queued row.
    const candidates = files.filter(
      (f) =>
        selectedIds.has(f.id) && f.status === 'done' && f.translated_available,
    )
    if (candidates.length === 0) return

    const extractedIds = candidates
      .filter((f) => f.source_video_path)
      .map((f) => f.id)
    const uploadedIds = candidates
      .filter((f) => !f.source_video_path)
      .map((f) => f.id)

    setErr('')
    setExporting(true)
    try {
      let carried: ExportResult = EMPTY_RESULT

      // Extracted-from-video files go back next to each source automatically
      // (target=null). Do that first so the folder picker only runs if
      // actually necessary.
      if (extractedIds.length > 0) {
        const r = await api.exportFiles(projectId, { file_ids: extractedIds })
        carried = mergeResults(carried, r)
      }

      if (uploadedIds.length > 0) {
        // Hand off to the FolderPicker — the finish handler below will post
        // the second batch with the picked target and show the combined summary.
        setExportPhase({
          kind: 'picking',
          uploadedIds,
          carriedResult: carried,
        })
      } else {
        setExportPhase({ kind: 'summary', result: carried })
        setSelectedIds(new Set())
      }
    } catch (e: unknown) {
      setErr(String(e))
    } finally {
      setExporting(false)
    }
  }

  const finishExportWithFolder = async (target: string) => {
    if (exportPhase.kind !== 'picking') return
    const { uploadedIds, carriedResult } = exportPhase
    setExporting(true)
    setErr('')
    try {
      const r = await api.exportFiles(projectId, {
        file_ids: uploadedIds,
        target,
      })
      setExportPhase({
        kind: 'summary',
        result: mergeResults(carriedResult, r),
      })
      setSelectedIds(new Set())
    } catch (e: unknown) {
      setErr(String(e))
      setExportPhase({ kind: 'idle' })
    } finally {
      setExporting(false)
    }
  }

  const cancelExportPicker = () => {
    // If the extracted-files pass already wrote something, keep that in the
    // summary so the operator knows it wasn't a no-op.
    if (exportPhase.kind === 'picking') {
      const result = exportPhase.carriedResult
      if (result.written.length > 0 || result.skipped.length > 0) {
        setExportPhase({ kind: 'summary', result })
      } else {
        setExportPhase({ kind: 'idle' })
      }
    }
  }

  const handleRetryOcr = async (fid: number) => {
    setErr('')
    try {
      const updated = await api.retryOcr(fid)
      setFiles((rows) => rows.map((r) => (r.id === fid ? updated : r)))
    } catch (e: unknown) {
      setErr(String(e))
    }
  }

  const handleSaveDefaults = async () => {
    setSavingDefaults(true)
    setSavedDefaults(false)
    try {
      const updated = await api.updateProject(projectId, {
        default_target_lang: targetLang,
        default_model: model,
      })
      setProject(updated)
      setSavedDefaults(true)
      setTimeout(() => setSavedDefaults(false), 2000)
    } catch (e: unknown) {
      setErr(String(e))
    } finally {
      setSavingDefaults(false)
    }
  }

  const handleTranslate = async (fid: number) => {
    if (!targetLang) {
      setErr('Pick a target language at the top of the page first')
      return
    }
    if (!effectiveModel) {
      setErr('No model available — configure one in Settings')
      return
    }
    setErr('')
    try {
      const updated = await api.translateFile(fid, {
        target_lang: targetLang,
        model: model || undefined,
      })
      setFiles((rows) => rows.map((r) => (r.id === fid ? updated : r)))
    } catch (e: unknown) {
      setErr(String(e))
    }
  }

  if (!project) {
    return <div className="empty">{err || 'Loading…'}</div>
  }

  return (
    <div className="stack">
      <div>
        <Link to="/">&larr; All projects</Link>
      </div>

      <div className="row between">
        <h2 style={{ margin: 0 }}>{project.name}</h2>
      </div>
      {project.description && <div className="muted">{project.description}</div>}

      {err && <div className="error-msg">{err}</div>}

      <div className="card stack">
        <div className="form-grid">
          <div>
            <label>Target language *</label>
            <select value={targetLang} onChange={(e) => setTargetLang(e.target.value)}>
              <option value="">Select…</option>
              {languages.map((l) => (
                <option key={l.code} value={l.code}>{l.name}</option>
              ))}
            </select>
          </div>
          <div>
            <label>Model {effectiveModel ? `(current: ${effectiveModel})` : ''}</label>
            <select value={model} onChange={(e) => setModel(e.target.value)}>
              <option value="">(use project / global default)</option>
              {models.map((m) => (
                <option key={m.name} value={m.name}>{m.name}</option>
              ))}
            </select>
          </div>
        </div>
        <div className="row" style={{ gap: 8 }}>
          <button
            type="button"
            className="small primary"
            onClick={handleSaveDefaults}
            disabled={savingDefaults || !targetLang}
            title="Save selected language and model as this project's defaults"
          >
            {savingDefaults ? 'Saving…' : 'Save as defaults'}
          </button>
          {savedDefaults && (
            <span className="small" style={{ color: 'var(--success)' }}>Saved</span>
          )}
        </div>
        <UploadDropzone onFiles={handleUpload} disabled={!targetLang || uploading} />

        <div className="row" style={{ marginTop: 8 }}>
          <button onClick={() => setVideoOpen(true)}>Add from video…</button>
          <span className="small muted">
            Browse the bind-mounted media folder (MKV, MP4, WebM, …) and extract
            subtitle tracks. Translate them afterwards from the file list.
          </span>
        </div>
      </div>

      {videoOpen && (
        <VideoBrowser
          onCancel={() => {
            setVideoOpen(false)
            // Pick up any rows the SSE stream missed while the modal was open
            // (e.g. race on first render).
            reload()
          }}
          onExtract={async (body) => {
            await api.extractTracks(projectId, body)
            // Don't close the modal — the extraction is now running on the
            // server-side queue and the operator may want to queue more videos.
            // SSE will update the file list live as extractions finish.
            await reload()
          }}
        />
      )}

      {files.length === 0 ? (
        <div className="empty">No files uploaded yet.</div>
      ) : (
        <div className="card file-list-card">
          <div className="row between file-list-toolbar">
            <div className="file-list-toolbar-left">
              {/* Mobile-only select-all — the desktop master checkbox lives
                  in the hidden-on-mobile .file-list-head row. */}
              <button
                type="button"
                className="small file-list-select-all-mobile"
                onClick={toggleSelectAll}
                disabled={exportableIds.length === 0}
              >
                {masterState === 'all'
                  ? 'Clear selection'
                  : `Select all${
                      exportableIds.length > 0 ? ` (${exportableIds.length})` : ''
                    }`}
              </button>
              <div className="small muted file-list-toolbar-hint">
                Tick files to export. Extracted subtitles go next to the
                source video; uploaded ones prompt for a folder.
              </div>
            </div>
            <button
              type="button"
              className="primary"
              onClick={handleExport}
              disabled={exportableCount === 0 || exporting}
            >
              {exporting
                ? 'Exporting…'
                : `Export selected${
                    exportableCount > 0 ? ` (${exportableCount})` : ''
                  }`}
            </button>
          </div>
          <div className="file-list">
            <div className="file-list-head">
              <div className="file-row-checkbox">
                <MasterCheckbox
                  state={masterState}
                  onToggle={toggleSelectAll}
                  disabled={exportableIds.length === 0}
                />
              </div>
              <span>File</span>
              <span>Status</span>
              <span>Detected</span>
              <span>Target</span>
              <span>Progress</span>
              <span />
            </div>
            {files.map((f) => (
              <FileRow
                key={f.id}
                f={f}
                selected={selectedIds.has(f.id)}
                onToggleSelected={toggleSelected}
                onDelete={handleDelete}
                onTranslate={handleTranslate}
                onRetryOcr={handleRetryOcr}
                onRename={handleRename}
              />
            ))}
          </div>
        </div>
      )}

      {exportPhase.kind === 'picking' && (
        <FolderPicker
          title="Save uploaded subtitles to…"
          hint="Extracted subtitles are going back next to their source video automatically."
          onPick={finishExportWithFolder}
          onCancel={cancelExportPicker}
        />
      )}

      {exportPhase.kind === 'summary' && (
        <ExportSummaryModal
          result={exportPhase.result}
          onClose={() => setExportPhase({ kind: 'idle' })}
        />
      )}
    </div>
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
            <div className="small" style={{ fontWeight: 600, marginBottom: 4 }}>
              Written
            </div>
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
            <div className="small" style={{ fontWeight: 600, marginBottom: 4 }}>
              Skipped
            </div>
            <ul className="export-summary-list">
              {skipped.map((s) => (
                <li key={s.file_id}>
                  <div>{s.name}</div>
                  <div className="small muted">
                    /media/{s.path}
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

// Tri-state checkbox for the file-list header. React has no `indeterminate`
// prop, so we set the DOM property via ref whenever state changes.
function MasterCheckbox({
  state,
  onToggle,
  disabled,
}: {
  state: 'none' | 'some' | 'all'
  onToggle: () => void
  disabled?: boolean
}) {
  const ref = useRef<HTMLInputElement>(null)
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = state === 'some'
  }, [state])
  return (
    <input
      ref={ref}
      type="checkbox"
      checked={state === 'all'}
      onChange={onToggle}
      disabled={disabled}
      aria-label={
        state === 'all' ? 'Deselect all files' : 'Select all ready files'
      }
      title={
        state === 'all' ? 'Deselect all' : 'Select all ready for export'
      }
    />
  )
}

function FileRow({
  f,
  selected,
  onToggleSelected,
  onDelete,
  onTranslate,
  onRetryOcr,
  onRename,
}: {
  f: SubtitleFile
  selected: boolean
  onToggleSelected: (id: number) => void
  onDelete: (id: number) => void
  onTranslate: (id: number) => void
  onRetryOcr: (id: number) => void
  onRename: (id: number, stem: string) => Promise<void>
}) {
  const barRef = useRef<HTMLDivElement>(null)
  // While the row is in the OCR pipeline the "progress" we want to show is
  // the OCR pass (0–100); once it lands at ocr_done / queued / translating /
  // done the translation progress takes over again.
  const ocrPhase = ['ocr_queued', 'ocr_running', 'ocr_error'].includes(f.status)
  const shownPct = ocrPhase ? f.ocr_progress_pct : f.progress_pct
  useEffect(() => {
    if (barRef.current) barRef.current.style.width = `${shownPct}%`
  }, [shownPct])

  // Translate button is offered on any row that isn't currently mid-flight —
  // covers first-run (extracted/ocr_done), retry (error), and re-translate (done).
  const canTranslate = ['extracted', 'ocr_done', 'done', 'error'].includes(f.status)
  const canRetryOcr = f.status === 'ocr_error'

  // `.{target_lang}.{format}` is the suffix the server preserves on rename —
  // we show it as a read-only tail next to the input so the operator knows
  // which part of the filename they can actually change.
  const suffix = `.${f.target_lang}.${f.format}`
  const displayName = f.translated_filename || f.original_filename
  const currentStem = f.translated_filename.endsWith(suffix)
    ? f.translated_filename.slice(0, -suffix.length)
    : displayName

  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(currentStem)
  const [saving, setSaving] = useState(false)
  const [renameErr, setRenameErr] = useState('')

  const canRename = f.status === 'done' && !!f.translated_filename
  const exportable = f.status === 'done' && f.translated_available

  const startEditing = () => {
    setDraft(currentStem)
    setRenameErr('')
    setEditing(true)
  }

  const cancelEditing = () => {
    setEditing(false)
    setRenameErr('')
  }

  const saveRename = async () => {
    const trimmed = draft.trim()
    if (!trimmed) {
      setRenameErr('Name cannot be empty')
      return
    }
    if (trimmed === currentStem) {
      cancelEditing()
      return
    }
    setSaving(true)
    setRenameErr('')
    try {
      await onRename(f.id, trimmed)
      setEditing(false)
    } catch (e: unknown) {
      setRenameErr(String(e))
    } finally {
      setSaving(false)
    }
  }

  // The DOM is grouped into .file-row-top / .file-row-meta / .file-row-actions
  // so that narrow screens can stack into three readable rows. On desktop
  // those wrappers use `display: contents` (see index.css) so their children
  // participate directly in the parent grid and the layout matches the old
  // table column-for-column.
  return (
    <div className="file-row">
      <div className="file-row-top">
        <div className="file-row-checkbox">
          <input
            type="checkbox"
            checked={selected}
            disabled={!exportable}
            onChange={() => onToggleSelected(f.id)}
            aria-label={
              exportable
                ? `Select ${displayName} for export`
                : 'Not ready for export'
            }
            title={
              exportable
                ? 'Include in bulk export'
                : 'Only done, translated files can be exported'
            }
          />
        </div>
        <div className="file-row-name">
          {editing ? (
            <div className="file-row-rename">
              <div className="file-row-rename-input">
                <input
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') void saveRename()
                    if (e.key === 'Escape') cancelEditing()
                  }}
                  autoFocus
                  disabled={saving}
                  aria-label="New filename (without extension)"
                />
                <span className="file-row-rename-suffix">{suffix}</span>
              </div>
              <div className="row" style={{ gap: 6 }}>
                <button
                  type="button"
                  className="small primary"
                  onClick={saveRename}
                  disabled={saving}
                >
                  {saving ? 'Saving…' : 'Save'}
                </button>
                <button
                  type="button"
                  className="small"
                  onClick={cancelEditing}
                  disabled={saving}
                >
                  Cancel
                </button>
              </div>
              {renameErr && (
                <div className="small" style={{ color: 'var(--error)' }}>{renameErr}</div>
              )}
            </div>
          ) : (
            <div className="file-row-name-line">
              <span>{displayName}</span>
              {canRename && (
                <button
                  type="button"
                  className="icon-button"
                  onClick={startEditing}
                  title="Rename translated file"
                  aria-label="Rename translated file"
                >
                  &#9998;
                </button>
              )}
            </div>
          )}
          {f.error && (
            <div className="small" style={{ color: 'var(--error)' }}>{f.error}</div>
          )}
        </div>
        <div className="file-row-status">
          <span className={`badge ${f.status}`}>{f.status}</span>
        </div>
      </div>

      <div className="file-row-meta">
        <div className="file-row-detected small muted">
          <span className="file-row-label">Detected:</span> {f.detected_lang || '—'}
        </div>
        <div className="file-row-target small">
          <span className="file-row-label">Target:</span> {f.target_lang || '—'}
        </div>
        <div className="file-row-progress">
          <div className="progress"><div ref={barRef} /></div>
          <div className="small muted">
            {ocrPhase ? `OCR ${shownPct}%` : `${shownPct}%`}
          </div>
        </div>
      </div>

      <div className="file-row-actions">
        {canTranslate && (
          <button
            className="small"
            onClick={() => onTranslate(f.id)}
            title="Queue this file for translation using the target language selected at the top of the page"
          >
            Translate
          </button>
        )}
        {canRetryOcr && (
          <button
            className="small"
            onClick={() => onRetryOcr(f.id)}
            title="Re-run OCR on the extracted PGS file"
          >
            Retry OCR
          </button>
        )}
        {f.translated_available && (
          <a
            href={api.downloadUrl(f.id)}
            download
            className="small button-like"
          >
            Download
          </a>
        )}
        <button className="danger small" onClick={() => onDelete(f.id)}>Delete</button>
      </div>
    </div>
  )
}
