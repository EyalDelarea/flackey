import { useState } from 'react'
import Segmented from './Segmented'
import UsageStatsCatalog from './UsageStatsCatalog'

export type UsageStatsChoice = 'share' | 'not-now'

/* The one wording for the question, on the Welcome step and in the one-time prompt an existing owner sees.
   It promises only what is true of every event: counts and outcomes, never what was downloaded -- so it
   never says (or implies) that a failed song can be picked out. */
export const ASK_TITLE = 'Share usage stats?'
export const ASK_TEXT = 'Help us find out why downloads fail and make Flackey better over time. We can’t see what you '
  + 'download: we only get counts like “a Soulseek search found nothing”, never song names, links, files or your IP.'
export const ASK_SETTINGS_LINE = 'You can change this any time in Settings › Privacy.'
const OPTIONS = [['share', 'Share usage stats'], ['not-now', 'Not now']] as const

/** The usage-stats question: two equal answers, neither chosen until the owner picks one, and the full list
 *  of what can be sent one click away. `value` is null until then. */
export default function UsageStatsAsk({ value, onChoose, onDismiss, className = '' }: {
  value: UsageStatsChoice | null; onChoose: (c: UsageStatsChoice) => void; onDismiss?: () => void; className?: string
}) {
  const [catalogOpen, setCatalogOpen] = useState(false)
  return (<div className={`optin-card ${className}`.trim()} role="group" aria-labelledby="usage-ask-title">
    <div className="optin-head">
      <div className="optin-title" id="usage-ask-title">{ASK_TITLE}</div>
      {onDismiss && <button type="button" className="banner-dismiss" aria-label="Dismiss" onClick={onDismiss}>×</button>}
    </div>
    <p className="optin-text">{ASK_TEXT}</p>
    <div className="optin-actions">
      <Segmented label="Usage stats" options={OPTIONS} value={value} onChange={onChoose} />
      <button type="button" className="btn-link" onClick={() => setCatalogOpen(true)}>What's shared?</button>
    </div>
    <p className="optin-foot">{ASK_SETTINGS_LINE}</p>
    {catalogOpen && <UsageStatsCatalog onClose={() => setCatalogOpen(false)} />}
  </div>)
}
