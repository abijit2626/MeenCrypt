// Small hand-drawn line-icon set - no emoji anywhere in this app, no icon
// library dependency. Everything renders in `currentColor`, so an icon
// picks up whatever color/size its surrounding text has (see .panel h2,
// .tag, .nav-btn in App.css). `fish` is a filled silhouette (reused at a
// larger size for the swimming background in App.jsx); every other icon
// is a plain stroke glyph, viewBox 0 0 24 24.
const PATHS = {
  fish: (
    <path
      fill="currentColor"
      stroke="none"
      d="M2.5 12C2.5 7.8 7 4.6 12 4.6c4 0 7.2 2.1 9 4.9.8-1.6 2-3 2.8-3-.6 1.9-1 3.6-1 5.5s.4 3.6 1 5.5c-.8 0-2-1.4-2.8-3-1.8 2.8-5 4.9-9 4.9-5 0-9.5-3.2-9.5-7.4z"
    />
  ),
  gear: (
    <>
      <circle cx="12" cy="12" r="3.2" />
      <path d="M12 2.5v3.2M12 18.3v3.2M4.2 4.2l2.3 2.3M17.5 17.5l2.3 2.3M2.5 12h3.2M18.3 12h3.2M4.2 19.8l2.3-2.3M17.5 6.5l2.3-2.3" />
    </>
  ),
  mic: (
    <>
      <rect x="9" y="2.5" width="6" height="11" rx="3" />
      <path d="M5.2 11a6.8 6.8 0 0 0 13.6 0" />
      <path d="M12 17.8v3.2M9 21h6" />
    </>
  ),
  key: (
    <>
      <circle cx="7.5" cy="14.5" r="3.8" />
      <path d="M10.2 11.8 19.5 2.5M14.2 6.5l2.8 2.8M17.3 3.4l2.8 2.8" />
    </>
  ),
  shield: <path d="M12 2.8 19 5.8v5.6c0 4.9-3 8.2-7 9.8-4-1.6-7-4.9-7-9.8V5.8L12 2.8z" />,
  pulse: <path d="M2.5 12.5h4l2-7.5 3.5 15 2-7.5h7.5" />,
  warning: (
    <>
      <path d="M12 3 22.2 20.5H1.8L12 3z" />
      <path d="M12 9.5v5" />
      <circle cx="12" cy="17.6" r="0.9" fill="currentColor" stroke="none" />
    </>
  ),
  target: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <circle cx="12" cy="12" r="4.5" />
      <circle cx="12" cy="12" r="1" fill="currentColor" stroke="none" />
    </>
  ),
  trendingUp: (
    <>
      <path d="M2.5 17.5 9 11l4 4 8.5-8.5" />
      <path d="M15.5 6h6v6" />
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
  camera: (
    <>
      <rect x="2.5" y="7" width="19" height="13.5" rx="2.2" />
      <path d="M8.2 7 10 4.2h4L15.8 7" />
      <circle cx="12" cy="13.7" r="3.7" />
    </>
  ),
  wrench: <path d="M14.9 6.3a4.2 4.2 0 0 0-5.6 5.6L3 18.2l2.8 2.8 6.3-6.3a4.2 4.2 0 0 0 5.6-5.6l-2.7 2.7-2.1-2.1 2.7-2.7z" />,
  barChart: <path d="M4 21V10.5M12 21V3.5M20 21v-8" />,
  info: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 10.8v5.7" />
      <circle cx="12" cy="7.3" r="0.9" fill="currentColor" stroke="none" />
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
