import { useEffect, useRef, useState } from 'react'
import QRCode from 'qrcode'
import { api, ApiError } from '../../api'
import Icon from '../Icon'

type Mode = 'qr' | 'phone'
export default function TelegramStep({ onDone, pollMs = 1500 }: { onDone: () => void; pollMs?: number }) {
  const [mode, setMode] = useState<Mode>('qr')
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
      .then(s => { if (!cancelled) setConnected(s.authorized ? (s.phone_masked ?? '') : null) })
      .catch(() => { if (!cancelled) setConnected(null) })
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    if (connected !== null) return
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
  }, [mode, pollMs, connected])

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

  const pwBox = needPw && (<div className="pw-box"><div className="k">Your account has a two-step password</div>
    <div className="row-gap"><input className="input" type="password" placeholder="Two-step password" value={pw} onChange={e => setPw(e.target.value)} />
    <button className="btn-primary" onClick={submitPw} disabled={busy}>Sign in</button></div></div>)

  if (connected === undefined) return <div className="tg" />
  if (connected !== null) {
    return (<div className="tg"><div>
      <h1>Telegram is already connected</h1>
      <p className="lead"><span className="status-dot" style={{ display: 'inline-block', marginRight: 8 }} />{connected ? `Connected as ${connected}` : 'Connected'}</p>
      <div className="row-gap"><button className="btn-primary" onClick={() => doneRef.current()}>Continue</button></div>
    </div></div>)
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
      <div className="footnote">You are signing in with your own Telegram account. Unofficial apps are occasionally flagged by Telegram; a spare number works too.</div>
    </div>
  </div>)
}
