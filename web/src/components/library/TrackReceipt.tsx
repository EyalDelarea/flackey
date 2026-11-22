import { useEffect, useState } from 'react'
import { ApiError, api, trackSpectrogramUrl } from '../../api'
import type { Receipt, Recheck } from '../../api'
import { asSentence } from '../../presentation'
import HelpTag from '../HelpTag'
import Icon from '../Icon'

const day = (iso: string) => {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
}

/** What today's check said when the owner asked for this file to be checked again. */
function recheckLine(r: Recheck): { text: string; ok: boolean | null } {
  if (r.status === 'missing') return { text: 'The file was not there to check.', ok: false }
  if (r.status === 'error') return { text: `Could not check it: ${r.reason ?? 'unknown error'}.`, ok: false }
  if (r.tier === 'clear') return { text: `Passed today's check${r.reason ? ` — ${r.reason}` : ''}.`, ok: true }
  if (r.tier === 'unverified') return { text: `Unverified by today's check${r.reason ? `: ${r.reason}` : ''}. One sign of a lossy source that a genuine master can show too — have a listen.`, ok: null }
  return { text: `Today's check would refuse this file${r.reason ? `: ${r.reason}` : ''}. That is evidence of a lossy source, not proof.`, ok: false }
}

const mark = (ok: boolean | null) => ok === true ? 'ok' : ok === false ? 'bad' : 'plain'

/** The receipt under a library row: where the file came from, every check it passed, and its spectrum.
    "Check again" runs today's check on the file; it reads the file and changes nothing. */
export default function TrackReceipt({ trackId, disabled }: { trackId: number; disabled?: boolean }) {
  const [r, setR] = useState<Receipt | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [checking, setChecking] = useState(false)
  useEffect(() => {
    let current = true
    setR(null); setError(null)
    api.receipt(trackId).then(x => { if (current) setR(x) })
      .catch(err => { if (current) setError(err instanceof ApiError ? err.message : "Couldn't load this track's checks.") })
    return () => { current = false }
  }, [trackId])
  const recheck = async () => {
    setChecking(true); setError(null)
    try { setR(await api.recheckTrack(trackId)) }
    catch (err) { setError(err instanceof ApiError ? err.message : "Couldn't check this file. Try again.") }
    finally { setChecking(false) }
  }
  if (error && !r) return <div className="receipt" role="region" aria-label="Why we trust this file"><p className="receipt-error">{error}</p></div>
  if (!r) return <div className="receipt" role="region" aria-label="Why we trust this file"><p className="muted">Loading…</p></div>
  const again = r.recheck ? recheckLine(r.recheck) : null
  return (<div className="receipt" role="region" aria-label="Why we trust this file" onClick={e => e.stopPropagation()} onDoubleClick={e => e.stopPropagation()}>
    <div className="receipt-body">
      <h3>Why we trust this file
        {r.kept_anyway && <span className="trust-badge bad">Kept anyway</span>}
        {r.unverified && <span className="trust-badge plain">Unverified</span>}</h3>
      <dl>
        <dt>File</dt><dd>{r.format}</dd>
        {r.asked_for && <><dt>You asked for</dt><dd className="ellipsis" title={r.asked_for}>{r.asked_for}</dd></>}
        <dt>Filed</dt><dd>{day(r.filed_at)}{r.replaced_at && ` · replaced with a better copy ${day(r.replaced_at)}`}</dd>
      </dl>
      <ul className="receipt-checks">
        {r.checks.map(c => {
          // The note on the app's own help tag, not `title`: the Mac window (WKWebView) shows no `title` tooltip
          const value = <span className="value">{c.value}</span>
          return <li key={c.label} className={mark(c.ok)}>
            {c.ok === null ? <span className="dot" aria-hidden /> : <Icon name={c.ok ? 'check' : 'x'} size={12} stroke={2.2} />}
            <span className="label">{c.label}</span>{c.note ? <HelpTag text={asSentence(c.note)}>{value}</HelpTag> : value}
          </li>
        })}
      </ul>
      {again && <p className={`receipt-again ${mark(again.ok)}`}>Checked again {day(r.recheck!.checked_at)}: {again.text}</p>}
      {error && <p className="receipt-error">{error}</p>}
      <div className="receipt-actions">
        <button className="btn-secondary" onClick={recheck} disabled={checking || disabled}>{checking ? 'Checking…' : 'Check again'}</button>
        <span className="muted">Runs today's quality check on this file. Nothing is moved or changed.</span>
      </div>
    </div>
    {r.has_spectrogram && <img className="receipt-spectrogram" src={trackSpectrogramUrl(r.track_id)} alt="Spectrogram of this file" />}
  </div>)
}
