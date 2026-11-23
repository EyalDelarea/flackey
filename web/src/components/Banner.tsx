export default function Banner({ tone, text, action, onDismiss }: { tone: 'amber' | 'red'; text: string; action?: { label: string; onClick: () => void }; onDismiss?: () => void }) {
  return <div className={`banner ${tone}`} role="status" aria-live={tone === 'red' ? 'assertive' : 'polite'}><span className="dot" /><span className="text">{text}</span>
    {/* Never the primary button: a banner's action is rarely the most important thing on the page, and
        a blue Dismiss was once the loudest control on the Library. */}
    {action && <button className="btn-secondary" onClick={action.onClick}>{action.label}</button>}
    {/* The one way to wave a card or banner away, everywhere: grey "Dismiss" at the right edge. Only
        offered where a banner is news rather than a fault: a signed-out Telegram comes back on the next
        status event however often it is waved away, so nothing is gained by letting it be. */}
    {onDismiss && <button className="btn-link muted dismiss" onClick={onDismiss}>Dismiss</button>}</div>
}
