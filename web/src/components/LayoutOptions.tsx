/** How new tracks are foldered inside the library: the server's `library_layout`. "month" and "day" are
 *  one choice on screen -- By date -- with the period picked beside it, because to the owner they are one
 *  idea ("sort by when I got it") at two sizes, not two layouts. A segmented control rather than cards:
 *  the choices differ by one folder level, which the example path under it shows better than a paragraph
 *  per option would. */
export type Layout = 'artist' | 'month' | 'day' | 'flat'

/** The folder name a date layout would give a track filed today, in the server's ISO form. */
export function dateFolder(layout: 'month' | 'day', today = new Date()): string {
  const y = today.getFullYear(), m = String(today.getMonth() + 1).padStart(2, '0'), d = String(today.getDate()).padStart(2, '0')
  return layout === 'month' ? `${y}-${m}` : `${y}-${m}-${d}`
}

/** Where a track lands under `layout`, as the owner would see it in Finder. */
export function examplePath(layout: string, today?: Date): string {
  if (layout === 'month' || layout === 'day') return `${dateFolder(layout, today)}/Bicep - Glue.aiff`
  if (layout === 'flat') return 'Bicep - Glue.aiff'
  return 'Bicep/Bicep - Glue.aiff'
}

function Segmented<T extends string>({ label, options, value, onChange, disabled }: {
  label: string; options: readonly (readonly [T, string])[]; value: T; onChange: (v: T) => void; disabled: boolean
}) {
  return <div className="segmented" role="radiogroup" aria-label={label}>
    {options.map(([key, name]) =>
      <button key={key} type="button" role="radio" aria-checked={key === value} disabled={disabled}
        onClick={() => key !== value && onChange(key)}>{name}</button>)}
  </div>
}

const LAYOUTS = [['artist', 'By artist'], ['date', 'By date'], ['flat', 'One folder']] as const
const PERIODS = [['month', 'Month'], ['day', 'Day']] as const

export default function LayoutOptions({ value, onChange, disabled = false, today }: {
  value: string; onChange: (layout: Layout) => void; disabled?: boolean; today?: Date
}) {
  const byDate = value === 'month' || value === 'day'
  const period = value === 'day' ? 'day' : 'month'
  return (<div className="layout-options">
    <div className="layout-controls">
      <Segmented label="Folder layout" options={LAYOUTS} value={byDate ? 'date' : value as 'artist' | 'flat'}
        onChange={k => onChange(k === 'date' ? period : k)} disabled={disabled} />
      {byDate && <Segmented label="New folder every" options={PERIODS} value={period} onChange={onChange} disabled={disabled} />}
    </div>
    <div className="v mono layout-example">{examplePath(value, today)}</div>
  </div>)
}
