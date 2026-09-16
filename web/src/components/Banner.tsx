export default function Banner({ tone, text, action }: { tone: 'amber' | 'red'; text: string; action?: { label: string; onClick: () => void } }) {
  return <div className={`banner ${tone}`} role="status" aria-live={tone === 'red' ? 'assertive' : 'polite'}><span className="dot" /><span className="text">{text}</span>
    {action && <button className={tone === 'amber' ? 'btn-primary' : 'btn-secondary'} onClick={action.onClick}>{action.label}</button>}</div>
}
