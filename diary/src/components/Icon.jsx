// Small hand-drawn line-icon set - no emoji anywhere in this app, no icon
// library dependency. Mirrors dashboard/src/components/Icon.jsx (same
// viewBox/stroke conventions, and the shared glyphs - fish, key, warning,
// lockOpen/lockClosed, vault, refresh, barChart - are the identical paths)
// so the diary and dashboard read as one product. Everything renders in
// `currentColor`, so an icon picks up whatever color/size its surrounding
// text has.
const PATHS = {
  fish: (
    <path
      fill="currentColor"
      stroke="none"
      d="M2.5 12C2.5 7.8 7 4.6 12 4.6c4 0 7.2 2.1 9 4.9.8-1.6 2-3 2.8-3-.6 1.9-1 3.6-1 5.5s.4 3.6 1 5.5c-.8 0-2-1.4-2.8-3-1.8 2.8-5 4.9-9 4.9-5 0-9.5-3.2-9.5-7.4z"
    />
  ),
  key: (
    <>
      <circle cx="7.5" cy="14.5" r="3.8" />
      <path d="M10.2 11.8 19.5 2.5M14.2 6.5l2.8 2.8M17.3 3.4l2.8 2.8" />
    </>
  ),
  warning: (
    <>
      <path d="M12 3 22.2 20.5H1.8L12 3z" />
      <path d="M12 9.5v5" />
      <circle cx="12" cy="17.6" r="0.9" fill="currentColor" stroke="none" />
    </>
  ),
  lockOpen: (
    <>
      <rect x="4" y="11" width="16" height="9.5" rx="2" />
      <path d="M8 11V7.8a4 4 0 0 1 7.6-1.7" />
    </>
  ),
  lockClosed: (
    <>
      <rect x="4" y="11" width="16" height="9.5" rx="2" />
      <path d="M8 11V7.8a4 4 0 0 1 8 0V11" />
    </>
  ),
  vault: (
    <>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <circle cx="12" cy="12" r="3.2" />
      <path d="M12 8.8v1.2M12 14v1.2M8.8 12H10M14 12h1.2" />
    </>
  ),
  refresh: (
    <>
      <path d="M4 4.5v5h5" />
      <path d="M20 19.5v-5h-5" />
      <path d="M5.1 9.5a7.5 7.5 0 0 1 12.6-3.8l2.3 2.3" />
      <path d="M18.9 14.5a7.5 7.5 0 0 1-12.6 3.8l-2.3-2.3" />
    </>
  ),
  barChart: <path d="M4 21V10.5M12 21V3.5M20 21v-8" />,
  calendar: (
    <>
      <rect x="3" y="5" width="18" height="16" rx="2" />
      <path d="M3 9.5h18M8 3v4M16 3v4" />
    </>
  ),
  tag: (
    <>
      <path d="M11.4 3.5H5.5A2 2 0 0 0 3.5 5.5v5.9c0 .5.2 1 .6 1.4l9 9a2 2 0 0 0 2.8 0l5.9-5.9a2 2 0 0 0 0-2.8l-9-9a2 2 0 0 0-1.4-.6z" />
      <circle cx="8" cy="8.5" r="1.4" fill="currentColor" stroke="none" />
    </>
  ),
  archive: (
    <>
      <rect x="3" y="4" width="18" height="5" rx="1.2" />
      <path d="M4.5 9V19a1.5 1.5 0 0 0 1.5 1.5h12A1.5 1.5 0 0 0 19.5 19V9" />
      <path d="M10 13h4" />
    </>
  ),
  trash: (
    <>
      <path d="M4.5 7h15M9.5 7V4.8a1 1 0 0 1 1-1h3a1 1 0 0 1 1 1V7" />
      <path d="M6.5 7 7.3 19a1.5 1.5 0 0 0 1.5 1.4h6.4a1.5 1.5 0 0 0 1.5-1.4L17.5 7" />
      <path d="M10.3 11v6M13.7 11v6" />
    </>
  ),
  pin: (
    <>
      <path d="M12 21s7-6.5 7-11.5A7 7 0 0 0 5 9.5C5 14.5 12 21 12 21z" />
      <circle cx="12" cy="9.5" r="2.4" />
    </>
  ),
  close: <path d="M5 5l14 14M19 5 5 19" />,
  undo: (
    <>
      <path d="M4 10h9.5a5.5 5.5 0 0 1 0 11H11" />
      <path d="M8.5 5.5 4 10l4.5 4.5" />
    </>
  ),
  folder: (
    <path d="M3 6.5A1.5 1.5 0 0 1 4.5 5H9l2 2.5h8A1.5 1.5 0 0 1 20.5 9V18a1.5 1.5 0 0 1-1.5 1.5H4.5A1.5 1.5 0 0 1 3 18z" />
  ),
  clipboard: (
    <>
      <rect x="6" y="4.5" width="12" height="16" rx="2" />
      <rect x="9" y="2.8" width="6" height="3.4" rx="1" />
      <path d="M9 11h6M9 14.3h6M9 17.6h3.5" />
    </>
  ),
  save: (
    <>
      <path d="M5 3.5h11.5l4 4V19a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 19V5A1.5 1.5 0 0 1 5 3.5z" />
      <path d="M7.5 3.5v5h7v-5" />
      <path d="M7.5 20v-6h9v6" />
    </>
  ),
  sparkle: (
    <path
      fill="currentColor"
      stroke="none"
      d="M12 2.5c.65 3.85 2.35 5.55 6.2 6.2-3.85.65-5.55 2.35-6.2 6.2-.65-3.85-2.35-5.55-6.2-6.2 3.85-.65 5.55-2.35 6.2-6.2z"
    />
  ),
  package: (
    <>
      <path d="M12 3 20.5 7.5V16.5L12 21 3.5 16.5V7.5z" />
      <path d="M3.5 7.5 12 12l8.5-4.5M12 12v9" />
    </>
  ),
  upload: (
    <>
      <path d="M12 15.5V4M8 8l4-4 4 4" />
      <path d="M4.5 15.5V18a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2v-2.5" />
    </>
  ),
  download: (
    <>
      <path d="M12 4v11.5M8 11.5l4 4 4-4" />
      <path d="M4.5 16v2.5a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2V16" />
    </>
  ),
  plug: (
    <>
      <path d="M9 3v5.5M15 3v5.5" />
      <path d="M6.5 8.5h11V12a5.5 5.5 0 0 1-11 0V8.5z" />
      <path d="M12 17.5v3.2" />
    </>
  ),
  sun: (
    <>
      <circle cx="12" cy="12" r="4.2" />
      <path d="M12 2.8v2.6M12 18.6v2.6M4.2 12H6.8M17.2 12h2.6M6 6l1.8 1.8M16.2 16.2 18 18M18 6l-1.8 1.8M7.8 16.2 6 18" />
    </>
  ),
  moon: <path d="M20 14.2A8.2 8.2 0 0 1 9.8 4 8.6 8.6 0 1 0 20 14.2z" />,
  search: (
    <>
      <circle cx="10.5" cy="10.5" r="6.5" />
      <path d="M20 20l-4.8-4.8" />
    </>
  ),
}

export default function Icon({ name, size = 15, className = '' }) {
  const glyph = PATHS[name]
  if (!glyph) return null
  return (
    <svg
      className={`icon ${className}`}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {glyph}
    </svg>
  )
}
