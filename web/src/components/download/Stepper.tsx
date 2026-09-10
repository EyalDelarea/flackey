import { STEP_TIPS } from '../../presentation'
import type { StepView } from '../../presentation'

/** One ladder, not two widgets. The old row put dots on the left and a `Search · Choose · …` string on the
 *  right, so the reader had to pair them up by counting. Here each rung carries its own name, state and
 *  the sentence explaining it -- the words come from STEP_TIPS so the ladder has a single source. */
export default function Stepper({ steps }: { steps: StepView[] }) {
  return (
    <div className="stepper">
      <ol className="step-track" aria-label="progress">
        {steps.map(s => (
          <li key={s.name} className={`step ${s.state}`} title={STEP_TIPS[s.name]}
              aria-current={s.state === 'current' ? 'step' : undefined}>
            <span className="step-mark" aria-hidden="true" />
            <span className="step-name">{s.name}</span>
          </li>
        ))}
      </ol>
    </div>
  )
}
