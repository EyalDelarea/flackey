import { useState } from 'react'
import type { WaitingItem } from '../../presentation'

/** The tracks Flackey has not found yet and is still looking for (idea 1), on one shelf at the top of the
 *  Downloads tab instead of scattered through the batches as amber rows. Folded by default: the point is
 *  "these will come, and you will be told", not another list to read. The rows stay in their batches too --
 *  that is where Stop and the countdown live. */
export default function WaitingShelf({ items }: { items: WaitingItem[] }) {
  const [open, setOpen] = useState(false)
  return (
    <section className="waiting-shelf" aria-label="Waiting for a copy">
      <div className="waiting-shelf-head">
        <span className="waiting-ring" aria-hidden="true" />
        <h2>Waiting for a copy · {items.length}</h2>
        {/* Says nothing about why: the rows do, and the reasons differ (nobody online, a busy queue,
            copies that did not match). A header that named one contradicted the row beneath it. */}
        <span className="summary">No copy of {items.length === 1 ? 'this one' : 'these'} yet. Flackey keeps
          looking and sends a notification when one lands.</span>
        <button className="btn-link" aria-expanded={open} onClick={() => setOpen(o => !o)}>{open ? 'Hide' : 'Show'}</button>
      </div>
      {open && (
        <ul className="waiting-list">
          {items.map(i => (
            <li key={i.id}>
              <span className="waiting-title">{i.title}</span>
              <span className="waiting-when">{i.lastLook} · {i.nextLook}</span>
              <span className="waiting-why">{i.why}</span>
            </li>))}
        </ul>)}
    </section>
  )
}
