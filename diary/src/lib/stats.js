// Stats over the in-memory decrypted entries only. Nothing here is ever
// persisted; the numbers vanish when the vault locks.
import { createdAt, longestStreak } from './calendar'
import { moodById, stripMarkdown, wordCount } from './payload'

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

function topKey(counts) {
  let best = null
  for (const [key, n] of counts) if (best === null || n > counts.get(best)) best = key
  return best
}

function tally(values) {
  const counts = new Map()
  for (const v of values) if (v != null) counts.set(v, (counts.get(v) || 0) + 1)
  return counts
}

function formatHour(hour) {
  if (hour == null) return '—'
  const suffix = hour < 12 ? 'am' : 'pm'
  return `${hour % 12 || 12}${suffix}`
}

// `items` is [{ entry, content: { body, mood } }].
export function computeStats(items) {
  const rows = items.map(({ entry, content }) => ({
    entry,
    content,
    date: createdAt(entry),
    words: wordCount(content.body),
  }))
  const totalWords = rows.reduce((sum, r) => sum + r.words, 0)
  const longest = rows.reduce((best, r) => (!best || r.words > best.words ? r : best), null)
  const hour = topKey(tally(rows.map((r) => r.date.getHours())))

  const years = new Map()
  for (const r of rows) {
    const year = r.date.getFullYear()
    if (!years.has(year)) years.set(year, [])
    years.get(year).push(r)
  }
  const wrapped = [...years.entries()]
    .sort(([a], [b]) => b - a)
    .map(([year, yearRows]) => {
      const month = topKey(tally(yearRows.map((r) => r.date.getMonth())))
      const mood = moodById(topKey(tally(yearRows.map((r) => r.content.mood))))
      return {
        year,
        entries: yearRows.length,
        words: yearRows.reduce((sum, r) => sum + r.words, 0),
        bestMonth: month == null ? '—' : MONTHS[month],
        topMood: mood,
        longestStreak: longestStreak(yearRows.map((r) => r.entry)),
      }
    })

  return {
    entries: rows.length,
    totalWords,
    averageWords: rows.length ? Math.round(totalWords / rows.length) : 0,
    longest: longest && {
      id: longest.entry.id,
      words: longest.words,
      date: longest.date,
      snippet: stripMarkdown(longest.content.body).slice(0, 120),
    },
    activeHour: formatHour(hour),
    wrapped,
  }
}
