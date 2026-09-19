import Icon from './Icon'

const NAV_ITEMS = [
  { id: 'entries', icon: 'fish', label: 'Entries' },
  { id: 'calendar', icon: 'calendar', label: 'Calendar' },
  { id: 'vault', icon: 'vault', label: 'Vault' },
  { id: 'tags', icon: 'tag', label: 'Tags' },
  { id: 'stats', icon: 'barChart', label: 'Stats' },
  { id: 'archive', icon: 'archive', label: 'Archive' },
  { id: 'bin', icon: 'trash', label: 'Bin' },
]

export default function Sidebar({ view, onNavigate, counts }) {
  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="brand-mark" aria-hidden><Icon name="fish" size={22} /></span>
        <span className="brand-name">MeenCrypt</span>
      </div>
      <nav className="side-nav">
        {NAV_ITEMS.map((item) => (
          <button
            key={item.id}
            className={`side-link ${view === item.id ? 'active' : ''}`}
            onClick={() => onNavigate(item.id)}
          >
            <span className="side-icon" aria-hidden><Icon name={item.icon} size={17} /></span>
            <span className="side-label">{item.label}</span>
            {counts[item.id] > 0 && <span className="side-count">{counts[item.id]}</span>}
          </button>
        ))}
      </nav>
    </aside>
  )
}
