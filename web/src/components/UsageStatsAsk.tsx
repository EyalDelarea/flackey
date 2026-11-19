import { useState } from 'react'
import Segmented from './Segmented'
import UsageStatsCatalog from './UsageStatsCatalog'

export type UsageStatsChoice = 'share' | 'not-now'

/* The one wording for the question, on the Welcome step and in the one-time prompt an existing owner sees,
   in the owner's own voice (approved 2026-11-18). It promises only what is true of every event: counts and
   outcomes, never what was downloaded -- so it never says (or implies) that a failed song can be picked out. */
export const ASK_TITLE = 'Hey! Eyal here 👋'
export const ASK_TEXT = 'I’m adding usage stats to Flackey so I can see where downloads fail and fix them faster. I '
  + 'never collect anything sensitive: no song names, links or files, just counts like “a Soulseek search found '
  + 'nothing”. My stats server never stores your IP address.'
export const ASK_PLEA = 'If you’re OK with it, please opt in. It really helps me improve Flackey as we go. Thank you! 🙏'
/* The website's privacy page. A `target="_blank"` link: pywebview opens those in the system browser
   (OPEN_EXTERNAL_LINKS_IN_BROWSER, on by default), as the Telegram step's link does. */
export const PRIVACY_URL = 'https://eyaldelarea.github.io/flackey/privacy.html'
/* The detail behind "never stores or logs your IP address", for Settings › Privacy and "What's shared?". The
   collector reads the address only to rate-limit, hashed in memory under a key replaced hourly; the edge in front
   of it keeps addresses and headers. The ID and secret travel only in the request body, never in the URL or a
   header, so nothing the edge logs carries them. */
export const IP_NOTE = 'Flackey’s stats server never stores or logs your IP address. Like most websites, the '
  + 'network in front of it (Cloudflare and our hosting firewall) sees IP addresses to block attacks, keeps them '
  + 'for up to 7 days, and keeps a short list of the busiest addresses for up to 400 days. Your ID is never in '
  + 'the web address or headers those logs keep, and we don’t link those logs to your stats.'
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
    <p className="optin-text">{ASK_PLEA}</p>
    <div className="optin-actions">
      <Segmented label="Usage stats" options={OPTIONS} value={value} onChange={onChoose} />
      <button type="button" className="btn-link" onClick={() => setCatalogOpen(true)}>What's shared?</button>
      <a className="btn-link" href={PRIVACY_URL} target="_blank" rel="noreferrer">Privacy page</a>
    </div>
    <p className="optin-foot">{ASK_SETTINGS_LINE}</p>
    {catalogOpen && <UsageStatsCatalog onClose={() => setCatalogOpen(false)} />}
  </div>)
}
