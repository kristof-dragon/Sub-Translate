import { useEffect, useState } from 'react'
import { NavLink, Route, Routes, useNavigate } from 'react-router-dom'
import { api } from './api'
import ProjectsList from './pages/ProjectsList'
import ProjectDetail from './pages/ProjectDetail'
import Settings from './pages/Settings'
import type { OllamaHealth } from './types'

// Host + port where jlesage/mkvtoolnix exposes its noVNC web GUI.
// We assume it's on the same host as the app, default jlesage port 5800.
// Override GUI_PORT in .env if you remapped it.
const MKVTOOLNIX_PORT = 5800

// How often to re-probe Ollama for the topbar status dot.
const LLM_HEALTH_POLL_MS = 30_000

function LlmStatus() {
  const nav = useNavigate()
  const [health, setHealth] = useState<OllamaHealth | null>(null)

  useEffect(() => {
    let cancelled = false
    const tick = async () => {
      try {
        const h = await api.ollamaHealth()
        if (!cancelled) setHealth(h)
      } catch {
        if (!cancelled) setHealth({ configured: true, ok: false, error: 'unreachable' })
      }
    }
    tick()
    const id = window.setInterval(tick, LLM_HEALTH_POLL_MS)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [])

  // null = still loading first response → treat as unknown/yellow
  let state: 'ok' | 'down' | 'unconfigured' = 'unconfigured'
  if (health) {
    if (!health.configured) state = 'unconfigured'
    else if (health.ok) state = 'ok'
    else state = 'down'
  }

  const titles: Record<typeof state, string> = {
    ok: 'Ollama reachable — click to open Settings',
    down: `Ollama unreachable${health?.error ? ` (${health.error})` : ''} — click to open Settings`,
    unconfigured: 'Ollama not configured yet — click to open Settings',
  }

  return (
    <button
      type="button"
      className="llm-status"
      title={titles[state]}
      aria-label={titles[state]}
      onClick={() => nav('/settings')}
    >
      <span className="llm-status-label">LLM</span>
      <span className={`llm-status-dot ${state}`} />
    </button>
  )
}

// Below this width the drawer becomes a slide-in overlay and starts closed;
// above it the drawer is part of the flex layout and starts open. Keep in sync
// with the `@media (max-width: 700px)` rules in index.css.
const MOBILE_MAX_PX = 700

export default function App() {
  const [isMobile, setIsMobile] = useState(() =>
    typeof window !== 'undefined'
      ? window.matchMedia(`(max-width: ${MOBILE_MAX_PX}px)`).matches
      : false,
  )
  // `open` defaults to the opposite of mobile — desktop users see the drawer
  // on first load, mobile users get it tucked away until they tap the burger.
  const [open, setOpen] = useState(() => !isMobile)

  useEffect(() => {
    const mql = window.matchMedia(`(max-width: ${MOBILE_MAX_PX}px)`)
    const handler = (e: MediaQueryListEvent) => {
      setIsMobile(e.matches)
      // Re-sync open state when crossing the breakpoint so a narrow window
      // doesn't land on a wide-style permanently-open drawer and vice versa.
      setOpen(!e.matches)
    }
    mql.addEventListener('change', handler)
    return () => mql.removeEventListener('change', handler)
  }, [])

  // On mobile, tapping any nav item should dismiss the overlay — it would
  // otherwise stay draped over the destination page until backdrop-tapped.
  const closeIfMobile = () => {
    if (isMobile) setOpen(false)
  }

  const mkvGuiHref = `${window.location.protocol}//${window.location.hostname}:${MKVTOOLNIX_PORT}/`

  return (
    <div className="app">
      <header className="topbar">
        <button
          className="hamburger"
          aria-label="Toggle menu"
          aria-expanded={open}
          onClick={() => setOpen((o) => !o)}
        >
          &#9776;
        </button>
        <h1>Subtitle Translator</h1>
        <LlmStatus />
      </header>

      <div className="layout">
        {isMobile && open && (
          <div
            className="drawer-backdrop"
            onClick={() => setOpen(false)}
            aria-hidden="true"
          />
        )}
        <aside className={`drawer${open ? '' : ' closed'}${isMobile ? ' mobile' : ''}`}>
          <nav onClick={closeIfMobile}>
            <NavLink to="/" end>Projects</NavLink>
            <NavLink to="/settings">Settings</NavLink>
            <div className="drawer-section">Tools</div>
            <a href={mkvGuiHref} target="_blank" rel="noopener noreferrer">
              MKVToolNix GUI &#8599;
            </a>
          </nav>
        </aside>

        <main className="content">
          <Routes>
            <Route path="/" element={<ProjectsList />} />
            <Route path="/projects/:id" element={<ProjectDetail />} />
            <Route path="/settings" element={<Settings />} />
          </Routes>
        </main>
      </div>
    </div>
  )
}
