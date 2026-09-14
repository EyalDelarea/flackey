import { useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../../api'
import type { SharingState, SlskdProgress, SoulseekConnect } from '../../api'
import { generateAccount } from '../../soulseekAccount'
import CopyButton from '../CopyButton'
import SharingPanel from '../SharingPanel'

const POLL_MS = 1000
// Soulseek releases a name that goes a month without signing in (slsknet.org/news/node/748), and flackey
// signs in every time it starts -- so this is a real condition on keeping the account, not a footnote.
const RECYCLE_NOTE = 'Open Flackey at least once a month: Soulseek releases a name that goes 30 days without a sign-in.'

export default function SoulseekStep({ onDone, onSkip }: { onDone: (connected: boolean) => void; onSkip: () => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [configured, setConfigured] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  // True while both fields hold an account flackey made up rather than one the owner typed. It decides
  // three things: that the password is shown in the clear (they have to be able to write it down), that a
  // rejected sign-in silently deals a new name instead of telling them to pick one, and that the warning
  // about there being no password reset is worth their attention.
  const [generated, setGenerated] = useState(false)
  // The sidecar binary downloads *while* the user is typing rather than after they hit Save: it is tens
  // of megabytes, and the two have nothing to wait on each other for. The user never learns its name --
  // "they never see slskd" -- so this reports one plain line of progress and no version number.
  const [install, setInstall] = useState<SlskdProgress | null>(null)
  // Soulseek has no sign-up step: a username is claimed by signing in with it. So saving *is* creating
  // the account, and this is the only confirmation that it worked.
  const [connect, setConnect] = useState<SoulseekConnect | null>(null)
  // Whether other users will be able to pull from this Mac. Only asked for once the account exists,
  // because the port check needs the sidecar signed in to be worth anything.
  const [sharing, setSharing] = useState<SharingState | null>(null)
  const timer = useRef<number | undefined>(undefined)
  const connectTimer = useRef<number | undefined>(undefined)
  // The name that was already saved when this step opened, kept because `username` is overwritten the
  // moment the owner asks for a new account -- and that is exactly when they need to be told which
  // account they are about to walk away from.
  const savedUsername = useRef<string | null>(null)

  const deal = () => {
    const account = generateAccount()
    setUsername(account.username)
    setPassword(account.password)
    setGenerated(true)
  }

  const stopPolling = () => {
    if (timer.current !== undefined) { clearInterval(timer.current); timer.current = undefined }
  }
  const stopConnectPolling = () => {
    if (connectTimer.current !== undefined) { clearInterval(connectTimer.current); connectTimer.current = undefined }
  }

  const poll = () => {
    stopPolling()   // never leave a second interval running behind this one
    const tick = () => api.slskdProgress()
      .then(p => { setInstall(p); if (p.state === 'done' || p.state === 'error') stopPolling() })
      .catch(() => stopPolling())
    timer.current = window.setInterval(tick, POLL_MS)
    tick()          // after the interval exists, so a first tick that already reads "done" can clear it
  }

  const pollConnect = () => {
    stopConnectPolling()
    const tick = () => api.soulseekConnectStatus()
      .then(s => {
        setConnect(s)
        if (s.state === 'connected' || s.state === 'failed') { stopConnectPolling(); setBusy(false) }
        // A name flackey dealt is not one the owner has any attachment to, so a rejection deals another
        // rather than leaving them to edit a random string by hand. Their next click retries with a fresh
        // pair; the only outcome they ever see is a working account.
        if (s.state === 'failed' && generated) deal()
      })
      .catch(() => { stopConnectPolling(); setBusy(false) })
    connectTimer.current = window.setInterval(tick, POLL_MS)
    tick()
  }

  const startInstall = () => {
    setInstall({ state: 'downloading', done: 0, total: 0, error: null })
    api.installSlskd()
      .then(poll)
      .catch(() => setInstall({ state: 'error', done: 0, total: 0, error: null }))
  }

  useEffect(() => {
    let cancelled = false
    api.soulseekSetup().then(s => {
      if (cancelled) return
      setConfigured(s.configured)
      if (s.configured && s.username) { savedUsername.current = s.username; setUsername(s.username) }
      else if (!s.configured) deal()   // there is nothing to sign up for, so the form arrives filled in
    }).catch(() => { /* not an error the user should see — leave the fields empty */ })
    api.slskdSetup().then(s => {
      if (!cancelled && !s.installed) startInstall()
    }).catch(() => { /* the install can be retried from the error line; don't block the form on it */ })
    // Leaving the step only stops the polling; the install and the sign-in both run on the server.
    return () => { cancelled = true; stopPolling(); stopConnectPolling() }
  }, [])

  const save = () => {
    if (busy || !username || !password) return
    setBusy(true); setErr(null); setConnect(null)
    api.saveSoulseek(username, password)
      .then(r => {
        // A password the owner typed is already theirs to remember; one flackey dealt has to stay on
        // screen until they have copied it, because there is no way to reset it afterwards.
        if (!generated) setPassword('')
        if (r.connecting) pollConnect()
        else { setBusy(false); onDone(false) }   // no live link on this build: saved, takes effect on restart
      })
      .catch(e => {
        setBusy(false)
        setErr(e instanceof ApiError ? e.message : 'Could not save your Soulseek account. Try again.')
      })
  }

  const pct = install && install.total > 0 ? Math.min(100, Math.round((100 * install.done) / install.total)) : null
  const progressText = install?.state === 'extracting' ? 'Almost there…'
    : pct === null ? 'Getting things ready…' : `Getting things ready… ${pct}%`
  const connected = connect?.state === 'connected'
  // The port check starts on the server the moment the sidecar signs in, so this only waits for it to
  // finish: poll until the state stops saying `checking`, then stop. Its own effect rather than a branch
  // of pollConnect, so leaving the step while a check is running cannot leave an interval behind.
  useEffect(() => {
    if (!connected) return
    let stopped = false
    let id: number | undefined
    const stop = () => { stopped = true; if (id !== undefined) { clearInterval(id); id = undefined } }
    const tick = () => api.sharing()
      .then(s => { if (stopped) return; setSharing(s); if (!s.checking) stop() })
      .catch(stop)   // nothing to tell the user here: the step is about the account, not the port
    id = window.setInterval(tick, POLL_MS)
    tick()
    return stop
  }, [connected])
  // Whatever was saved before this screen opened, a name flackey has just dealt is one being created --
  // so the screen stops describing itself as the account they already have.
  const existing = configured && !generated
  const saveLabel = connected ? 'Continue' : busy ? 'Signing in…' : existing ? 'Sign in' : 'Create account'

  return (<>
    <h1>{existing ? 'Your Soulseek account' : 'Create a Soulseek account'}</h1>
    {/* Kept to two or three lines on purpose: this step carries a password warning and a pair of fields
        under it, and at the window's default height the whole column has to fit without scrolling. */}
    <p className="lead">Soulseek is where Flackey finds lossless copies. {existing
      ? 'This account is already saved — sign in to check it still works, or make a new one.'
      : generated
        ? 'It has no sign-up form — a name is yours the moment something signs in with it — so Flackey has made you one.'
        : 'There is no sign-up form: pick any name and password, and an unused name becomes yours the moment it signs in.'}</p>
    <div className="fields">
      <div className="field">
        <label htmlFor="soulseek-username">Soulseek username</label>
        <input id="soulseek-username" className="input" autoComplete="username" disabled={busy}
          value={username} onChange={e => { setUsername(e.target.value); setGenerated(false) }} />
      </div>
      <div className="field">
        <label htmlFor="soulseek-password">Soulseek password</label>
        <div className="field-row">
          {/* Shown in the clear only while it is a password flackey dealt: the owner cannot copy down what
              they cannot read, and a masked field would be hiding it from the only person it belongs to. */}
          <input id="soulseek-password" className="input" type={generated ? 'text' : 'password'}
            autoComplete="current-password" spellCheck={false} disabled={busy}
            value={password} onChange={e => { setPassword(e.target.value); setGenerated(false) }} />
          {generated && <CopyButton value={password} />}
        </div>
      </div>
    </div>
    {/* The only way back to an account flackey makes once the fields hold something else: a name was
        already saved (so nothing was dealt on mount), or the owner typed over the pair and changed their
        mind. Without it the offer to do the work for them existed on a first run and nowhere else. */}
    {!generated && <div className="hint-row"><button className="btn-link" onClick={deal} disabled={busy}>
      {configured ? 'Make me a new account' : 'Let Flackey make me an account'}</button></div>}
    {generated && <div className="hint-row warn">Copy this password somewhere safe: Soulseek has no way to
      reset one, and Flackey is the only other place it is kept.
      {savedUsername.current && ` Creating it also replaces the account already saved (${savedUsername.current}) — copy that password from Settings first if you want to keep it.`}</div>}
    {connect?.state === 'connecting' && <div className="hint-row">Signing in to Soulseek…</div>}
    {connected && <div className="hint-row ok">Signed in as {connect?.username ?? username}. The account is yours.</div>}
    {connected && sharing && <SharingPanel state={sharing} onCheck={() => {}} compact />}
    {connect?.state === 'failed' && (generated
      ? <div className="hint-row">That name was taken. Here is another one — try again.</div>
      : <div className="err">{connect.error}</div>)}
    {install && install.state !== 'done' && (install.state === 'error'
      ? <div className="hint-row">Couldn't finish getting Soulseek ready. You can carry on — Soulseek stays
          off until this succeeds. <button className="btn-link" onClick={startInstall}>Try again</button></div>
      : <div className="hint-row">{progressText}</div>)}
    <div className="row-gap">
      <button className="btn-primary lg" onClick={connected ? () => onDone(true) : save}
        disabled={!connected && (busy || !username || !password)}>{saveLabel}</button>
      <button className="btn-link" onClick={onSkip}>Skip for now</button>
    </div>
    {err && <div className="err">{err}</div>}
    <div className="footnote">Optional — you can add Soulseek later from Settings. {RECYCLE_NOTE}</div>
  </>)
}
