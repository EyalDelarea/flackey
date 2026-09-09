import Icon from '../Icon'
import type { CheckView } from '../../presentation'

/** The evidence that the file is the real thing. The fingerprint score answers "is this actually the
 *  recording we asked for"; the cutoff answers "is the audio genuine". Both already ride in the bundle. */
export default function Checks({ checks }: { checks: CheckView[] }) {
  if (!checks.length) return null
  return (
    <div className="checks">
      {checks.map(c => (
        <span key={c.label} className={`check${c.ok ? '' : ' bad'}`}>
          <Icon name={c.ok ? 'check' : 'x'} size={11} stroke={2.4} />
          {c.label} <b>{c.value}</b>
        </span>
      ))}
    </div>
  )
}
