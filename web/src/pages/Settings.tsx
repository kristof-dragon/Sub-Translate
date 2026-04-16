import { useEffect, useState } from 'react'
import { api } from '../api'
import type { AppSettings, OllamaModel } from '../types'

export default function Settings() {
  const [settings, setSettings] = useState<AppSettings | null>(null)
  const [url, setUrl] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [chunkSize, setChunkSize] = useState(30)
  const [defaultModel, setDefaultModel] = useState('')
  const [disableThinking, setDisableThinking] = useState(false)
  const [requestTimeout, setRequestTimeout] = useState(600)
  const [numCtx, setNumCtx] = useState(0)
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
      setDisableThinking(s.disable_thinking)
      setRequestTimeout(s.request_timeout)
      setNumCtx(s.num_ctx)
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
        disable_thinking: disableThinking,
        request_timeout: requestTimeout,
        num_ctx: numCtx,
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

          <div>
            <label>Request timeout (seconds)</label>
            <input
              type="number"
              min={10}
              max={7200}
              value={requestTimeout}
              onChange={(e) => setRequestTimeout(Number(e.target.value))}
            />
            <div className="small muted">
              Per-call timeout applied to translate and detect requests. Large
              local models on slow hardware may need several minutes per chunk.
            </div>
          </div>

          <div>
            <label>Context window override (num_ctx)</label>
            <input
              type="number"
              min={0}
              max={262144}
              step={512}
              value={numCtx}
              onChange={(e) => setNumCtx(Number(e.target.value))}
            />
            <div className="small muted">
              0 = use the model's default (nothing is sent). Any positive value
              is forwarded as <code>options.num_ctx</code> on every request.
            </div>
          </div>
        </div>

        <div>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, margin: 0 }}>
            <input
              type="checkbox"
              checked={disableThinking}
              onChange={(e) => setDisableThinking(e.target.checked)}
              style={{ width: 'auto' }}
            />
            <span>Disable reasoning dialogue (<code>think=false</code>)</span>
          </label>
          <div className="small muted" style={{ marginTop: 4 }}>
            Skips the chain-of-thought prelude on thinking-capable models
            (deepseek-r1, qwq, gpt-oss, …). Has no effect on other models.
          </div>
        </div>

        <div className="muted small" style={{ borderTop: '1px solid var(--border)', paddingTop: 8 }}>
          <strong>Request preview:</strong>{' '}
          {settings?.context_sent
            ? (
              <span>
                each <code>/api/generate</code> call will include
                {' '}
                <code>options.num_ctx={settings.num_ctx}</code>
              </span>
            )
            : <span>no <code>context</code> value is attached — model defaults apply</span>}
          {settings?.disable_thinking && (
            <span>
              {' '}· <code>think=false</code>
            </span>
          )}
          {' '}· timeout {settings?.request_timeout ?? '—'}s
        </div>

        <div className="row">
          <button type="submit" className="primary">Save</button>
        </div>
      </form>
    </div>
  )
}
