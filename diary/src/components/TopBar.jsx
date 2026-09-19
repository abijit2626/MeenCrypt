import { THEME_PREFS, useTheme } from '../lib/theme'
import { useDiary } from '../vault/DiaryContext'
import Icon from './Icon'

const THEME_ICON = { light: 'sun', dark: 'moon' }

export default function TopBar({ query, onQuery, onRequestUnlock }) {
  const { unlocked, lock, unlockProgress } = useDiary()
  const [theme, setTheme] = useTheme()
  const nextTheme = THEME_PREFS[(THEME_PREFS.indexOf(theme) + 1) % THEME_PREFS.length]
  return (
    <header className="topbar">
      <label className="search">
        <span aria-hidden><Icon name="search" /></span>
        <input
          type="search"
          value={query}
          onChange={(e) => onQuery(e.target.value)}
          disabled={!unlocked}
          placeholder={unlocked ? 'Search this session’s unlocked entries' : 'Unlock the vault to search'}
          title="Searches only entries decrypted in this tab — nothing is searched on the server"
        />
      </label>
      <button
        className="icon-btn theme-toggle"
        onClick={() => setTheme(nextTheme)}
        aria-label={`Theme: ${theme}`}
        title={`Theme: ${theme} (click for ${nextTheme})`}
      >
        <Icon name={THEME_ICON[theme]} />
      </button>
      <button
        className={`vault-toggle ${unlocked ? 'open' : 'sealed'}`}
        onClick={unlocked ? lock : onRequestUnlock}
        aria-label={unlocked ? 'Vault unlocked' : 'Vault locked'}
        title={unlocked ? 'Lock the vault (forgets the key and all decrypted text)' : 'Unlock with your private key'}
      >
        <span aria-hidden><Icon name={unlocked ? 'lockOpen' : 'lockClosed'} /></span>
        <span className="vault-label">{unlocked ? 'Vault unlocked' : 'Vault locked'}</span>
        {unlockProgress && <span className="mono dim">{unlockProgress.done}/{unlockProgress.total}</span>}
      </button>
    </header>
  )
}
