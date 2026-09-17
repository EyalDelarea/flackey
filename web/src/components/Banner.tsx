export default function Banner({ tone, text, action, onDismiss }: { tone: 'amber' | 'red'; text: string; action?: { label: string; onClick: () => void }; onDismiss?: () => void }) {
  return <div className={`banner ${tone}`} role="status" aria-live={tone === 'red' ? 'assertive' : 'polite'}><span className="dot" /><span className="text">{text}</span>
    {action && <button className={tone === 'amber' ? 'btn-primary' : 'btn-secondary'} onClick={action.onClick}>{action.label}</button>}
    {/* Only offered where a banner is news rather than a fault: a signed-out Telegram comes back on the
        next status event however often it is waved away, so nothing is gained by letting it be. */}
    {onDismiss && <button className="banner-dismiss" aria-label="Dismiss" onClick={onDismiss}>×</button>}</div>
}
