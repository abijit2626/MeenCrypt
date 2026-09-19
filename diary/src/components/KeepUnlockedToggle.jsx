import { useDiary } from '../vault/DiaryContext'

export default function KeepUnlockedToggle() {
  const { keepUnlocked, setKeepUnlocked } = useDiary()
  return (
    <label className="switch-row">
      <input type="checkbox" checked={keepUnlocked} onChange={(e) => setKeepUnlocked(e.target.checked)} />
      <span>
        <strong>Keep unlocked for this session</strong>
        <small>
          Held in this tab’s memory only — never saved to disk or to any browser storage. Closing or
          reloading the tab locks the vault. When off, the vault locks after 10 minutes idle or 2 minutes
          in a background tab.
        </small>
      </span>
    </label>
  )
}
