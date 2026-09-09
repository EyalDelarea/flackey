import type { ReactNode } from 'react'

// A toolbar row (Download's paste bar, Library's search bar). Inside the inset native
// title bar, empty toolbar space should still drag the window. pywebview walks up the DOM
// from the actual click target looking for a `.pywebview-drag-region` ancestor, so putting
// the class on `.toolbar` itself would turn every click on a button or input inside it into
// a drag. Instead this renders a dedicated drag layer as a sibling of the controls — behind
// them in stacking order (see `.toolbar-drag` in app.css) — so it only catches clicks that
// land on empty space.
export default function Toolbar({ inset = false, children }: { inset?: boolean; children: ReactNode }) {
  return (
    <div className="toolbar">
      {inset && <div className="toolbar-drag pywebview-drag-region" aria-hidden />}
      {children}
    </div>
  )
}
