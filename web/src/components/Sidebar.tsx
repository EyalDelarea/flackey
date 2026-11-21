import type { ReactNode } from 'react'
import Icon, { type IconName } from './Icon'
import SidebarMark from './SidebarMark'
import type { LosslessHealth, SharingState } from '../api'
export type Tab = 'download' | 'library' | 'uploads' | 'settings'

/* The footer is an overall availability signal. Either usable source is enough to download; Settings
   carries each source's detailed state. A saved account or an intentionally disabled source is not a
   connection, so neither can turn the dot green by itself.

   Three tones. Green is connected, red is nothing to download with, and amber is connected but with the
   Soulseek sharing port closed -- which still downloads, but cuts how many peers can answer. Amber only
   once the server has seen the port closed on two checks a minute or more apart (`closed_confirmed`):
   port tests false-alarm, and a footer that flickers amber on one bad reading teaches the owner to ignore
   it. Red outranks amber; amber and red both open Settings, where the fix is. */
type Footer = { tone: 'ok' | 'warn' | 'bad'; text: string; hint?: string }
function footer(telegramAuthorized: boolean, lossless?: LosslessHealth, sourceEnabled = true, sharing?: SharingState | null): Footer {
  const telegramConnected = sourceEnabled && telegramAuthorized
  const soulseekConnected = !!lossless?.enabled && lossless.provider?.status === 'ok'
  if (!telegramConnected && !soulseekConnected) return { tone: 'bad', text: 'Not connected' }
  if (soulseekConnected && sharing?.enabled && sharing.reachable === false && sharing.closed_confirmed) {
    return { tone: 'warn', text: 'Connected · port closed', hint: 'Fewer Soulseek results until the port opens.' }
  }
  return { tone: 'ok', text: 'Connected' }
}
const ITEMS: { tab: Tab; label: string; icon: IconName }[] = [
  { tab: 'download', label: 'Download', icon: 'download' }, { tab: 'library', label: 'Library', icon: 'library' },
  { tab: 'uploads', label: 'Uploads', icon: 'upload' }, { tab: 'settings', label: 'Settings', icon: 'settings' }]
export default function Sidebar({ tab, onTab, telegramAuthorized, lossless, sharing, inset, extra, sourceEnabled = true, updateWaiting = false, onReport }: { tab: Tab; onTab: (t: Tab) => void; telegramAuthorized: boolean; lossless?: LosslessHealth; sharing?: SharingState | null; inset: boolean; extra?: ReactNode; sourceEnabled?: boolean; updateWaiting?: boolean; onReport?: () => void }) {
  const status = footer(telegramAuthorized, lossless, sourceEnabled, sharing)
  return (
    <nav className={`sidebar${inset ? ' inset' : ''}`}>
      {inset && <div className="titlebar-strip" aria-hidden />}
      {/* The dot is what is left once the banner is waved away: the update is still waiting, and the
          owner should be able to find it again without remembering which page it was on. */}
      <div className="nav">{ITEMS.map(i => <button key={i.tab} className={`nav-item${tab === i.tab ? ' active' : ''}`} aria-current={tab === i.tab ? 'page' : undefined} onClick={() => onTab(i.tab)}><Icon name={i.icon} />{i.label}
        {i.tab === 'settings' && updateWaiting && <span className="nav-badge" title="An update is available" />}</button>)}</div>
      {extra}
      <SidebarMark />
      {/* Always here, not only after something visibly fails: the flicker in #31 never showed an error. */}
      {onReport && <button className="sidebar-report" onClick={onReport}><Icon name="flag" size={13} />Report a bug</button>}
      {status.tone === 'ok'
        ? <div className="sidebar-footer"><div className="status-line"><span className="status-dot" />{status.text}</div></div>
        : <button className={`sidebar-footer ${status.tone}`} onClick={() => onTab('settings')}
            title={status.tone === 'bad' ? 'Open Settings to check connections' : 'Open Settings to open the port'}>
            <span className="status-line"><span className={`status-dot ${status.tone === 'bad' ? 'red' : 'amber'}`} />{status.text}</span>
            {status.hint && <span className="status-hint">{status.hint}</span>}
          </button>}
    </nav>
  )
}
