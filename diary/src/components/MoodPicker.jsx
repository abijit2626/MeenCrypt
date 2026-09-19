import { MOODS } from '../lib/payload'

export default function MoodPicker({ value, onChange }) {
  return (
    <div className="mood-picker" role="radiogroup" aria-label="Mood">
      {MOODS.map((mood) => (
        <button
          key={mood.id}
          type="button"
          role="radio"
          aria-checked={value === mood.id}
          className={`mood-chip ${value === mood.id ? 'active' : ''}`}
          onClick={() => onChange(value === mood.id ? null : mood.id)}
          title={mood.label}
        >
          <span aria-hidden>{mood.emoji}</span> {mood.label}
        </button>
      ))}
    </div>
  )
}
