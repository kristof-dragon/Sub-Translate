import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api'
import VideoBrowser from '../components/VideoBrowser'
import UploadDropzone from '../components/UploadDropzone'
import MergeSubtitles from '../components/MergeSubtitles'
import ExportSubtitles from '../components/ExportSubtitles'
import type {
  AppSettings,
  EventMessage,
  Language,
  MergeResult,
  OllamaModel,
  Project,
  SubtitleFile,
} from '../types'

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
  const [mergeOpen, setMergeOpen] = useState(false)
  const [mergeNotice, setMergeNotice] = useState('')
  const [savingDefaults, setSavingDefaults] = useState(false)
  const [savedDefaults, setSavedDefaults] = useState(false)

  // Inline project-name editor (header pencil), mirroring FileRow's rename UI.
  const [editingName, setEditingName] = useState(false)
  const [nameDraft, setNameDraft] = useState('')
  const [savingName, setSavingName] = useState(false)
  const [nameErr, setNameErr] = useState('')

  // Interactive export overlay (replaces the old tick-box bulk export).
  const [exportOpen, setExportOpen] = useState(false)

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

  const handleMergeDone = (created: MergeResult) => {
    setMergeOpen(false)
    setErr('')
    setMergeNotice(
      `Merged into ${created.original_filename} — ${created.result_cues} cues` +
        (created.overlap_count > 0
          ? `, ${created.overlap_count} overlap${
              created.overlap_count === 1 ? '' : 's'
            } combined`
          : '') +
        '. Click Translate on the new row.',
    )
    reload()
    setTimeout(() => setMergeNotice(''), 8000)
  }

  const startEditingName = () => {
    setNameDraft(project?.name || '')
    setNameErr('')
    setEditingName(true)
  }

  const saveName = async () => {
    const trimmed = nameDraft.trim()
    if (!trimmed) {
      setNameErr('Name cannot be empty')
      return
    }
    if (trimmed === project?.name) {
      setEditingName(false)
      return
    }
    setSavingName(true)
    setNameErr('')
    try {
      const updated = await api.updateProject(projectId, { name: trimmed })
      setProject(updated)
      setEditingName(false)
    } catch (e: unknown) {
      setNameErr(String(e))
    } finally {
      setSavingName(false)
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
        {editingName ? (
          <div className="stack" style={{ gap: 4, flex: 1 }}>
            <div className="row" style={{ gap: 6, alignItems: 'center' }}>
              <input
                value={nameDraft}
                onChange={(e) => setNameDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') void saveName()
                  if (e.key === 'Escape') setEditingName(false)
                }}
                autoFocus
                disabled={savingName}
                aria-label="Project name"
                style={{ fontSize: '1.2rem', fontWeight: 600 }}
              />
              <button
                type="button"
                className="small primary"
                onClick={saveName}
                disabled={savingName}
              >
                {savingName ? 'Saving…' : 'Save'}
              </button>
              <button
                type="button"
                className="small"
                onClick={() => setEditingName(false)}
                disabled={savingName}
              >
                Cancel
              </button>
            </div>
            {nameErr && (
              <div className="small" style={{ color: 'var(--error)' }}>{nameErr}</div>
            )}
          </div>
        ) : (
          <h2 className="row" style={{ margin: 0, gap: 8, alignItems: 'center' }}>
            {project.name}
            <button
              type="button"
              className="icon-button"
              onClick={startEditingName}
              title="Rename project"
              aria-label="Rename project"
            >
              &#9998;
            </button>
          </h2>
        )}
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

        <div className="row" style={{ marginTop: 8 }}>
          <button onClick={() => setMergeOpen(true)}>Merge subtitles…</button>
          <span className="small muted">
            Stitch a “forced” track and a “standard”/full track into one union
            subtitle, then translate the result.
          </span>
        </div>
        {mergeNotice && (
          <div className="small" style={{ color: 'var(--success)' }}>{mergeNotice}</div>
        )}
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

      {mergeOpen && (
        <MergeSubtitles
          projectId={projectId}
          files={files}
          onCancel={() => setMergeOpen(false)}
          onDone={handleMergeDone}
        />
      )}

      {files.length === 0 ? (
        <div className="empty">No files uploaded yet.</div>
      ) : (
        <div className="card file-list-card">
          <div className="row between file-list-toolbar">
            <div className="small muted file-list-toolbar-hint">
              Export any subtitle — original, unified, or translated — to the
              source video's folder, your downloads, or a chosen folder.
            </div>
            <button
              type="button"
              className="primary"
              onClick={() => setExportOpen(true)}
            >
              Export subtitles…
            </button>
          </div>
          <div className="file-list">
            <div className="file-list-head">
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
                onDelete={handleDelete}
                onTranslate={handleTranslate}
                onRetryOcr={handleRetryOcr}
                onRename={handleRename}
              />
            ))}
          </div>
        </div>
      )}

      {exportOpen && (
        <ExportSubtitles
          projectId={projectId}
          files={files}
          onClose={() => setExportOpen(false)}
        />
      )}
    </div>
  )
}

function FileRow({
  f,
  onDelete,
  onTranslate,
  onRetryOcr,
  onRename,
}: {
  f: SubtitleFile
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
              {f.source_track_name && (
                <span
                  className="badge"
                  title="Source subtitle track this row was extracted from"
                  style={{ fontWeight: 400 }}
                >
                  🎞 {f.source_track_name}
                </span>
              )}
              {f.source_format === 'merged' && (
                <span className="small muted" title="Created by merging two subtitles">
                  · merged
                </span>
              )}
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
