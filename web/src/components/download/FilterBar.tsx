import { selectionKeys } from './selectionKeys'
import type { Filter } from '../../presentation'

type View = 'active' | 'history' | 'failed'
interface Chip {
  key: Filter; label: string; tone?: 'amber' | 'red' | 'purple'; views: View[]
  /** Behind a 1px rule. Waiting is not a rung of the pipeline, it is "not working right now", and reading
   *  it in line with the stages it sits between claims a progression that is not happening. */
  divided?: boolean
  /** Left out at zero. On a fresh database `Unknown` and `Choose` are always zero, and a permanent
   *  `Unknown 0` is noise in a bar whose whole job is saying where things went. */
  hideAtZero?: boolean
}
/* One table, both tabs, one vocabulary -- the same `Stage` keys read at two points in time. Only the words
   differ: on the active tab a stage is what a track is doing ("Searching"), on the Failed tab it is where
   it stopped ("Search"). Active order is pipeline order, so the chips read as the ladder the rows draw. */
const CHIPS: Chip[] = [
  { key: 'all', label: 'All', views: ['active', 'history', 'failed'] },
  { key: 'search', label: 'Searching', views: ['active'] },
  { key: 'choose', label: 'Needs you', tone: 'amber', views: ['active'] },
  { key: 'download', label: 'Downloading', views: ['active'] },
  { key: 'verify', label: 'Verifying', views: ['active'] },
  { key: 'waiting', label: 'Waiting', tone: 'amber', views: ['active'], divided: true },
  { key: 'search', label: 'Search', tone: 'amber', views: ['failed'] },
  { key: 'choose', label: 'Choose', tone: 'amber', views: ['failed'], hideAtZero: true },
  { key: 'download', label: 'Download', tone: 'red', views: ['failed'] },
  { key: 'verify', label: 'Verify', tone: 'purple', views: ['failed'] },
  { key: 'stopped', label: 'Stopped', views: ['failed'] },
  { key: 'unknown', label: 'Unknown', views: ['failed'], hideAtZero: true },
  { key: 'done', label: 'Done', views: ['history'] },
  { key: 'failed', label: 'Failed', tone: 'red', views: ['history'] },
]

interface Props {
  filter: Filter
  counts: Record<Filter, number>
  onFilter: (f: Filter) => void
  onClearFailed: () => void
  view: View
  /** Render the chips as a row inside an existing `.filterbar`, rather than as a bar of their own. The
   *  Failed tab holds its chips and its "Retry all" sentence in one block; two filter bars stacked on one
   *  tab is what this row of chips exists to avoid. */
  bare?: boolean
}

export default function FilterBar({ filter, counts, onFilter, onClearFailed, view, bare }: Props) {
  const chips = CHIPS.filter(c => c.views.includes(view) && !(c.hideAtZero && counts[c.key] === 0 && filter !== c.key))
  return (
    <div className={bare ? 'filterbar-row' : 'filterbar'}>
      <span className="filter-scope">{view === 'active' ? 'Downloads' : view === 'failed' ? 'Failed' : 'History'} · {counts.all} tracks</span>
      <div className="stage-filters" role="radiogroup" aria-label={`Filter ${view === 'active' ? 'downloads' : view}${view === 'history' ? ' by outcome' : ' by stage'}`} onKeyDown={selectionKeys}>
      {chips.map(c => {
        const count = counts[c.key]
        const toned = c.tone && count > 0 ? ` ${c.tone}` : ''
        return (
          <span className="stage-option" key={c.label}>
            {c.divided && <span className="chip-divider" aria-hidden="true" />}
            <button className="chip" role="radio" aria-checked={filter === c.key} tabIndex={filter === c.key ? 0 : -1} onClick={() => onFilter(c.key)}>
              {c.label} <span className={`count${toned}`}>{count}</span>
            </button>
          </span>
        )
      })}
      </div>
      {view !== 'active' && <button className="btn-secondary" disabled={counts.failed === 0} onClick={onClearFailed}>Clear failed</button>}
    </div>
  )
}
