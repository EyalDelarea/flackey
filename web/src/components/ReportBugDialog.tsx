import { useEffect, useRef, useState } from 'react'
import { api, ApiError, type BugContext, type BugPreview, type BugSent } from '../api'
import CopyButton from './CopyButton'
import Icon from './Icon'
import { fileManager, thisComputer, usePlatform } from '../platform'
import { plural } from '../presentation'

const getErrorMessage = (e: unknown, fallback: string): string => e instanceof ApiError ? e.message : fallback

/** Report a bug from inside the app (issue #33). Written for someone who has never filed an issue: what
 *  goes along is said in a few plain lines, and the exact files are one click further for whoever wants
 *  to check. Nothing leaves until they press Send in their own email -- this only writes the email to the
 *  developer and puts the file where they can drag it in.
 *
 *  With `requestId` it is "Report this failure" (issue #165): the same dialog and the same send, about one
 *  failed track. The server then adds that track's record and narrows the log to it, and the reporter's
 *  words become optional -- the diagnosis already says what went wrong, and a report nobody sends because
 *  they could not think what to type is the one the developer never sees.
 *
 *  A plain `div role="dialog"` rather than `<dialog>.showModal()`, which jsdom does not implement. */
export default function ReportBugDialog({ screen, requestId, onClose }: { screen: string; requestId?: number; onClose: () => void }) {
  const platform = usePlatform()
  const [description, setDescription] = useState('')
  const [steps, setSteps] = useState('')
  const [preview, setPreview] = useState<BugPreview | null>(null)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [details, setDetails] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [sent, setSent] = useState<BugSent | null>(null)
  const [revealError, setRevealError] = useState<string | null>(null)
  const failure = requestId != null
  const ctx = (): BugContext => ({ screen, window: [window.innerWidth, window.innerHeight],
    display: [window.screen?.width ?? 0, window.screen?.height ?? 0], pixel_ratio: window.devicePixelRatio || 1,
    ...(failure ? { request_id: requestId } : {}) })

  useEffect(() => {
    let live = true
    api.bugPreview(ctx())
      .then(p => { if (live) setPreview(p) })
      .catch(e => { if (live) setPreviewError(getErrorMessage(e, "Couldn't gather the details. You can still send the report.")) })
    return () => { live = false }
    // Once per opening: the details are what was true when the owner pressed the button.
  }, [])
  const dialog = useRef<HTMLDivElement>(null)
  // Escape is Cancel, the way it is on a Mac sheet -- but not while there are words in the boxes that one
  // stray key would throw away. Tab stays inside the dialog, and focus goes back where it came from.
  const typed = !sent && !!(description.trim() || steps.trim())
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !typed) { onClose(); return }
      if (e.key !== 'Tab' || !dialog.current) return
      const stops = [...dialog.current.querySelectorAll<HTMLElement>('button:not(:disabled), textarea, [href]')]
      if (!stops.length) return
      const first = stops[0], last = stops[stops.length - 1]
      const inside = dialog.current.contains(document.activeElement)
      if (e.shiftKey && (document.activeElement === first || !inside)) { e.preventDefault(); last.focus() }
      else if (!e.shiftKey && (document.activeElement === last || !inside)) { e.preventDefault(); first.focus() }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, typed])
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null
    return () => opener?.focus?.()
  }, [])

  const send = (via: 'gmail' | 'mail') => {
    setBusy(true); setError(null)
    api.sendBugReport({ ...ctx(), description, steps, via })
      .then(setSent)
      .catch(e => setError(getErrorMessage(e, "Couldn't prepare the report. Try again.")))
      .finally(() => setBusy(false))
  }
  const revealAgain = () => {
    setRevealError(null)
    api.revealBugReport().catch(e => setRevealError(getErrorMessage(e, "Couldn't show the file.")))
  }
  const summary = new Map(preview?.summary ?? [])
  // "0.1.9 (Mac app)" or "0.1.9 (Windows app)" -> "0.1.9": how it was installed is for the details, not the one-line summary.
  const version = summary.get('Flackey')?.split(' ')[0]
  const canSend = !busy && (failure || !!description.trim())

  return (
    <div className="modal-backdrop">
      <div ref={dialog} className="modal" role="dialog" aria-modal="true" aria-labelledby="report-bug-title">
        <div className="modal-head">
          <h2 id="report-bug-title">{sent ? 'Almost done' : failure ? 'Report this failure' : 'Report a problem'}</h2>
          <button className="modal-close" onClick={onClose} aria-label="Close"><Icon name="x" size={14} /></button>
        </div>
        {sent ? (
          <div className="modal-body">
            <ol className="report-steps">
              <li>An email to the Flackey developer opened with your report already written.</li>
              <li>Drag <strong className="mono">{sent.file}</strong> from the {fileManager(platform)} window into the email.</li>
              <li>Press <strong>Send</strong>. That's it — thank you!</li>
            </ol>
            <div className="report-fallback">
              <span className="muted">Email didn't open? Send it yourself to <strong>{sent.to}</strong></span>
              <div className="report-fallback-actions">
                <CopyButton value={sent.to} label="Copy address" />
                <CopyButton value={`${sent.subject}\n\n${sent.body}`} label="Copy message" />
                <button className="btn-secondary" onClick={revealAgain}>Show the file again</button>
              </div>
            </div>
            {revealError && <div className="err">{revealError}</div>}
            <div className="modal-actions"><button className="btn-primary" onClick={onClose}>Done</button></div>
          </div>
        ) : (
          <div className="modal-body">
            {failure
              ? <p className="muted">Flackey sends what it knows about this track, so the developer can tell a bug from a track nobody is sharing.</p>
              : <p className="muted">Tell us what went wrong. Flackey adds the technical details, so we won't have to ask you for them later.</p>}
            {failure ? (
              <label className="report-field"><span>Anything to add? <span className="faint">(optional)</span></span>
                <textarea className="input" rows={3} autoFocus value={description} onChange={e => setDescription(e.target.value)}
                  placeholder="It's definitely on Soulseek, I found it there myself…" />
              </label>
            ) : (<>
              <label className="report-field"><span>What went wrong?</span>
                <textarea className="input" rows={4} autoFocus value={description} onChange={e => setDescription(e.target.value)}
                  placeholder="I pressed Download and the window went blank…" />
              </label>
              <label className="report-field"><span>What were you doing just before? <span className="faint">(optional)</span></span>
                <textarea className="input" rows={2} value={steps} onChange={e => setSteps(e.target.value)} />
              </label>
            </>)}
            <div className="report-included">
              <div className="report-included-head">Sent along with your words</div>
              {preview ? (<ul>
                {failure && preview.diagnosis && <li><Icon name="check" size={12} stroke={2.4} />What happened to this track: {preview.diagnosis}</li>}
                <li><Icon name="check" size={12} stroke={2.4} />Flackey {version} on {summary.get('System') ?? thisComputer(platform)}</li>
                <li><Icon name="check" size={12} stroke={2.4} />Your displays, and that you were on {summary.get('Was on') ?? 'an unknown screen'}</li>
                <li><Icon name="check" size={12} stroke={2.4} />{failure
                  ? `Flackey's log lines about this track (${plural(preview.log_lines, 'line')})`
                  : `Flackey's recent activity log (${plural(preview.log_lines, 'line')})`}</li>
              </ul>) : previewError ? <div className="err">{previewError}</div>
                : <div className="muted">Gathering details…</div>}
              <p className="faint">Your phone number, email addresses, passwords and {platform === 'mac' ? 'Mac user name' : 'user name'} are taken out of these first.</p>
              {preview && <button className="btn-link" aria-expanded={details} onClick={() => setDetails(d => !d)}>
                {details ? 'Hide details' : "Show everything that's included"}</button>}
              {preview && details && <div className="report-details">
                <table><tbody>{preview.summary.map(([k, v]) => <tr key={k}><th>{k}</th><td>{v}</td></tr>)}</tbody></table>
                {preview.files.map(f => <div key={f.name}><div className="report-file mono">{f.name}</div><pre className="mono">{f.text}</pre></div>)}
              </div>}
            </div>
            {error && <div className="err">{error}</div>}
            <div className="modal-actions">
              <span className="faint">Opens an email to the developer, ready to send.</span>
              <button className="btn-secondary" onClick={onClose}>Cancel</button>
              <button className="btn-secondary" onClick={() => send('mail')} disabled={!canSend}>
                Other email app</button>
              <button className="btn-primary" onClick={() => send('gmail')} disabled={!canSend}>
                {busy ? 'Preparing…' : 'Email with Gmail'}</button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
