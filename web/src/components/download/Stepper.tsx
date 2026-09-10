import { useState } from 'react'
import { createPortal } from 'react-dom'
import { STEP_TIPS } from '../../presentation'
import type { StepView } from '../../presentation'

interface Tip { name: string; x: number; y: number; below: boolean }
/** Roughly the tag's own height plus its gap. Above the rung is the better place -- the pointer is not
 *  covering it -- but a rung near the top of the window has no room there, and a tag half off the screen
 *  explains nothing. */
const TIP_ROOM = 96

/** One ladder, not two widgets. The old row put dots on the left and a `Search · Choose · …` string on the
 *  right, so the reader had to pair them up by counting. Here each rung carries its own name, state and
 *  the sentence explaining it -- the words come from STEP_TIPS so the ladder has a single source.
 *
 *  The sentence used to ride on the `title` attribute, which showed nothing: inside the packaged app the
 *  page is a WKWebView, and WKWebView draws no tooltip layer of its own. So the app draws one. It is
 *  portalled to the end of the document and placed in viewport coordinates because the ladder sits inside
 *  `.scroll`, an overflow-y: auto ancestor that would otherwise cut it off; and it answers focus as well
 *  as hover, because it stands in for an affordance the platform used to provide. */
export default function Stepper({ steps }: { steps: StepView[] }) {
  const [tip, setTip] = useState<Tip | null>(null)
  const show = (name: string) => (e: { currentTarget: Element }) => {
    const r = e.currentTarget.getBoundingClientRect()
    const below = r.top < TIP_ROOM
    setTip({ name, x: r.left + r.width / 2, y: below ? r.bottom : r.top, below })
  }
  return (
    <div className="stepper">
      <ol className="step-track" aria-label="progress">
        {steps.map(s => (
          <li key={s.name} className={`step ${s.state}`} onMouseEnter={show(s.name)} onMouseLeave={() => setTip(null)}
              aria-current={s.state === 'current' ? 'step' : undefined}>
            {/* A button, not the li: it is the thing that takes focus, and a rung with no explanation
                behind it should not stop the keyboard on its way past. */}
            <button type="button" className="step-tip-target" aria-label={`${s.name}: ${STEP_TIPS[s.name]}`}
                    onFocus={show(s.name)} onBlur={() => setTip(null)}>
              <span className="step-mark" aria-hidden="true" />
              <span className="step-name">{s.name}</span>
            </button>
          </li>
        ))}
      </ol>
      {tip && createPortal(
        <div className={`step-tip${tip.below ? ' below' : ''}`} role="tooltip"
             style={{ position: 'fixed', left: tip.x, top: tip.y }}>
          <strong>{tip.name}</strong>{STEP_TIPS[tip.name]}
        </div>, document.body)}
    </div>
  )
}
