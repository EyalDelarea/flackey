/** How new tracks are foldered inside the library: the server's `library_layout`. "month" and "day" are
 *  one choice on screen -- By date -- with the period picked underneath, because to the owner they are
 *  one idea ("sort by when I got it") at two sizes, not two layouts. */
export type Layout = 'artist' | 'month' | 'day' | 'flat'

const CHOICES = [
  { key: 'artist', name: 'By artist', example: 'Bicep/Bicep - Glue.aiff' },
  { key: 'date', name: 'By date', example: '' },
  { key: 'flat', name: 'One folder', example: 'Bicep - Glue.aiff' },
] as const

/** The folder name a date layout would give a track filed today, in the server's ISO form. */
export function dateFolder(layout: 'month' | 'day', today = new Date()): string {
  const y = today.getFullYear(), m = String(today.getMonth() + 1).padStart(2, '0'), d = String(today.getDate()).padStart(2, '0')
  return layout === 'month' ? `${y}-${m}` : `${y}-${m}-${d}`
}

export function layoutSummary(layout: string): string {
  if (layout === 'month') return 'New tracks go into a folder for the month they were downloaded.'
  if (layout === 'day') return 'New tracks go into a folder for the day they were downloaded.'
  if (layout === 'flat') return 'New tracks go straight into the library folder.'
  return 'New tracks go into a folder named after the artist.'
}

export default function LayoutOptions({ value, onChange, disabled = false, today }: {
  value: string; onChange: (layout: Layout) => void; disabled?: boolean; today?: Date
}) {
  const byDate = value === 'month' || value === 'day'
  const period = value === 'day' ? 'day' : 'month'
  const selectedKey = byDate ? 'date' : value
  return (<>
    <fieldset className="format-options" aria-label="Folder layout">
      {CHOICES.map(c => {
        const selected = c.key === selectedKey
        const example = c.key === 'date' ? `${dateFolder(period, today)}/Bicep - Glue.aiff` : c.example
        return <button key={c.key} type="button" className={`format-choice${selected ? ' selected' : ''}`}
          aria-pressed={selected} disabled={disabled}
          onClick={() => onChange(c.key === 'date' ? period : c.key)}>
          <span className="format-name">{c.name}</span>
          {c.key === 'artist' && <span className="format-badge">Default</span>}
          <span className="format-note mono">{example}</span>
        </button>
      })}
    </fieldset>
    {byDate && <div className="layout-period" role="group" aria-label="New folder every">
      <span className="layout-period-label">New folder every</span>
      {(['month', 'day'] as const).map(p =>
        <button key={p} type="button" className="chip" aria-pressed={p === period} disabled={disabled}
          onClick={() => onChange(p)}>{p === 'month' ? 'Month' : 'Day'}</button>)}
    </div>}
  </>)
}
