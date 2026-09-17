import type { ReactNode } from 'react'
import Sidebar, { type Tab } from './Sidebar'
import type { LosslessHealth } from '../api'
export default function Shell({ tab, onTab, telegramAuthorized, lossless, banner, sidebarExtra, inset, sourceEnabled, updateWaiting, children }: { tab: Tab; onTab: (t: Tab) => void; telegramAuthorized: boolean; lossless?: LosslessHealth; banner?: ReactNode; sidebarExtra?: ReactNode; inset: boolean; sourceEnabled?: boolean; updateWaiting?: boolean; children: ReactNode }) {
  return (
    <div className="app">
      <Sidebar tab={tab} onTab={onTab} telegramAuthorized={telegramAuthorized} lossless={lossless} inset={inset} extra={sidebarExtra} sourceEnabled={sourceEnabled} updateWaiting={updateWaiting} />
      <main className="content">
        {inset && <div className="titlebar-spacer" aria-hidden />}
        {banner}
        {children}
      </main>
    </div>
  )
}
