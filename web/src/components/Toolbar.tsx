import type { ReactNode } from 'react'

// A toolbar row (Download's paste bar, Library's search bar). It used to carry a drag layer so that
// empty toolbar space moved the window; dragging is AppKit's job now and the window is moved by its
// title bar, so the row is just the row. See `.titlebar-spacer` in app.css for why.
export default function Toolbar({ children }: { children: ReactNode }) {
  return <div className="toolbar">{children}</div>
}
