import { useMemo, useState } from 'react'
import Composer from './components/Composer'
import EntryReader from './components/EntryReader'
import Icon from './components/Icon'
import Sidebar from './components/Sidebar'
import TopBar from './components/TopBar'
import UnlockModal from './components/UnlockModal'
import { moodById } from './lib/payload'
import { DiaryProvider, useDiary } from './vault/DiaryContext'
import CalendarView from './views/CalendarView'
import EntriesView from './views/EntriesView'
import EntryGrid from './views/EntryGrid'
import StatsView from './views/StatsView'
import TagsView from './views/TagsView'
import VaultView from './views/VaultView'
import './App.css'

function Shell() {
  const { entries, unlocked, decrypted, loading, loadError, refresh, updateFlags, purge } = useDiary()
  const [view, setView] = useState('entries')
  const [query, setQuery] = useState('')
  const [composerOpen, setComposerOpen] = useState(false)
  const [unlockOpen, setUnlockOpen] = useState(false)
  const [readingId, setReadingId] = useState(null)
  const [focusDay, setFocusDay] = useState(null)
  const [actionError, setActionError] = useState('')

  const active = entries.filter((e) => !e.flags?.archived && !e.flags?.deleted_at)
  const archived = entries.filter((e) => e.flags?.archived && !e.flags?.deleted_at)
  const binned = entries.filter((e) => e.flags?.deleted_at)
  const notBinned = entries.filter((e) => !e.flags?.deleted_at)

  // Client-side search over this session's decrypted text only.
  const needle = unlocked ? query.trim().toLowerCase() : ''
  const matches = useMemo(() => (entry) => {
    if (!needle) return true
    const content = decrypted.get(entry.id)
    if (!content) return false
    const mood = moodById(content.mood)
    return content.body.toLowerCase().includes(needle) || (mood && mood.label.includes(needle))
  }, [needle, decrypted])

  function openEntry(entry) {
    if (decrypted.has(entry.id)) setReadingId(entry.id)
    else setUnlockOpen(true)
  }

  async function act(fn) {
    setActionError('')
    try {
      await fn()
    } catch (err) {
      setActionError(String(err.message || err))
    }
  }

  function renderActions(entry) {
    if (entry.flags?.deleted_at) {
      return (
        <>
          <button className="icon-btn" title="Restore" onClick={() => act(() => updateFlags(entry.id, { deleted: false }))}><Icon name="undo" /></button>
          <button
            className="icon-btn"
            title="Delete forever"
            onClick={() => window.confirm('Delete this entry forever? The ciphertext is removed from disk and cannot be recovered.')
              && act(() => purge(entry.id))}
          >
            <Icon name="close" />
          </button>
        </>
      )
    }
    return (
      <>
        <button className={`icon-btn ${entry.flags?.pinned ? 'on' : ''}`} title={entry.flags?.pinned ? 'Unpin' : 'Pin'}
          onClick={() => act(() => updateFlags(entry.id, { pinned: !entry.flags?.pinned }))}><Icon name="pin" /></button>
        <button className="icon-btn" title={entry.flags?.archived ? 'Unarchive' : 'Archive'}
          onClick={() => act(() => updateFlags(entry.id, { archived: !entry.flags?.archived }))}><Icon name="archive" /></button>
        <button className="icon-btn" title="Move to bin"
          onClick={() => act(() => updateFlags(entry.id, { deleted: true }))}><Icon name="trash" /></button>
      </>
    )
  }

  const shared = { onOpen: openEntry, renderActions }
  const requestUnlock = () => setUnlockOpen(true)
  const reading = readingId && entries.find((e) => e.id === readingId)

  let content
  if (view === 'entries') {
    content = (
      <EntriesView
        entries={active.filter(matches)}
        searching={!!needle}
        focusDay={focusDay}
        onFocusHandled={() => setFocusDay(null)}
        {...shared}
      />
    )
  } else if (view === 'calendar') {
    content = <CalendarView entries={notBinned} onSelectDay={(key) => { setFocusDay(key); setView('entries') }} />
  } else if (view === 'vault') {
    content = <VaultView onRequestUnlock={requestUnlock} />
  } else if (view === 'tags') {
    content = <TagsView entries={notBinned.filter(matches)} onRequestUnlock={requestUnlock} {...shared} />
  } else if (view === 'stats') {
    content = <StatsView entries={notBinned} onOpenId={setReadingId} onRequestUnlock={requestUnlock} />
  } else if (view === 'archive') {
    content = (
      <>
        <h2 className="section-title">Archive</h2>
        <EntryGrid entries={archived.filter(matches)} empty="Nothing archived." {...shared} />
      </>
    )
  } else {
    content = (
      <>
        <h2 className="section-title">Bin</h2>
        <p className="muted small">Entries stay here until you delete them forever.</p>
        <EntryGrid entries={binned.filter(matches)} empty="The bin is empty." {...shared} />
      </>
    )
  }

  return (
    <div className="app">
      <Sidebar
        view={view}
        onNavigate={setView}
        counts={{ entries: active.length, archive: archived.length, bin: binned.length }}
      />
      <div className="main">
        <TopBar query={query} onQuery={setQuery} onRequestUnlock={requestUnlock} />
        <main className="content">
          {loadError && (
            <div className="alert bad row">
              <span>Can’t reach the diary server: {loadError}</span>
              <span className="spacer" />
              <button className="btn ghost small" onClick={refresh}>Retry</button>
            </div>
          )}
          {actionError && <div className="alert bad">{actionError.slice(0, 200)}</div>}
          {loading ? <p className="empty">Opening the tank…</p> : content}
        </main>
      </div>

      {!composerOpen && (
        <button className="fab" onClick={() => setComposerOpen(true)} aria-label="New entry" title="New entry">+</button>
      )}
      {composerOpen && <Composer onClose={() => setComposerOpen(false)} />}
      {unlockOpen && <UnlockModal onClose={() => setUnlockOpen(false)} />}
      {reading && <EntryReader entry={reading} onClose={() => setReadingId(null)} />}
    </div>
  )
}

export default function App() {
  return (
    <DiaryProvider>
      <Shell />
    </DiaryProvider>
  )
}
