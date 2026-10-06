/** An on/off setting. A switch rather than a "Turn off" button, because a button labelled with the
 *  action leaves the owner guessing whether it names the current state or the one a press would give.
 *  `busy` pulses the thumb while a save is in flight, so a press that has not landed yet still shows. */
export default function Switch({ label, checked, onChange, busy = false, disabled = false }: {
  label: string; checked: boolean; onChange: (next: boolean) => void; busy?: boolean; disabled?: boolean
}) {
  return <button type="button" role="switch" aria-checked={checked} aria-label={label} aria-busy={busy || undefined}
    className={`switch${busy ? ' busy' : ''}`} disabled={disabled || busy} onClick={() => onChange(!checked)}>
    <span className="switch-thumb" />
  </button>
}
