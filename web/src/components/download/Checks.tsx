import type { ReactNode } from 'react'
import HelpTag from '../HelpTag'
import Icon from '../Icon'
import type { CheckView } from '../../presentation'

/** The evidence about the file. The fingerprint score answers "is this actually the
 *  recording we asked for"; the spectrum says where the content stops, which is evidence about the source rather
 *  than proof of it (issue #97). Both already ride in the bundle. */
export default function Checks({ checks, after }: { checks: CheckView[]
  /** The rest of the line after the checks: a History row's file format, in grey. */
  after?: ReactNode }) {
  if (!checks.length && !after) return null
  return (
    <div className="checks">
      {checks.map(c => {
        // The reason rides on the app's own help tag: the Mac window (WKWebView) shows no `title` tooltip
        const check = <span key={c.label} className={`check${c.ok === null ? ' plain' : c.ok ? '' : ' bad'}`}>
          {c.ok !== null && <Icon name={c.ok ? 'check' : 'x'} size={11} stroke={2.4} />}
          {c.label} <b>{c.value}</b>
        </span>
        return c.title ? <HelpTag key={c.label} text={c.title}>{check}</HelpTag> : check
      })}
      {after}
    </div>
  )
}
