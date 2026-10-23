import { useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

interface Tip { x: number; y: number; below: boolean }
const TIP_ROOM = 96
const TIP_WIDTH = 280      // the most the tag is ever wide; see .help-tag
const EDGE = 8

/** A help tag drawn by the app, for the reason `Stepper` draws its own: the packaged window is a WKWebView,
 *  which shows no `title` tooltip at all. It answers hover and keyboard focus on whatever it wraps, and is
 *  portalled to the document so a scrolling list cannot clip it. */
export default function HelpTag({ text, children }: { text: string; children: ReactNode }) {
  const [tip, setTip] = useState<Tip | null>(null)
  const show = (e: { currentTarget: Element }) => {
    const r = e.currentTarget.getBoundingClientRect()
    const below = r.top < TIP_ROOM
    // Centred on what it explains, but held inside the window: the buttons it most often explains sit at the
    // right edge of a row, where a centred tag would be squeezed into a column a word wide.
    const half = TIP_WIDTH / 2
    const x = Math.min(Math.max(r.left + r.width / 2, half + EDGE), window.innerWidth - half - EDGE)
    setTip({ x, y: below ? r.bottom : r.top, below })
  }
  const hide = () => setTip(null)
  return (
    <span className="help-tag-target" onMouseEnter={show} onMouseLeave={hide} onFocus={show} onBlur={hide}>
      {children}
      {tip && createPortal(
        <div className={`step-tip help-tag${tip.below ? ' below' : ''}`} role="tooltip"
             style={{ position: 'fixed', left: tip.x, top: tip.y }}>{text}</div>, document.body)}
    </span>
  )
}
