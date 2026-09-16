import { useEffect, useRef, useState } from 'react'
import QRCode from 'qrcode'
import { api, ApiError } from '../../api'
import Icon from '../Icon'

type Mode = 'qr' | 'phone'
export default function TelegramStep({ onDone, onSkip, pollMs = 1500 }: { onDone: () => void; onSkip: () => void; pollMs?: number }) {
  const [mode, setMode] = useState<Mode>('qr')
  // A build handed to someone else arrives without Telegram API keys: they belong to whoever compiled it,
  // not to the app. So the step asks for a pair before it may touch Telegram at all.
  const [needKeys, setNeedKeys] = useState(false)
  const [apiId, setApiId] = useState(''); const [apiHash, setApiHash] = useState('')
  const [keysErr, setKeysErr] = useState<string | null>(null)
  const [img, setImg] = useState<string | null>(null); const [qrErr, setQrErr] = useState<string | null>(null)
  const [needPw, setNeedPw] = useState(false); const [pw, setPw] = useState(''); const [err, setErr] = useState<string | null>(null)
  const [phone, setPhone] = useState(''); const [code, setCode] = useState(''); const [codeSent, setCodeSent] = useState(false)
  const [busy, setBusy] = useState(false)
  // undefined = still asking; null = not signed in (show the QR); string = connected, masked phone ('' when unknown)
  const [connected, setConnected] = useState<string | null | undefined>(undefined)
  const doneRef = useRef(onDone)
  doneRef.current = onDone

  useEffect(() => {
    // Starting a QR login on an account that is already signed in makes Telegram drop that session, so a
    // migrated install with a live session must never reach api.qrStart().
    let cancelled = false
    api.telegramStatus()
      // Both in one handler: React batches them into a single render, and the QR effect below must never
      // see the half-state where nobody is signed in and the keys have not been asked for yet.
      .then(s => { if (!cancelled) { setConnected(s.authorized ? (s.phone_masked ?? '') : null); setNeedKeys(!s.configured) } })
      .catch(() => { if (!cancelled) setConnected(null) })
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    if (connected !== null || needKeys) return
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined
    async function start() {
      try {
        const q = await api.qrStart()
        if (cancelled) return
        const url = await QRCode.toDataURL(q.url, { margin: 0, width: 216, color: { dark: '#111111', light: '#ffffff' } })
        if (cancelled) return
        setImg(url)
        const poll = async () => {
          const { state } = await api.qrState(q.id).catch(() => ({ state: 'unknown' as const }))
          if (cancelled) return
          if (state === 'done') return doneRef.current()
          if (state === 'password_needed') { setNeedPw(true); return }
          if (state === 'expired' || state === 'unknown') return start()
          timer = setTimeout(poll, pollMs)
        }
        timer = setTimeout(poll, pollMs)
      } catch (e) { if (!cancelled) setQrErr(e instanceof ApiError ? e.message : 'Could not start the Telegram sign-in.') }
    }
    if (mode === 'qr') start()
    return () => { cancelled = true; if (timer) clearTimeout(timer) }
  }, [mode, pollMs, connected, needKeys])

  const fail = (e: unknown) => setErr(e instanceof ApiError ? e.message : 'Something went wrong. Try again.')
  const submitPw = () => {
    if (busy) return
    setBusy(true); setErr(null)
    api.password(pw).then(() => doneRef.current()).catch(fail).finally(() => setBusy(false))
  }
  const sendCode = () => {
    if (busy) return
    setBusy(true); setErr(null)
    api.sendCode(phone).then(() => setCodeSent(true)).catch(fail).finally(() => setBusy(false))
  }
  const signIn = () => {
    if (busy) return
    setBusy(true); setErr(null)
    api.signIn(phone, code).then(r => r.state === 'done' ? doneRef.current() : setNeedPw(true)).catch(fail).finally(() => setBusy(false))
  }

  const saveKeys = () => {
    if (busy) return
    setBusy(true); setKeysErr(null)
    api.telegramKeys(apiId.trim(), apiHash.trim()).then(() => setNeedKeys(false))
      .catch(e => setKeysErr(e instanceof ApiError ? e.message : 'Could not save the keys. Try again.'))
      .finally(() => setBusy(false))
  }
  const skip = () => {
    if (busy) return
    setBusy(true); setErr(null)
    api.skipTelegram().then(() => onSkip()).catch(fail).finally(() => setBusy(false))
  }
  const skipRow = <div className="row-gap"><button className="btn-link" onClick={skip} disabled={busy}>Skip for now</button></div>

  const pwBox = needPw && (<div className="pw-box"><div className="k">Your account has a two-step password</div>
    <div className="row-gap"><input className="input" type="password" placeholder="Two-step password" value={pw} onChange={e => setPw(e.target.value)} />
    <button className="btn-primary" onClick={submitPw} disabled={busy}>Sign in</button></div></div>)

  if (connected === undefined) return <div />
  if (connected !== null) {
    // Deliberately outside `.tg`: that grid exists to stand a 220px QR card beside its instructions, and a
    // lone child lands in the 220px column -- which is what wrapped this heading onto two lines against the
    // left edge of a step whose every other screen is centred. With nothing to sit beside, the plain block
    // inherits `.setup-content`'s centring instead.
    return (<div className="tg-done">
      <h1>Telegram is already connected</h1>
      <p className="lead"><span className="status-dot" style={{ display: 'inline-block', marginRight: 8 }} />{connected ? `Connected as ${connected}` : 'Connected'}</p>
      <div className="row-gap"><button className="btn-primary" onClick={() => doneRef.current()}>Continue</button></div>
    </div>)
  }
  // Below the already-connected branch on purpose: a copy that still has a live session does not need
  // keys asked for. Above the QR grid because nothing may reach Telegram until a pair is saved.
  if (needKeys) {
    return (<div className="tg-done">
      <h1>This copy needs Telegram API keys</h1>
      <p className="lead">These keys are free developer credentials for Flackey. Telegram creates them for you; we can’t generate them on your behalf.</p>
      <div className="telegram-key-help">
        <strong>Get your keys in about a minute</strong>
        <ol>
          <li>Open <a href="https://my.telegram.org/apps" target="_blank" rel="noreferrer">my.telegram.org/apps</a> and sign in with your Telegram number.</li>
          <li>Choose <b>API development tools</b>, then create an app if you don’t already have one.</li>
          <li>Copy the <b>App api_id</b> into the first field and the <b>api_hash</b> into the second.</li>
        </ol>
        <span>They identify this Flackey installation, not your Telegram account.</span>
      </div>
      <div className="fields">
        <div className="field"><label htmlFor="tg-api-id">API id</label>
          <input id="tg-api-id" className="input" inputMode="numeric" value={apiId} onChange={e => setApiId(e.target.value)} disabled={busy} /></div>
        <div className="field"><label htmlFor="tg-api-hash">API hash</label>
          <input id="tg-api-hash" className="input" spellCheck={false} value={apiHash} onChange={e => setApiHash(e.target.value)} disabled={busy} /></div>
      </div>
      <div className="row-gap"><button className="btn-primary" onClick={saveKeys} disabled={busy || !apiId.trim() || !apiHash.trim()}>Save keys</button></div>
      {keysErr && <div className="err">{keysErr}</div>}
      {/* `skip` reports through `err`, which the two sign-in screens render. This screen has to render it
          too, or a skip that fails from here un-disables its button and says nothing at all. */}
      {err && <div className="err">{err}</div>}
      {skipRow}
      <div className="footnote">Skipping leaves Telegram off. Flackey then fetches from Soulseek only, and you can connect Telegram later from Settings.</div>
    </div>)
  }
  return (<div className="tg">
    <div>{mode === 'qr' ? (<><div className="qr-card">{img && <img src={img} alt="QR code" />}</div>
      <div className="qr-hint">refreshes every 30 seconds</div></>) : <div className="qr-card phone"><Icon name="phone" size={48} stroke={1.2} /></div>}</div>
    <div>
      {mode === 'qr' ? (<>
        <h1>Scan with the Telegram app on your phone</h1>
        <p className="lead">Open Telegram → Settings → Devices → Link Desktop Device, then point your camera at the code.</p>
        {pwBox}
        <div className="row-gap"><button className="btn-link" onClick={() => { setMode('phone'); setNeedPw(false) }}>Use phone number instead</button></div>
      </>) : (<>
        <h1>Sign in with your phone number</h1>
        {!codeSent ? (<><input className="input" placeholder="Phone number with country code" value={phone} onChange={e => setPhone(e.target.value)} />
          <div className="row-gap"><button className="btn-primary" onClick={sendCode} disabled={busy}>Send code</button></div></>)
          : !needPw ? (<><input className="input" placeholder="Code from Telegram" value={code} onChange={e => setCode(e.target.value)} />
          <div className="row-gap"><button className="btn-primary" onClick={signIn} disabled={busy}>Sign in</button></div></>) : pwBox}
        <div className="row-gap"><button className="btn-link" onClick={() => { setMode('qr'); setNeedPw(false); setCodeSent(false) }}>Scan a QR code instead</button></div>
      </>)}
      {mode === 'qr' && qrErr && <div className="err">{qrErr}</div>}
      {err && <div className="err">{err}</div>}
      {/* Outside the mode ternary, like the footnote under it: the offer to leave Telegram off belongs to
          the step, not to whichever of the two sign-in screens happens to be showing. */}
      {skipRow}
      <div className="footnote">You are signing in with your own Telegram account. Unofficial apps are occasionally flagged by Telegram; a spare number works too.</div>
    </div>
  </div>)
}
