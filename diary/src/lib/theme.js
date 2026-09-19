// Light / dark theme. Only this display preference is kept in
// localStorage - never key material or diary content.
import { useEffect, useState } from 'react'

const STORAGE_KEY = 'meencrypt-theme'
export const THEME_PREFS = ['light', 'dark']
const DEFAULT_THEME = 'light'

export function getStoredTheme() {
  try {
    const value = localStorage.getItem(STORAGE_KEY)
    return THEME_PREFS.includes(value) ? value : DEFAULT_THEME
  } catch {
    return DEFAULT_THEME
  }
}

function storeTheme(pref) {
  try {
    if (pref === DEFAULT_THEME) localStorage.removeItem(STORAGE_KEY)
    else localStorage.setItem(STORAGE_KEY, pref)
  } catch { /* storage blocked - theme just won't persist */ }
}

export function useTheme() {
  const [pref, setPref] = useState(getStoredTheme)

  useEffect(() => {
    document.documentElement.dataset.theme = pref
    storeTheme(pref)
  }, [pref])

  return [pref, setPref]
}
