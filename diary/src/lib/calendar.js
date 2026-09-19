// Everything here reads only plaintext package metadata (created_at,
// fish_quality) and flags - nothing needs the vault to be unlocked.

const TIER_RANK = { GOOD: 3, MEDIUM: 2, BAD: 1 }

export function createdAt(entry) {
  return new Date(entry.package?.metadata?.created_at || 0)
}

export function tierOf(entry) {
  const q = String(entry.package?.metadata?.fish_quality || '').toUpperCase()
  return TIER_RANK[q] ? q : null
}

// Local calendar day key, e.g. "2026-09-17".
export function dayKey(date) {
  const y = date.getFullYear()
  const m = String(date.getMonth() + 1).padStart(2, '0')
  const d = String(date.getDate()).padStart(2, '0')
  return `${y}-${m}-${d}`
}

function startOfDay(date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate())
}

function addDays(date, n) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate() + n)
}

// One cell per day for the last `days` days, oldest first. A day with
// several entries shows its best tier; any pinned entry outlines the cell.
export function heatmapDays(entries, days = 35, today = new Date()) {
  const byDay = new Map()
  for (const entry of entries) {
    const key = dayKey(createdAt(entry))
    const cell = byDay.get(key) || { count: 0, tier: null, pinned: false }
    cell.count += 1
    const tier = tierOf(entry)
    if (tier && (!cell.tier || TIER_RANK[tier] > TIER_RANK[cell.tier])) cell.tier = tier
    if (entry.flags?.pinned) cell.pinned = true
    byDay.set(key, cell)
  }
  const end = startOfDay(today)
  return Array.from({ length: days }, (_, i) => {
    const date = addDays(end, i - days + 1)
    const key = dayKey(date)
    return { key, date, ...(byDay.get(key) || { count: 0, tier: null, pinned: false }) }
  })
}

function daySet(entries) {
  return new Set(entries.map((e) => dayKey(createdAt(e))))
}

// Consecutive days with an entry, ending today - or yesterday, so the
// streak isn't shown as broken before you've written today.
export function currentStreak(entries, today = new Date()) {
  const days = daySet(entries)
  let cursor = startOfDay(today)
  if (!days.has(dayKey(cursor))) cursor = addDays(cursor, -1)
  let streak = 0
  while (days.has(dayKey(cursor))) {
    streak += 1
    cursor = addDays(cursor, -1)
  }
  return streak
}

export function longestStreak(entries) {
  const sorted = [...daySet(entries)].sort()
  let best = 0
  let run = 0
  let prev = null
  for (const key of sorted) {
    const date = new Date(`${key}T00:00:00`)
    run = prev && dayKey(addDays(prev, 1)) === key ? run + 1 : 1
    best = Math.max(best, run)
    prev = date
  }
  return best
}

// Entries written on this day-of-month in an earlier month or year.
export function onThisDay(entries, today = new Date()) {
  const todayKey = dayKey(today)
  return entries.filter((entry) => {
    const date = createdAt(entry)
    return date.getDate() === today.getDate() && dayKey(date) < todayKey
  })
}

export function formatDate(date) {
  return date.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
}

export function formatWeekday(date) {
  return date.toLocaleDateString(undefined, { weekday: 'long' })
}
