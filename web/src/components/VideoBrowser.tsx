import { useEffect, useState } from 'react'
import { api } from '../api'
import type { BrowseResponse, VideoTrack } from '../types'

interface Props {
  onCancel: () => void
  onExtract: (body: { video_path: string; track_ids: number[] }) => Promise<void>
}

/**
 * Two-step modal: pick a video from the bind-mounted media folder, then pick
 * the subtitle tracks to extract. Extraction only demuxes the tracks into the
 * project — translation is a separate step triggered from the file list.
 */
export default function VideoBrowser({ onCancel, onExtract }: Props) {
  const [view, setView] = useState<BrowseResponse | null>(null)
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(false)

  const [selectedVideo, setSelectedVideo] = useState<string | null>(null)
  const [tracks, setTracks] = useState<VideoTrack[]>([])
  const [selectedTracks, setSelectedTracks] = useState<Set<number>>(new Set())
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

  const pickVideo = async (relPath: string) => {
    setLoading(true)
    setErr('')
    try {
      const r = await api.videoTracks(relPath)
      setSelectedVideo(relPath)
      setTracks(r.tracks)
      // Default: tick every supported track so the common case is one click.
      setSelectedTracks(
        new Set(r.tracks.filter((t) => t.supported).map((t) => t.id)),
      )
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
    if (!selectedVideo || selectedTracks.size === 0) return
    setSubmitting(true)
    setErr('')
    try {
      await onExtract({
        video_path: selectedVideo,
        track_ids: Array.from(selectedTracks),
      })
    } catch (e: unknown) {
      setErr(String(e))
    } finally {
      setSubmitting(false)
    }
  }

  const backToBrowse = () => {
    setSelectedVideo(null)
    setTracks([])
    setSelectedTracks(new Set())
  }

  return (
    <div className="modal-backdrop" onClick={onCancel}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="row between" style={{ marginBottom: 12 }}>
          <h3 style={{ margin: 0 }}>
            {selectedVideo
              ? 'Pick subtitle tracks to extract'
              : 'Add from video — pick a file'}
          </h3>
          <button onClick={onCancel}>Close</button>
        </div>

        {err && <div className="error-msg">{err}</div>}

        {!selectedVideo ? (
          <BrowserPane
            view={view}
            loading={loading}
            onNavigate={loadDir}
            onPick={pickVideo}
          />
        ) : (
          <TrackPane
            videoPath={selectedVideo}
            tracks={tracks}
            selected={selectedTracks}
            onToggle={toggle}
            onBack={backToBrowse}
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
  videoPath,
  tracks,
  selected,
  onToggle,
  onBack,
  onSubmit,
  submitting,
}: {
  videoPath: string
  tracks: VideoTrack[]
  selected: Set<number>
  onToggle: (id: number) => void
  onBack: () => void
  onSubmit: () => void
  submitting: boolean
}) {
  const supportedCount = tracks.filter((t) => t.supported).length
  return (
    <div className="stack">
      <div className="muted small">File: {videoPath}</div>
      {supportedCount === 0 && (
        <div className="error-msg">
          No supported (text) subtitle tracks found. ASS/SSA/PGS/VobSub are
          detected but not yet translatable.
        </div>
      )}
      <table className="file-list">
        <thead>
          <tr>
            <th style={{ width: 32 }}></th>
            <th style={{ width: 40 }}>#</th>
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
                {t.codec}
                {!t.supported && ' (not supported)'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="muted small">
        Tracks will be extracted into this project as subtitle files. Translate
        them afterwards with the Translate button on each row.
      </div>

      <div className="row between">
        <button onClick={onBack}>&larr; Back to browser</button>
        <button
          className="primary"
          disabled={submitting || selected.size === 0}
          onClick={onSubmit}
        >
          {submitting
            ? 'Extracting…'
            : `Extract ${selected.size} track${selected.size === 1 ? '' : 's'}`}
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
