import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api'
import VideoBrowser from '../components/VideoBrowser'
import UploadDropzone from '../components/UploadDropzone'
import type {
  AppSettings,
  EventMessage,
  Language,
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
              detected_lang: data.detected_lang ?? r.detected_lang,
              error: data.error ?? r.error,
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
        <div className="card">
          <table className="file-list">
            <thead>
              <tr>
                <th>File</th>
                <th>Status</th>
                <th>Detected</th>
                <th>Target</th>
                <th>Progress</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {files.map((f) => (
                <FileRow
                  key={f.id}
                  f={f}
                  onDelete={handleDelete}
                  onTranslate={handleTranslate}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function FileRow({
  f,
  onDelete,
  onTranslate,
}: {
  f: SubtitleFile
  onDelete: (id: number) => void
  onTranslate: (id: number) => void
}) {
  const barRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (barRef.current) barRef.current.style.width = `${f.progress_pct}%`
  }, [f.progress_pct])

  // Translate button is offered on any row that isn't currently mid-flight —
  // covers first-run (extracted), retry (error), and re-translate (done).
  const canTranslate = ['extracted', 'done', 'error'].includes(f.status)

  return (
    <tr>
      <td>
        <div>{f.original_filename}</div>
        {f.error && <div className="small" style={{ color: 'var(--error)' }}>{f.error}</div>}
      </td>
      <td><span className={`badge ${f.status}`}>{f.status}</span></td>
      <td className="small muted">{f.detected_lang || '—'}</td>
      <td className="small">{f.target_lang || '—'}</td>
      <td style={{ minWidth: 160 }}>
        <div className="progress"><div ref={barRef} /></div>
        <div className="small muted">{f.progress_pct}%</div>
      </td>
      <td style={{ whiteSpace: 'nowrap' }}>
        {canTranslate && (
          <button
            className="small"
            onClick={() => onTranslate(f.id)}
            style={{ marginRight: 8 }}
            title="Queue this file for translation using the target language selected at the top of the page"
          >
            Translate
          </button>
        )}
        {f.translated_available && (
          <a
            href={api.downloadUrl(f.id)}
            download
            className="small"
            style={{ marginRight: 8 }}
          >
            Download
          </a>
        )}
        <button className="danger small" onClick={() => onDelete(f.id)}>Delete</button>
      </td>
    </tr>
  )
}
