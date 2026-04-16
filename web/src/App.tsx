import { useState } from 'react'
import { NavLink, Route, Routes } from 'react-router-dom'
import ProjectsList from './pages/ProjectsList'
import ProjectDetail from './pages/ProjectDetail'
import Settings from './pages/Settings'

// Host + port where jlesage/mkvtoolnix exposes its noVNC web GUI.
// We assume it's on the same host as the app, default jlesage port 5800.
// Override GUI_PORT in .env if you remapped it.
const MKVTOOLNIX_PORT = 5800

export default function App() {
  const [open, setOpen] = useState(true)
  const mkvGuiHref = `${window.location.protocol}//${window.location.hostname}:${MKVTOOLNIX_PORT}/`

  return (
    <div className="app">
      <header className="topbar">
        <button
          className="hamburger"
          aria-label="Toggle menu"
          onClick={() => setOpen((o) => !o)}
        >
          &#9776;
        </button>
        <h1>Subtitle Translator</h1>
      </header>

      <div className="layout">
        <aside className={open ? 'drawer' : 'drawer closed'}>
          <nav>
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
