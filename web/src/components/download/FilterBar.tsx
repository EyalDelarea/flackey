import type { Bucket } from '../../presentation'

interface Chip { key: Bucket | 'all'; label: string; tone?: 'amber' | 'red' }
const CHIPS: Chip[] = [
  { key: 'all', label: 'All' },
  { key: 'progress', label: 'In progress' },
  { key: 'needs', label: 'Needs you', tone: 'amber' },
  { key: 'done', label: 'Done' },
  { key: 'failed', label: 'Failed', tone: 'red' },
]

interface Props {
  filter: Bucket | 'all'
  counts: Record<Bucket | 'all', number>
  onFilter: (f: Bucket | 'all') => void
  onClearFailed: () => void
  view?: 'active' | 'history' | 'failed'
}

export default function FilterBar({ filter, counts, onFilter, onClearFailed, view }: Props) {
  return (
    <div className="filterbar">
      {CHIPS.filter(c => !view || c.key === 'all' || (view === 'active' ? c.key === 'progress' || c.key === 'needs' : c.key === 'done' || c.key === 'failed')).map(c => {
        const count = counts[c.key]
        const toned = c.tone && count > 0 ? ` ${c.tone}` : ''
        return (
          <button key={c.key} className="chip" aria-pressed={filter === c.key} onClick={() => onFilter(c.key)}>
            {c.label} <span className={`count${toned}`}>{count}</span>
          </button>
        )
      })}
      {view !== 'active' && <button className="btn-secondary" disabled={counts.failed === 0} onClick={onClearFailed}>Clear failed</button>}
    </div>
  )
}
