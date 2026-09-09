import type { ReactNode } from 'react'
import Icon from '../Icon'

const NAMES = ['Folder', 'Telegram', 'Soulseek', 'Ready']

export default function SetupShell({ step, children, onBack, hint, inset = false }: { step: 1 | 2 | 3 | 4; children: ReactNode; onBack?: () => void; hint?: string; inset?: boolean }) {
  return (<div className={`setup${inset ? ' inset' : ''}`}>
    <div className="setup-title pywebview-drag-region">Welcome to Flackey</div>
    <div className="setup-body">
      <div className="stepper">{NAMES.map((n, i) => {
        const k = i + 1; const state = k < step ? 'done' : k === step ? 'cur' : 'next'
        return <div key={n} className={`step ${state}`}><span className="disc">{state === 'done' ? <Icon name="check" size={10} stroke={3} /> : k}</span>{n}</div>
      })}</div>
      <div className="setup-content">{children}</div>
      <div className="setup-bar">{onBack ? <button className="btn-secondary" onClick={onBack}>Back</button> : <span />}{hint && <span className="hint">{hint}</span>}</div>
    </div>
  </div>)
}
