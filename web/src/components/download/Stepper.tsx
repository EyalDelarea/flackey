import type { StepView } from '../../presentation'

/** One ladder, not two widgets. The old row put dots on the left and a `Identify · Match · …` string on the
 *  right, so the reader had to pair them up by counting. Here each rung carries its own name and state. */
export default function Stepper({ steps }: { steps: StepView[] }) {
  return (
    <div className="stepper">
      <ol className="step-track" aria-label="progress">
        {steps.map(s => (
          <li key={s.name} className={`step ${s.state}`} aria-current={s.state === 'current' ? 'step' : undefined}>
            <span className="step-mark" aria-hidden="true" />
            <span className="step-name">{s.name}</span>
          </li>
        ))}
      </ol>
    </div>
  )
}
