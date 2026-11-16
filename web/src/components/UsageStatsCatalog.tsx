import { useEffect, useState } from 'react'
import { api } from '../api'
import type { TelemetryCatalog } from '../api'
import Icon from './Icon'

/** "See exactly what's sent": every event that can ever leave, with its field names, read from the same
 *  schema the server checks each event against -- so this list cannot promise less than is sent. */
export default function UsageStatsCatalog({ onClose }: { onClose: () => void }) {
  const [catalog, setCatalog] = useState<TelemetryCatalog | null>(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    let live = true
    api.telemetryCatalog().then(c => { if (live) setCatalog(c) }).catch(() => { if (live) setFailed(true) })
    return () => { live = false }
  }, [])
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="modal-backdrop" onClick={e => { if (e.target === e.currentTarget) onClose() }}>
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="usage-catalog-title">
        <div className="modal-head">
          <h2 id="usage-catalog-title">What usage stats contain</h2>
          <button className="modal-close" onClick={onClose} aria-label="Close"><Icon name="x" size={14} /></button>
        </div>
        <div className="modal-body">
          <p className="muted">Every report is one of these events, with only these fields. Times and counts are sent as
            ranges like “1-5s”, never exact numbers.</p>
          {failed && <div className="err">Couldn't load the list.</div>}
          {!catalog && !failed && <div className="faint">Loading…</div>}
          {catalog && <dl className="usage-catalog">
            <div className="usage-catalog-row"><dt>On every event</dt>
              <dd>{Object.keys(catalog.common).join(', ')}</dd></div>
            {Object.entries(catalog.events).map(([name, fields]) => <div key={name} className="usage-catalog-row">
              <dt className="mono">{name}</dt>
              <dd>{Object.keys(fields).length ? Object.keys(fields).join(', ') : 'no extra fields'}</dd>
            </div>)}
          </dl>}
          <div className="modal-actions"><button className="btn-primary" onClick={onClose}>Done</button></div>
        </div>
      </div>
    </div>)
}
