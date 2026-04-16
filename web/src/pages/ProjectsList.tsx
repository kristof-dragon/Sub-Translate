import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import type { Language, OllamaModel, Project } from '../types'

export default function ProjectsList() {
  const [projects, setProjects] = useState<Project[]>([])
  const [languages, setLanguages] = useState<Language[]>([])
  const [models, setModels] = useState<OllamaModel[]>([])
  const [err, setErr] = useState('')
  const [creating, setCreating] = useState(false)

  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [targetLang, setTargetLang] = useState('')
  const [model, setModel] = useState('')

  const reload = async () => {
    try {
      const rows = await api.listProjects()
      setProjects(rows)
    } catch (e: unknown) {
      setErr(String(e))
    }
  }

  useEffect(() => {
    reload()
    api.listLanguages().then(setLanguages).catch(() => {})
    api.listModels().then((r) => setModels(r.models)).catch(() => setModels([]))
  }, [])

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault()
    setErr('')
    try {
      await api.createProject({
        name,
        description,
        default_target_lang: targetLang,
        default_model: model,
      })
      setName('')
      setDescription('')
      setTargetLang('')
      setModel('')
      setCreating(false)
      reload()
    } catch (e: unknown) {
      setErr(String(e))
    }
  }

  const handleDelete = async (id: number) => {
    if (!confirm('Delete this project and all its files?')) return
    await api.deleteProject(id)
    reload()
  }

  return (
    <div>
      <div className="row between" style={{ marginBottom: 16 }}>
        <h2 style={{ margin: 0 }}>Projects</h2>
        <button className="primary" onClick={() => setCreating((c) => !c)}>
          {creating ? 'Cancel' : 'New project'}
        </button>
      </div>

      {err && <div className="error-msg">{err}</div>}

      {creating && (
        <form className="card stack" onSubmit={handleCreate}>
          <div>
            <label>Name *</label>
            <input
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
          </div>
          <div>
            <label>Description</label>
            <textarea
              rows={2}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>
          <div className="form-grid">
            <div>
              <label>Default target language</label>
              <select
                value={targetLang}
                onChange={(e) => setTargetLang(e.target.value)}
              >
                <option value="">(inherit global)</option>
                {languages.map((l) => (
                  <option key={l.code} value={l.code}>{l.name}</option>
                ))}
              </select>
            </div>
            <div>
              <label>Default model override</label>
              <select value={model} onChange={(e) => setModel(e.target.value)}>
                <option value="">(use global default)</option>
                {models.map((m) => (
                  <option key={m.name} value={m.name}>{m.name}</option>
                ))}
              </select>
            </div>
          </div>
          <div className="row">
            <button type="submit" className="primary">Create</button>
          </div>
        </form>
      )}

      {projects.length === 0 ? (
        <div className="empty">No projects yet. Click <b>New project</b> to start.</div>
      ) : (
        <div className="grid">
          {projects.map((p) => (
            <div key={p.id} className="card stack">
              <div className="row between">
                <Link to={`/projects/${p.id}`}><b>{p.name}</b></Link>
                <button className="danger small" onClick={() => handleDelete(p.id)}>Delete</button>
              </div>
              {p.description && <div className="muted">{p.description}</div>}
              <div className="small muted">
                {p.file_count} file{p.file_count === 1 ? '' : 's'}
                {p.default_target_lang && <> · target: {p.default_target_lang}</>}
                {p.default_model && <> · model: {p.default_model}</>}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
