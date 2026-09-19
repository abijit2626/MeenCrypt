import EntryCard from '../components/EntryCard'

export default function EntryGrid({ entries, highlightId, onOpen, renderActions, empty }) {
  if (!entries.length) return <p className="empty">{empty}</p>
  return (
    <div className="grid">
      {entries.map((entry) => (
        <EntryCard
          key={entry.id}
          entry={entry}
          highlighted={entry.id === highlightId}
          onOpen={onOpen}
          actions={renderActions?.(entry)}
        />
      ))}
    </div>
  )
}
