import { useEffect, useState } from 'react'
import { api } from '../api'
import type { BrowseResponse, Language, MkvTrack } from '../types'

interface Props {
  defaultTargetLang: string
  defaultModel: string
  languages: Language[]
  models: { name: string }[]
  onCancel: () => void
  onQueue: (body: {
    mkv_path: string
    track_ids: number[]
    target_lang: string
    model?: string
  }) => Promise<void>
}

/** Two-step modal: pick an MKV from the bind-mounted media folder, then pick tracks. */
export default function MkvBrowser({
  defaultTargetLang,
  defaultModel,
  languages,
  models,
  onCancel,
  onQueue,
}: Props) {
  const [view, setView] = useState<BrowseResponse | null>(null)
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(false)

  const [selectedMkv, setSelectedMkv] = useState<string | null>(null)
  const [tracks, setTracks] = useState<MkvTrack[]>([])
  const [selectedTracks, setSelectedTracks] = useState<Set<number>>(new Set())
  const [targetLang, setTargetLang] = useState(defaultTargetLang)
  const [model, setModel] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const loadDir = async (path: string) => {
    setLoading(true)
    setErr('')
    try {
      setView(await api.browse(path))
    } catch (e: unknown) {
      setErr(String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadDir('')
  }, [])

  const pickMkv = async (relPath: string) => {
    setLoading(true)
    setErr('')
    try {
      const r = await api.mkvTracks(relPath)
      setSelectedMkv(relPath)
      setTracks(r.tracks)
      // Default: tick every supported track so the common case is one click.
      setSelectedTracks(new Set(r.tracks.filter((t) => t.supported).map((t) => t.id)))
    } catch (e: unknown) {
      setErr(String(e))
    } finally {
      setLoading(false)
    }
  }

  const toggle = (id: number) =>
    setSelectedTracks((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const submit = async () => {
    if (!selectedMkv || selectedTracks.size === 0) return
    if (!targetLang) {
      setErr('Pick a target language first')
      return
    }
    setSubmitting(true)
    setErr('')
    try {
      await onQueue({
        mkv_path: selectedMkv,
        track_ids: Array.from(selectedTracks),
        target_lang: targetLang,
        model: model || undefined,
      })
    } catch (e: unknown) {
      setErr(String(e))
    } finally {
      setSubmitting(false)
    }
  }

  const backToBrowse = () => {
    setSelectedMkv(null)
    setTracks([])
    setSelectedTracks(new Set())
  }

  return (
    <div className="modal-backdrop" onClick={onCancel}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="row between" style={{ marginBottom: 12 }}>
          <h3 style={{ margin: 0 }}>
            {selectedMkv ? 'Pick tracks to translate' : 'Add from MKV — pick a file'}
          </h3>
          <button onClick={onCancel}>Close</button>
        </div>

        {err && <div className="error-msg">{err}</div>}

        {!selectedMkv ? (
          <BrowserPane
            view={view}
            loading={loading}
            onNavigate={loadDir}
            onPick={pickMkv}
          />
        ) : (
          <TrackPane
            mkvPath={selectedMkv}
            tracks={tracks}
            selected={selectedTracks}
            onToggle={toggle}
            onBack={backToBrowse}
            targetLang={targetLang}
            setTargetLang={setTargetLang}
            model={model}
            setModel={setModel}
            defaultModel={defaultModel}
            languages={languages}
            models={models}
            onSubmit={submit}
            submitting={submitting}
          />
        )}
      </div>
    </div>
  )
}

function BrowserPane({
  view,
  loading,
  onNavigate,
  onPick,
}: {
  view: BrowseResponse | null
  loading: boolean
  onNavigate: (path: string) => void
  onPick: (path: string) => void
}) {
  if (loading && !view) return <div className="empty">Loading…</div>
  if (!view) return null

  const joinPath = (base: string, name: string) =>
    base ? `${base}/${name}` : name

  return (
    <div>
      <div className="muted small" style={{ marginBottom: 8 }}>
        /media/{view.path}
      </div>
      <div className="file-list-wrap">
        <table className="file-list">
          <tbody>
            {view.parent !== null && (
              <tr className="clickable" onClick={() => onNavigate(view.parent!)}>
                <td colSpan={2}>📁 ..</td>
              </tr>
            )}
            {view.entries.length === 0 && (
              <tr><td colSpan={2} className="muted">(empty)</td></tr>
            )}
            {view.entries.map((e) => (
              <tr
                key={e.name}
                className="clickable"
                onClick={() =>
                  e.is_dir
                    ? onNavigate(joinPath(view.path, e.name))
                    : onPick(joinPath(view.path, e.name))
                }
              >
                <td>
                  {e.is_dir ? '📁 ' : '🎬 '}
                  {e.name}
                </td>
                <td className="small muted" style={{ width: 120, textAlign: 'right' }}>
                  {e.is_dir ? '' : formatBytes(e.size || 0)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function TrackPane({
  mkvPath,
  tracks,
  selected,
  onToggle,
  onBack,
  targetLang,
  setTargetLang,
  model,
  setModel,
  defaultModel,
  languages,
  models,
  onSubmit,
  submitting,
}: {
  mkvPath: string
  tracks: MkvTrack[]
  selected: Set<number>
  onToggle: (id: number) => void
  onBack: () => void
  targetLang: string
  setTargetLang: (s: string) => void
  model: string
  setModel: (s: string) => void
  defaultModel: string
  languages: Language[]
  models: { name: string }[]
  onSubmit: () => void
  submitting: boolean
}) {
  const supportedCount = tracks.filter((t) => t.supported).length
  return (
    <div className="stack">
      <div className="muted small">File: {mkvPath}</div>
      {supportedCount === 0 && (
        <div className="error-msg">
          No supported (text) subtitle tracks found in this MKV. ASS/SSA/PGS are
          detected but not yet translatable.
        </div>
      )}
      <table className="file-list">
        <thead>
          <tr>
            <th style={{ width: 32 }}></th>
            <th style={{ width: 40 }}>ID</th>
            <th>Language</th>
            <th>Name</th>
            <th>Codec</th>
          </tr>
        </thead>
        <tbody>
          {tracks.map((t) => (
            <tr key={t.id} style={{ opacity: t.supported ? 1 : 0.5 }}>
              <td>
                <input
                  type="checkbox"
                  checked={selected.has(t.id)}
                  disabled={!t.supported}
                  onChange={() => onToggle(t.id)}
                  style={{ width: 'auto' }}
                />
              </td>
              <td>{t.id}</td>
              <td>{t.language || '—'}</td>
              <td>{t.name || '—'}</td>
              <td className="small muted">
                {t.codec_id || t.codec}
                {!t.supported && ' (not supported)'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

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
          <label>Model {defaultModel && `(default: ${defaultModel})`}</label>
          <select value={model} onChange={(e) => setModel(e.target.value)}>
            <option value="">(use project / global default)</option>
            {models.map((m) => (
              <option key={m.name} value={m.name}>{m.name}</option>
            ))}
          </select>
        </div>
      </div>

      <div className="row between">
        <button onClick={onBack}>&larr; Back to browser</button>
        <button
          className="primary"
          disabled={submitting || selected.size === 0 || !targetLang}
          onClick={onSubmit}
        >
          {submitting ? 'Extracting…' : `Extract & queue ${selected.size} track${selected.size === 1 ? '' : 's'}`}
        </button>
      </div>
    </div>
  )
}

function formatBytes(n: number): string {
  if (!n) return ''
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let v = n
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return `${v.toFixed(v < 10 ? 1 : 0)} ${units[i]}`
}
