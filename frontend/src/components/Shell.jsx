// Shell.jsx - the frame around every page: a dark sidebar on the left, a top bar, and the page itself.
//
// The sidebar's menu does not change page. Each item scrolls to its section on the results page, so the
// page stays one screen you can read top to bottom. On the upload page the menu is replaced by a way back.

import Icon from './Icon'

export const NAV = [
  { id: 'plan', label: 'Site plan', icon: 'plan' },
  { id: 'cameras', label: 'Camera setup', icon: 'camera' },
  { id: 'simulation', label: 'Intruder simulation', icon: 'intruder' },
  { id: 'analytics', label: 'Analytics', icon: 'chart' },
  { id: 'recommendations', label: 'Recommendations', icon: 'bulb' },
  { id: 'reports', label: 'Reports', icon: 'file' },
]

function jump(id) {
  const el = document.getElementById(id)
  if (!el) return
  const calm = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  el.scrollIntoView({ behavior: calm ? 'auto' : 'smooth', block: 'start' })
}

export function Sidebar({ onHome, nav, details, current }) {
  return (
    <aside className="sidebar">
      <a href="./" className="brand" onClick={(e) => { e.preventDefault(); onHome() }}>
        <span className="brand-mark"><Icon name="logo" size={30} /></span>
        <span>
          <span className="brand-name">RedHouse</span>
          <span className="brand-tag">Secure spaces, safer tomorrows</span>
        </span>
      </a>

      {nav ? (
        <nav aria-label="Sections of this page">
          <ul className="nav">
            {NAV.map((n) => (
              <li key={n.id}>
                <a
                  href={`#${n.id}`}
                  className={n.id === current ? 'nav-item is-current' : 'nav-item'}
                  aria-current={n.id === current ? 'location' : undefined}
                  onClick={(e) => { e.preventDefault(); jump(n.id) }}
                >
                  <Icon name={n.icon} />
                  {n.label}
                </a>
              </li>
            ))}
          </ul>
        </nav>
      ) : null}

      {details}
    </aside>
  )
}

export function Topbar({ children }) {
  return <header className="topbar">{children}</header>
}

export default function Shell({ onHome, nav = true, details = null, current, top, children }) {
  return (
    <div className="shell">
      <Sidebar onHome={onHome} nav={nav} details={details} current={current} />
      <div className="main">
        <Topbar>{top}</Topbar>
        {children}
      </div>
    </div>
  )
}
