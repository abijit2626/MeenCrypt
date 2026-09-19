// The encrypted plaintext of a diary entry. Mood lives INSIDE this string,
// so it goes through encrypt_with_observation() with the body and is never
// stored as a separate unencrypted field.

const FORMAT = 'entry/v1'

export function encodeEntry({ body, mood }) {
  return JSON.stringify({ meencrypt: FORMAT, mood: mood || null, body })
}

// Anything that isn't our JSON envelope is a pre-redesign diary: the whole
// plaintext is the body and there's no mood.
export function decodeEntry(plaintext) {
  try {
    const parsed = JSON.parse(plaintext)
    if (parsed && parsed.meencrypt === FORMAT && typeof parsed.body === 'string') {
      return { body: parsed.body, mood: parsed.mood || null }
    }
  } catch { /* legacy plain text */ }
  return { body: plaintext, mood: null }
}

export const MOODS = [
  { id: 'calm', emoji: '😌', label: 'calm' },
  { id: 'happy', emoji: '😊', label: 'happy' },
  { id: 'excited', emoji: '🤩', label: 'excited' },
  { id: 'tired', emoji: '🥱', label: 'tired' },
  { id: 'sad', emoji: '😢', label: 'sad' },
  { id: 'anxious', emoji: '😬', label: 'anxious' },
  { id: 'angry', emoji: '😠', label: 'angry' },
  { id: 'thoughtful', emoji: '🤔', label: 'thoughtful' },
]

export function moodById(id) {
  return MOODS.find((m) => m.id === id) || null
}

// Rough Markdown -> text for card snippets and word counts.
export function stripMarkdown(md) {
  return md
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/`([^`]*)`/g, '$1')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, ' ')
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/^\s{0,3}(#{1,6}|>|[-*+]|\d+\.)\s+/gm, '')
    .replace(/[*_~]{1,3}([^*_~]+)[*_~]{1,3}/g, '$1')
    .replace(/\s+/g, ' ')
    .trim()
}

export function wordCount(md) {
  const text = stripMarkdown(md)
  return text ? text.split(' ').length : 0
}
