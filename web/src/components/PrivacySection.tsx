import { useEffect, useState } from 'react'
import { api, ApiError } from '../api'
import type { TelemetryLog, TelemetryState } from '../api'
import CopyButton from './CopyButton'
import Switch from './Switch'
import UsageStatsCatalog from './UsageStatsCatalog'
import { IP_NOTE } from './UsageStatsAsk'

const when = (iso: string) => {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString()
}

function statusLine(t: TelemetryState): string {
  if (!t.enabled) return 'Off. Nothing is collected or sent.'
  if (!t.collector) return 'Not sending: no local collector configured'
  const last = t.last_send
  const lastText = !last ? 'Nothing sent yet.'
    : last.status === 'ok' ? `Last sent ${when(last.at)}${last.events != null ? ` · ${last.events} ${last.events === 1 ? 'event' : 'events'}` : ''}.`
    : `Last send failed ${when(last.at)}${last.error ? `: ${last.error}` : ''}.`
  return `Sending to ${t.collector}. ${lastText}`
}

// Honest about a delete that has not gone through: the data is still on the collector until it does.
function deletePending(r: NonNullable<TelemetryState['delete_retry']>): string {
  const why = r.error ? ` (${r.error})` : ''
  const next = r.next_at ? `, next at ${when(r.next_at)}` : ''
  return `The delete hasn't gone through yet${why}, so what was sent is still stored. Flackey keeps trying${next}.`
}

/** Settings > Privacy: the usage-stats switch, the ID, and every event exactly as it leaves.
 *  Hidden entirely when the server says usage stats cannot run here (or does not answer). */
export default function PrivacySection({ onReport }: { onReport?: () => void } = {}) {
  const [t, setT] = useState<TelemetryState | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [logOpen, setLogOpen] = useState(false)
  const [log, setLog] = useState<TelemetryLog | null>(null)
  const [logError, setLogError] = useState<string | null>(null)
  const [catalogOpen, setCatalogOpen] = useState(false)

  useEffect(() => {
    let live = true
    api.telemetry().then(s => { if (live) setT(s) }).catch(() => undefined)
    return () => { live = false }
  }, [])
  // Read when opened, and again after anything that changes what is queued, so it shows what is there now.
  const loadLog = () => {
    setLogError(null)
    api.telemetryLog().then(setLog).catch(e => setLogError(e instanceof ApiError ? e.message : "Couldn't read the reports."))
  }
  useEffect(() => { if (logOpen) loadLog() }, [logOpen, t?.enabled, t?.id_prefix])

  if (!t?.available) return null

  const run = (call: () => Promise<TelemetryState>, fallback: string) => {
    setBusy(true); setError(null)
    call().then(setT).catch(e => setError(e instanceof ApiError ? e.message : fallback)).finally(() => setBusy(false))
  }
  const toggle = (on: boolean) => run(() => api.setTelemetry(on), on ? "Couldn't turn usage stats on." : "Couldn't turn usage stats off.")
  const newId = () => run(() => api.telemetryNewId(), "Couldn't make a new ID.")
  const pending = JSON.stringify(log?.pending ?? [], null, 2)
  const sent = JSON.stringify(log?.sent ?? [], null, 2)

  return (<>
    <h2>Privacy</h2>
    <div className="group">
      <div className="srow"><div className="srow-body"><div className="k">Share usage stats</div>
        <div className="v">Which steps work, which fail, and how long they take. No names, links or file names, and
          nothing about the music you download.{' '}
          <button type="button" className="btn-link" onClick={() => setCatalogOpen(true)}>See exactly what's sent</button></div>
        <div className="v">{IP_NOTE}</div>
        {t.enabled ? <div className="v">Turning this off also deletes everything that was sent.</div>
          : t.pending_delete ? <div className="v">Turned off. What was sent is being deleted.</div>
          : t.last_delete_confirmed ? <div className="v">Turned off. The delete was received: what was sent is
            erased within the hour.</div> : null}
        {error && <div className="err">{error}</div>}</div>
        <div className="actions"><Switch label="Share usage stats" checked={t.enabled} onChange={toggle} busy={busy} /></div></div>
      {t.enabled && t.id_prefix && <div className="srow"><div className="srow-body"><div className="k">Your ID</div>
        <div className="v mono">{t.id_prefix}…</div>
        <div className="v">A new ID deletes what was sent under this one.</div></div>
        <div className="actions"><button className="btn-secondary" onClick={newId} disabled={busy}>New ID</button></div></div>}
      <div className="srow"><div className="srow-body"><div className="k">Status</div>
        <div className="v">{statusLine(t)}</div>
        {t.last_delete_confirmed && <div className="v">Delete received at {when(t.last_delete_confirmed)}.</div>}
        {t.pending_delete && t.delete_retry && <div className="v">{deletePending(t.delete_retry)}</div>}
        {t.delete_refused && <div className="err" role="alert">Couldn't confirm deletion: our server refused the
          request{t.delete_refused.at ? ` at ${when(t.delete_refused.at)}` : ''}, so what was sent may still be
          stored. Contact us and we'll look into it.{' '}
          {onReport ? <button type="button" className="btn-link" onClick={onReport}>Report a bug…</button>
            : 'Use Report a bug in the sidebar.'}</div>}</div></div>
      <div className="srow"><div className="srow-body">
        <details className="tech" open={logOpen} onToggle={e => setLogOpen((e.currentTarget as HTMLDetailsElement).open)}>
          <summary>What's sent</summary>
          {logError && <div className="err">{logError}</div>}
          {log && <>
            <div className="usage-log-head"><div className="k">Waiting to send ({log.pending.length})</div><CopyButton value={pending} /></div>
            <pre className="usage-log mono">{pending}</pre>
            <div className="usage-log-head"><div className="k">Sent ({log.sent.length})</div><CopyButton value={sent} /></div>
            <pre className="usage-log mono">{sent}</pre>
          </>}
        </details></div></div>
    </div>
    {catalogOpen && <UsageStatsCatalog onClose={() => setCatalogOpen(false)} />}
  </>)
}
