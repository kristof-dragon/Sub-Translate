import { useEffect, useState } from 'react'
import { api } from '../api'
import type { AppSettings, OllamaModel } from '../types'

export default function Settings() {
  const [settings, setSettings] = useState<AppSettings | null>(null)
  const [url, setUrl] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [chunkSize, setChunkSize] = useState(30)
  const [defaultModel, setDefaultModel] = useState('')
  const [models, setModels] = useState<OllamaModel[]>([])
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [loadingModels, setLoadingModels] = useState(false)

  useEffect(() => {
    api.getSettings().then((s) => {
      setSettings(s)
      setUrl(s.ollama_url)
      setDefaultModel(s.default_model)
      setChunkSize(s.chunk_size)
      if (s.ollama_url) void refreshModels()
    }).catch((e) => setErr(String(e)))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const refreshModels = async () => {
    setLoadingModels(true)
    setErr('')
    try {
      const r = await api.listModels()
      setModels(r.models)
    } catch (e: unknown) {
      setErr(String(e))
      setModels([])
    } finally {
      setLoadingModels(false)
    }
  }

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault()
    setErr('')
    setMsg('')
    try {
      // Only send api_key if the user typed something — blank keeps existing.
      const payload: Partial<AppSettings & { ollama_api_key: string }> = {
        ollama_url: url.trim(),
        default_model: defaultModel,
        chunk_size: chunkSize,
      }
      if (apiKey) payload.ollama_api_key = apiKey
      const updated = await api.updateSettings(payload)
      setSettings(updated)
      setApiKey('')
      setMsg('Saved.')
      await refreshModels()
    } catch (e: unknown) {
      setErr(String(e))
    }
  }

  return (
    <div className="stack">
      <h2>Settings</h2>

      {err && <div className="error-msg">{err}</div>}
      {msg && <div className="success-msg">{msg}</div>}

      <form className="card stack" onSubmit={handleSave}>
        <div>
          <label>Ollama base URL *</label>
          <input
            placeholder="http://192.168.1.50:11434"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            required
          />
          <div className="small muted">
            Full URL including <code>http://</code> and port. Ollama default is 11434.
          </div>
        </div>

        <div>
          <label>
            API key / bearer token (optional)
            {settings?.ollama_api_key_set && (
              <span className="small muted"> — currently set, leave blank to keep</span>
            )}
          </label>
          <input
            type="password"
            placeholder={settings?.ollama_api_key_set ? '••••••••' : 'leave blank if not required'}
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
          />
        </div>

        <div className="form-grid">
          <div>
            <label>Default model</label>
            <div className="row" style={{ gap: 8 }}>
              <select
                value={defaultModel}
                onChange={(e) => setDefaultModel(e.target.value)}
                style={{ flex: 1 }}
              >
                <option value="">(none)</option>
                {models.map((m) => (
                  <option key={m.name} value={m.name}>{m.name}</option>
                ))}
                {defaultModel && !models.find((m) => m.name === defaultModel) && (
                  <option value={defaultModel}>{defaultModel} (offline)</option>
                )}
              </select>
              <button type="button" onClick={refreshModels} disabled={loadingModels || !url}>
                {loadingModels ? 'Loading…' : 'Refresh'}
              </button>
            </div>
            <div className="small muted">
              {models.length > 0
                ? `${models.length} model${models.length === 1 ? '' : 's'} available`
                : url
                  ? 'Save or refresh to list models from this Ollama instance.'
                  : 'Save the URL first to list available models.'}
            </div>
          </div>

          <div>
            <label>Chunk size (cues per Ollama call)</label>
            <input
              type="number"
              min={1}
              max={500}
              value={chunkSize}
              onChange={(e) => setChunkSize(Number(e.target.value))}
            />
            <div className="small muted">
              Smaller = more accurate mapping but slower; larger = faster but risks context overrun on smaller models.
            </div>
          </div>
        </div>

        <div className="row">
          <button type="submit" className="primary">Save</button>
        </div>
      </form>
    </div>
  )
}
