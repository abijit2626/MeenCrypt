// Tiny scaling helper shared by the inline-SVG line charts (FishActivityChart,
// OpsRateChart) - no charting library in this codebase (see Pipeline.jsx's
// one decorative <svg> for the closest prior art), so this is plain math,
// not a component.

// values -> an SVG `points` string, linearly scaled into
// [padding, width-padding] x [padding, height-padding]. min/max default to
// the data's own range; pass them explicitly to keep two series comparable
// on the same axes (see OpsRateChart, which shares one scale for both lines).
export function linePoints(values, { width = 240, height = 60, min, max, padding = 4 } = {}) {
  if (!values.length) return ''
  const lo = min ?? Math.min(...values)
  const hi = max ?? Math.max(...values)
  const span = hi - lo || 1
  const innerW = width - padding * 2
  const innerH = height - padding * 2
  const step = values.length > 1 ? innerW / (values.length - 1) : 0
  return values
    .map((v, i) => {
      const x = padding + i * step
      const y = padding + innerH - ((v - lo) / span) * innerH
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')
}
