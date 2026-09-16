import type { ReactNode } from 'react'
import Icon, { type IconName } from './Icon'
import SidebarMark from './SidebarMark'
import type { LosslessHealth } from '../api'
export type Tab = 'download' | 'library' | 'uploads' | 'settings'

/* The footer is an overall availability signal. Either usable source is enough to download; Settings
   carries each source's detailed state. A saved account or an intentionally disabled source is not a
   connection, so neither can turn the dot green by itself. */
function footer(telegramAuthorized: boolean, lossless?: LosslessHealth, sourceEnabled = true): { connected: boolean; text: string } {
  const telegramConnected = sourceEnabled && telegramAuthorized
  const soulseekConnected = !!lossless?.enabled && lossless.provider?.status === 'ok'
  const connected = telegramConnected || soulseekConnected
  return { connected, text: connected ? 'Connected' : 'Not connected' }
}
const ITEMS: { tab: Tab; label: string; icon: IconName }[] = [
  { tab: 'download', label: 'Download', icon: 'download' }, { tab: 'library', label: 'Library', icon: 'library' },
  { tab: 'uploads', label: 'Uploads', icon: 'upload' }, { tab: 'settings', label: 'Settings', icon: 'settings' }]
export default function Sidebar({ tab, onTab, telegramAuthorized, lossless, inset, extra, sourceEnabled = true }: { tab: Tab; onTab: (t: Tab) => void; telegramAuthorized: boolean; lossless?: LosslessHealth; inset: boolean; extra?: ReactNode; sourceEnabled?: boolean }) {
  const status = footer(telegramAuthorized, lossless, sourceEnabled)
  return (
    <nav className={`sidebar${inset ? ' inset' : ''}`}>
      {inset && <div className="drag-strip pywebview-drag-region" />}
      <div className="nav">{ITEMS.map(i => <button key={i.tab} className={`nav-item${tab === i.tab ? ' active' : ''}`} aria-current={tab === i.tab ? 'page' : undefined} onClick={() => onTab(i.tab)}><Icon name={i.icon} />{i.label}</button>)}</div>
      {extra}
      <SidebarMark />
      {status.connected
        ? <div className="sidebar-footer"><div className="status-line"><span className="status-dot" />{status.text}</div></div>
        : <button className="sidebar-footer bad" onClick={() => onTab('settings')}
            title="Open Settings to check connections">
            <span className="status-line"><span className="status-dot red" />{status.text}</span>
          </button>}
    </nav>
  )
}
