import type { ReactNode } from 'react'
import Sidebar, { type Tab } from './Sidebar'
import type { LosslessHealth, SharingState } from '../api'
export default function Shell({ tab, onTab, telegramAuthorized, lossless, sharing, appConnected, banner, sidebarExtra, inset, sourceEnabled, updateWaiting, onReport, children }: { tab: Tab; onTab: (t: Tab) => void; telegramAuthorized: boolean; lossless?: LosslessHealth; sharing?: SharingState | null; appConnected?: boolean; banner?: ReactNode; sidebarExtra?: ReactNode; inset: boolean; sourceEnabled?: boolean; updateWaiting?: boolean; onReport?: () => void; children: ReactNode }) {
  return (
    <div className="app">
      <Sidebar tab={tab} onTab={onTab} telegramAuthorized={telegramAuthorized} lossless={lossless} sharing={sharing} appConnected={appConnected} inset={inset} extra={sidebarExtra} sourceEnabled={sourceEnabled} updateWaiting={updateWaiting} onReport={onReport} />
      <main className="content">
        {inset && <div className="titlebar-spacer" aria-hidden />}
        {banner}
        {children}
      </main>
    </div>
  )
}
