import type { ReactNode } from 'react'
import Icon from '../Icon'

const NAMES = ['Folder', 'Format', 'Telegram', 'Soulseek', 'Ready']

export default function SetupShell({ step, children, onBack, hint, inset = false, skipped }: {
  step: 1 | 2 | 3 | 4 | 5; children: ReactNode; onBack?: () => void; hint?: string; inset?: boolean
  /** Which of the source steps (3 Telegram, 4 Soulseek) the owner skipped rather than completed --
      a step already passed with nothing connected must not wear the same green check as one that
      actually is, or the stepper is claiming a source that isn't there. */
  skipped?: Partial<Record<3 | 4, boolean>>
}) {
  return (<div className={`setup${inset ? ' inset' : ''}`}>
    <div className="setup-title pywebview-drag-region">Welcome to Flackey</div>
    <div className="setup-body">
      <div className="stepper">{NAMES.map((n, i) => {
        const k = i + 1 as 1 | 2 | 3 | 4 | 5
        const wasSkipped = (k === 3 || k === 4) && skipped?.[k]
        const state = k < step ? (wasSkipped ? 'skipped' : 'done') : k === step ? 'cur' : 'next'
        return <div key={n} className={`step ${state}`}><span className="disc">{state === 'done' ? <Icon name="check" size={10} stroke={3} /> : k}</span>{n}{state === 'skipped' && <span className="step-skipped"> Skipped</span>}</div>
      })}</div>
      <div className="setup-content">{children}</div>
      <div className="setup-bar">{onBack ? <button className="btn-secondary" onClick={onBack}>Back</button> : <span />}{hint && <span className="hint">{hint}</span>}</div>
    </div>
  </div>)
}
