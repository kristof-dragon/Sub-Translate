import { useEffect, useState } from 'react'
import { api } from '../api'
import type { BrowseResponse } from '../types'

interface Props {
  /** Called with the media-root-relative path the operator selected. */
  onPick: (path: string) => void
  /** Called when the operator dismisses the picker without choosing. */
  onCancel: () => void
  /** Optional — title shown at the top of the modal. */
  title?: string
  /** Optional — extra note shown under the breadcrumb. */
  hint?: string
}

/**
 * Navigate the bind-mounted /media folder and pick a directory. Reuses
 * the same `/api/browse` endpoint as VideoBrowser, but files are shown
 * disabled (they can't be picked) and a "Select this folder" button
 * commits the current directory.
 */
export default function FolderPicker({ onPick, onCancel, title, hint }: Props) {
  const [view, setView] = useState<BrowseResponse | null>(null)
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(false)

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
    void loadDir('')
  }, [])

  const joinPath = (base: string, name: string) => (base ? `${base}/${name}` : name)

  return (
    <div className="modal-backdrop" onClick={onCancel}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="row between" style={{ marginBottom: 12 }}>
          <h3 style={{ margin: 0 }}>{title || 'Pick a target folder'}</h3>
          <button onClick={onCancel}>Cancel</button>
        </div>

        {err && <div className="error-msg">{err}</div>}

        <div className="muted small" style={{ marginBottom: 4 }}>
          /media/{view?.path || ''}
        </div>
        {hint && <div className="muted small" style={{ marginBottom: 8 }}>{hint}</div>}

        {loading && !view ? (
          <div className="empty">Loading…</div>
        ) : view ? (
          <div className="file-list-wrap">
            <table className="file-list">
              <tbody>
                {view.parent !== null && (
                  <tr className="clickable" onClick={() => loadDir(view.parent!)}>
                    <td>📁 ..</td>
                  </tr>
                )}
                {view.entries.length === 0 && (
                  <tr><td className="muted">(empty)</td></tr>
                )}
                {view.entries.map((e) => (
                  <tr
                    key={e.name}
                    className={e.is_dir ? 'clickable' : ''}
                    style={{ opacity: e.is_dir ? 1 : 0.45 }}
                    onClick={() => {
                      if (e.is_dir) void loadDir(joinPath(view.path, e.name))
                    }}
                  >
                    <td>
                      {e.is_dir ? '📁 ' : '🎬 '}
                      {e.name}
                      {!e.is_dir && (
                        <span className="small muted"> (file — folders only)</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}

        <div className="row between" style={{ marginTop: 12 }}>
          <span className="small muted">
            Files at the destination are skipped, not overwritten.
          </span>
          <button
            type="button"
            className="primary"
            disabled={!view}
            onClick={() => view && onPick(view.path)}
          >
            Select this folder
          </button>
        </div>
      </div>
    </div>
  )
}
