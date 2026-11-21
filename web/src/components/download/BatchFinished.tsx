import Icon from '../Icon'
import { batchFinishText, type BatchFinishView } from '../../presentation'

/** The one card a playlist gets once it has stopped moving (idea 9): how much of it landed, which tracks did
 *  not, and a second pass at the ones Retry all would take. It sits above the list on the Downloads tab,
 *  where the owner was watching the batch, because the rows themselves have already moved to History. */
export default function BatchFinished({ f, busy, onRetry, onDismiss }: {
  f: BatchFinishView; busy: boolean; onRetry: (ids: number[]) => void; onDismiss: () => void
}) {
  const shown = f.missing.slice(0, 5)
  const more = f.missing.length - shown.length
  return (
    <section className="batch-finished" aria-label={`${f.name} finished`}>
      <div className="batch-finished-head">
        <span className={`batch-finished-mark${f.missing.length ? ' partial' : ''}`}><Icon name="check" size={13} stroke={2.2} /></span>
        <h2>{f.name} finished</h2>
        <span className="summary">{batchFinishText(f)}</span>
        <span className="batch-finished-actions">
          {f.retryIds.length > 0 && (
            <button className="btn-secondary" disabled={busy} onClick={() => onRetry(f.retryIds)}>
              {busy ? 'Retrying…' : f.retryIds.length === 1 ? 'Retry the missing one' : `Retry the ${f.retryIds.length} missing`}</button>)}
          <button className="btn-link muted" onClick={onDismiss}>Dismiss</button>
        </span>
      </div>
      {shown.length > 0 && (
        <ul className="batch-missing">
          {shown.map(m => <li key={m.id}>{m.title}</li>)}
          {more > 0 && <li className="more">and {more} more on the Failed tab</li>}
        </ul>)}
    </section>
  )
}
