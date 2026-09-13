import type { ReactNode } from 'react'
import Icon, { type IconName } from './Icon'
import SidebarMark from './SidebarMark'
import type { LosslessHealth } from '../api'
export type Tab = 'download' | 'library' | 'uploads' | 'settings'

/* Two different questions, and the footer must not confuse them: `enabled` means credentials are saved,
   `provider.status` means the sidecar answered a probe just now. Before the worker's first probe lands
   `provider` is null, which is neither -- so it says "starting", not "connected". */
function soulseekLine(l: LosslessHealth): { ok: boolean; text: string } {
  if (l.provider === null) return { ok: false, text: 'Soulseek starting' }
  if (l.provider.status === 'ok') return { ok: true, text: 'Soulseek connected' }
  if (l.provider.status === 'not_logged_in') return { ok: false, text: 'Soulseek signing in' }
  return { ok: false, text: 'Soulseek unreachable' }
}

/* One line while everything works, because a healthy status list is a list nobody reads. It expands into
   the specifics only when one of them is unhappy, and then it is a button: the detail lives in Settings,
   so the footer's job is to say something is wrong and take you where you can see what. */
function footer(telegramAuthorized: boolean, lossless?: LosslessHealth, sourceEnabled = true): { ok: boolean; lines: string[] } {
  // Switched off is a choice, not a fault: it never turns the footer amber, and it never reads "signed
  // out" -- nobody is locked out of anything they asked for.
  const parts = [
    sourceEnabled ? { ok: telegramAuthorized, text: telegramAuthorized ? 'Telegram connected' : 'Telegram signed out' }
      : { ok: true, text: 'Telegram off' },
    ...(lossless?.enabled ? [soulseekLine(lossless)] : []),
  ]
  const bad = parts.filter(p => !p.ok)
  if (bad.length) return { ok: false, lines: bad.map(p => p.text) }
  // The healthy footer still collapses to one line, but "Connected" would be a claim about a source that
  // is not running, so with Telegram off that line says so instead.
  return { ok: true, lines: [sourceEnabled ? 'Connected' : 'Telegram off'] }
}
const ITEMS: { tab: Tab; label: string; icon: IconName }[] = [
  { tab: 'download', label: 'Download', icon: 'download' }, { tab: 'library', label: 'Library', icon: 'library' },
  { tab: 'uploads', label: 'Uploads', icon: 'upload' }, { tab: 'settings', label: 'Settings', icon: 'settings' }]
export default function Sidebar({ tab, onTab, telegramAuthorized, lossless, inset, extra, sourceEnabled = true }: { tab: Tab; onTab: (t: Tab) => void; telegramAuthorized: boolean; lossless?: LosslessHealth; inset: boolean; extra?: ReactNode; sourceEnabled?: boolean }) {
  const status = footer(telegramAuthorized, lossless, sourceEnabled)
  return (
    <nav className={`sidebar${inset ? ' inset' : ''}`}>
      {inset && <div className="drag-strip pywebview-drag-region" />}
      <div className="nav">{ITEMS.map(i => <button key={i.tab} className={`nav-item${tab === i.tab ? ' active' : ''}`} onClick={() => onTab(i.tab)}><Icon name={i.icon} />{i.label}</button>)}</div>
      {extra}
      <SidebarMark />
      {status.ok
        ? <div className="sidebar-footer"><div className="status-line"><span className="status-dot" />{status.lines[0]}</div></div>
        : <button className="sidebar-footer bad" onClick={() => onTab('settings')}
            title="Open Settings to see what's wrong">
            {status.lines.map(l => <span key={l} className="status-line"><span className="status-dot amber" />{l}</span>)}
          </button>}
    </nav>
  )
}
