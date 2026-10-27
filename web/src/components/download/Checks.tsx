import Icon from '../Icon'
import type { CheckView } from '../../presentation'

/** The evidence about the file. The fingerprint score answers "is this actually the
 *  recording we asked for"; the spectrum says where the content stops, which is evidence about the source rather
 *  than proof of it (issue #97). Both already ride in the bundle. */
export default function Checks({ checks }: { checks: CheckView[] }) {
  if (!checks.length) return null
  return (
    <div className="checks">
      {checks.map(c => (
        <span key={c.label} className={`check${c.ok === null ? ' plain' : c.ok ? '' : ' bad'}`} title={c.title}>
          {c.ok !== null && <Icon name={c.ok ? 'check' : 'x'} size={11} stroke={2.4} />}
          {c.label} <b>{c.value}</b>
        </span>
      ))}
    </div>
  )
}
