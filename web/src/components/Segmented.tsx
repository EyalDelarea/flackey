/** A Mac segmented control for a single choice: a radio group, so a screen reader announces "1 of 3,
 *  selected" rather than three unrelated toggle buttons. Pressing the chosen segment again does nothing. */
export default function Segmented<T extends string>({ label, options, value, onChange, disabled = false, busy = false }: {
  label: string; options: readonly (readonly [T, string])[]; value: T; onChange: (v: T) => void; disabled?: boolean; busy?: boolean
}) {
  return <div className={`segmented${busy ? ' busy' : ''}`} role="radiogroup" aria-label={label} aria-busy={busy || undefined}>
    {options.map(([key, name]) =>
      <button key={key} type="button" role="radio" aria-checked={key === value} disabled={disabled || busy}
        onClick={() => key !== value && onChange(key)}>{name}</button>)}
  </div>
}
