import { useEffect, useState } from 'react'
import { api, ApiError } from '../api'
import type { AppSettings, LosslessHealth, TelegramStatus } from '../api'
import type { Live } from '../live'
import Banner from './Banner'
import CopyButton from './CopyButton'
import SharingPanel from './SharingPanel'

const getErrorMessage = (e: unknown, fallback: string): string => e instanceof ApiError ? e.message : fallback

const PORT_ROWS: [keyof NonNullable<AppSettings['ports']>, string][] = [
  ['app', 'Flackey itself'], ['sidecar', 'the Soulseek helper'], ['soulseek_listen', 'incoming Soulseek transfers'],
]

/* Same two questions the sidebar keeps apart: `enabled` means credentials are saved, `provider.status`
   means the helper answered a probe just now. Neither one alone is "connected". */
function soulseekLine(l: LosslessHealth | undefined): { ok: boolean; text: string; hint?: string } {
  if (!l?.enabled) return { ok: false, text: 'Not set up — run setup again to add an account' }
  if (l.provider === null) return { ok: false, text: 'Starting…' }
  if (l.provider.status === 'ok') return { ok: true, text: `Connected as ${l.provider.username ?? 'your account'}` }
  if (l.provider.status === 'not_logged_in') {
    return { ok: false, text: 'Signing in…',
      hint: 'If it stays here, Soulseek may be refusing the name — somebody else may already use it.' }
  }
  return { ok: false, text: 'Not reachable', hint: 'The Soulseek helper is not answering on this Mac.' }
}

export default function SettingsPage({ live, onReconnect }: { live: Live; onReconnect: () => void }) {
  const s = live.settings; const authorized = live.health?.telegram_authorized ?? true
  const [editing, setEditing] = useState(false); const [path, setPath] = useState(''); const [err, setErr] = useState<string | null>(null)
  const [revealError, setRevealError] = useState<string | null>(null)
  const [tg, setTg] = useState<TelegramStatus | null>(null)
  const [tgError, setTgError] = useState<string | null>(null)
  const [signingOut, setSigningOut] = useState(false)
  const [pickerAvailable, setPickerAvailable] = useState(false)
  const [busy, setBusy] = useState(false)
  const [resettingSetup, setResettingSetup] = useState(false)
  const [setupResetError, setSetupResetError] = useState<string | null>(null)
  const [reconnecting, setReconnecting] = useState(false)
  const [soulseekError, setSoulseekError] = useState<string | null>(null)
  // The saved Soulseek password, once the owner asks for it. Held only in this component's state:
  // nothing fetches it until the button is pressed, and leaving Settings forgets it again.
  const [soulseekPassword, setSoulseekPassword] = useState<string | null>(null)
  const [passwordBusy, setPasswordBusy] = useState(false)
  const [passwordError, setPasswordError] = useState<string | null>(null)
  // The owner's own Telegram API keys, if they would rather not use the ones Flackey ships with. Held
  // only long enough to post them: the hash is never read back, so there is nothing to prefill from.
  const [apiId, setApiId] = useState(''); const [apiHash, setApiHash] = useState('')
  const [keysBusy, setKeysBusy] = useState(false)
  const [keysError, setKeysError] = useState<string | null>(null)
  const [keysSaved, setKeysSaved] = useState(false)
  useEffect(() => {
    api.telegramStatus().then(setTg).catch(e => setTgError(getErrorMessage(e, "Couldn't check the Telegram connection.")))
  }, [authorized])
  useEffect(() => {
    api.pickFolderAvailable().then(r => setPickerAvailable(r.available)).catch(() => {})
  }, [])
  if (!s) return null
  async function save() {
    try { live.setSettings(await api.saveSettings(path)); setEditing(false); setErr(null) }
    catch (e) { setErr(getErrorMessage(e, 'Could not save that folder.')) }
  }
  async function chooseFolderClicked() {
    setBusy(true); setErr(null)
    try { const r = await api.pickFolder(path || null); if (r.path) setPath(r.path) }
    catch (e) { setErr(getErrorMessage(e, "Couldn't open the folder chooser.")) } finally { setBusy(false) }
  }
  const reveal = () => {
    api.reveal(s.data_dir)
      .then(() => setRevealError(null))
      .catch(err => setRevealError(getErrorMessage(err, "Couldn't open folder. Try again.")))
  }
  const showLogs = () => {
    api.reveal(s.log_path)
      .then(() => setRevealError(null))
      .catch(err => setRevealError(getErrorMessage(err, "Couldn't open the log. Try again.")))
  }
  const signOut = () => {
    setSigningOut(true)
    api.logout()
      .then(() => setTgError(null))
      .catch(err => setTgError(getErrorMessage(err, 'Could not sign out. Try again.')))
      .finally(() => setSigningOut(false))
  }
  const resetSetup = () => {
    setResettingSetup(true)
    api.setupReset().then(() => live.refresh())
      .then(() => setSetupResetError(null))
      .catch(err => setSetupResetError(getErrorMessage(err, 'Could not restart setup. Try again.')))
      .finally(() => setResettingSetup(false))
  }
  const telegramLine = authorized ? (tg?.phone_masked ? `Connected as ${tg.phone_masked}` : 'Connected') : 'Signed out'
  const lossless: LosslessHealth | undefined = live.health?.lossless
  const soulseek = soulseekLine(lossless)
  const reconnectSoulseek = () => {
    setReconnecting(true); setSoulseekError(null)
    api.connectSoulseek()
      .then(() => live.refresh())
      .catch(e => setSoulseekError(getErrorMessage(e, "Couldn't reconnect to Soulseek.")))
      .finally(() => setReconnecting(false))
  }
  const saveKeys = () => {
    setKeysBusy(true); setKeysError(null); setKeysSaved(false)
    api.telegramKeys(apiId.trim(), apiHash.trim())
      // Cleared rather than left on screen: a hash that stays in a field is a secret anyone walking past
      // can read, and the server never hands one back to refill it with.
      .then(() => { setApiId(''); setApiHash(''); setKeysSaved(true) })
      .catch(e => setKeysError(getErrorMessage(e, 'Could not save those keys.')))
      .finally(() => setKeysBusy(false))
  }
  const showSoulseekPassword = () => {
    setPasswordBusy(true); setPasswordError(null)
    api.soulseekPassword()
      .then(r => setSoulseekPassword(r.password))
      .catch(e => setPasswordError(getErrorMessage(e, "Couldn't read the saved Soulseek password.")))
      .finally(() => setPasswordBusy(false))
  }
  return (
    <div className="scroll settings">
      {revealError && <Banner tone="red" text={revealError} action={{ label: 'Dismiss', onClick: () => setRevealError(null) }} />}
      {tgError && <Banner tone="red" text={tgError} action={{ label: 'Dismiss', onClick: () => setTgError(null) }} />}
      {setupResetError && <Banner tone="red" text={setupResetError} action={{ label: 'Dismiss', onClick: () => setSetupResetError(null) }} />}
      <h1>Settings</h1>
      <div className="group">
        <div className="srow"><div className="srow-body"><div className="k">Library folder</div>
          {editing ? <><input className="input" value={path} onChange={e => setPath(e.target.value)} />{err && <div className="err">{err}</div>}</> : <div className="v mono">{s.library_root}</div>}</div>
          <div className="actions">{editing
            ? <>{pickerAvailable && <button className="btn-secondary" onClick={chooseFolderClicked} disabled={busy}>Choose…</button>}<button className="btn-secondary" onClick={() => { setEditing(false); setErr(null) }} disabled={busy}>Cancel</button><button className="btn-primary" onClick={save} disabled={busy}>Save</button></>
            : <button className="btn-secondary" onClick={() => { setPath(s.library_root); setEditing(true); setErr(null) }}>Change</button>}</div></div>
      </div>
      <div className="group">
        <div className="srow"><div className="srow-body"><div className="k">Telegram</div>
          <div className="v"><span className={`status-dot${authorized ? '' : ' amber'}`} />{telegramLine}</div></div>
          <div className="actions">{authorized ? <button className="btn-secondary" onClick={signOut} disabled={signingOut}>Sign out</button>
            : <button className="btn-secondary" onClick={onReconnect}>Reconnect</button>}</div></div>
        {/* Flackey ships with keys of its own and almost nobody needs to replace them, so this is folded
            away beside the ports table rather than sitting in the panel. It exists for the owner whose
            copy was built without them, or who would rather sign in under keys they control. */}
        <div className="srow"><div className="srow-body">
          <details className="tech">
            <summary>Use your own Telegram API keys</summary>
            <div className="keys">
              <div className="field"><label htmlFor="settings-tg-api-id">API ID</label>
                <input id="settings-tg-api-id" className="input" autoComplete="off" spellCheck={false}
                  value={apiId} onChange={e => { setApiId(e.target.value); setKeysSaved(false) }} /></div>
              <div className="field"><label htmlFor="settings-tg-api-hash">API hash</label>
                <input id="settings-tg-api-hash" className="input" type="password" autoComplete="off" spellCheck={false}
                  value={apiHash} onChange={e => { setApiHash(e.target.value); setKeysSaved(false) }} /></div>
            </div>
            {keysError && <div className="err">{keysError}</div>}
            {keysSaved && <div className="hint-row">Saved. Sign in again from the Telegram row.</div>}
            <div className="row-gap"><button className="btn-secondary" onClick={saveKeys}
              disabled={keysBusy || !apiId.trim() || !apiHash.trim()}>{keysBusy ? 'Saving…' : 'Save keys'}</button></div>
          </details></div></div>
      </div>
      <div className="group">
        <div className="srow"><div className="srow-body"><div className="k">Soulseek</div>
          <div className="v"><span className={`status-dot${soulseek.ok ? '' : ' amber'}`} />{soulseek.text}</div>
          {soulseek.hint && <div className="v">{soulseek.hint}</div>}
          {soulseekError && <div className="err">{soulseekError}</div>}</div>
          <div className="actions">{lossless?.enabled &&
            <button className="btn-secondary" onClick={reconnectSoulseek} disabled={reconnecting}>
              {reconnecting ? 'Reconnecting…' : soulseek.ok ? 'Reconnect' : 'Try again'}</button>}</div></div>
        {/* Soulseek has no password reset: the name is bound to the password it was claimed with, and
            Flackey generated both. So the owner has to be able to get this string back -- without it they
            cannot sign in from any other machine, ever, and the account is gone. Behind a press rather than
            printed in the panel, because a secret on screen is a secret over someone's shoulder. */}
        {s.soulseek_enabled && <div className="srow"><div className="srow-body"><div className="k">Password</div>
          {soulseekPassword
            ? <div className="v mono">{soulseekPassword}</div>
            : <div className="v">Soulseek cannot reset a password. Keep a copy of this one somewhere safe.</div>}
          {passwordError && <div className="err">{passwordError}</div>}</div>
          <div className="actions">{soulseekPassword
            ? <><CopyButton value={soulseekPassword} />
              <button className="btn-secondary" onClick={() => setSoulseekPassword(null)}>Hide</button></>
            : <button className="btn-secondary" onClick={showSoulseekPassword} disabled={passwordBusy}>
                {passwordBusy ? 'Reading…' : 'Show'}</button>}</div></div>}
        {/* Downloading works behind any router; being downloaded from does not, and an account nobody can
            pull from is the one Soulseek eventually stops trusting. The state arrives on the status event,
            so the answer to a re-check lands here by itself and the response is discarded. */}
        {s.soulseek_enabled && <div className="srow"><div className="srow-body"><div className="k">Sharing</div>
          <SharingPanel state={live.health?.sharing ?? null} onCheck={() => { api.checkSharing().catch(() => undefined) }} /></div></div>}
        {/* Ports and the pick rules are the two things nobody needs until something is wrong, and stating
            them beside the format switch made the panel read as a control room. Folded away, not dropped:
            they are the first thing to ask for when a transfer never starts or a copy is refused. */}
        {(s.ports || (lossless?.enabled && s.ranking)) && <div className="srow"><div className="srow-body">
          <details className="tech">
            <summary>Technical details</summary>
            {s.ports && <><div className="k">Ports</div>
              <div className="v mono">{PORT_ROWS.map(([key, label]) => {
                const p = s.ports![key]
                return <div key={key}>{p.port} — {label} {p.public ? '· open to other Soulseek users' : '· this Mac only'}</div>
              })}</div></>}
            {lossless?.enabled && s.ranking && <><div className="k">How copies are ranked</div>
              <div className="v">Files a peer offers must match on length (±{s.ranking.duration_tolerance_s}s) and
                title ({s.ranking.title_ratio}% or closer){s.ranking.require_artist ? ', and name the artist' : ''}.
                Survivors are ordered by quality, then by who can send now. Flackey tries up to {s.ranking.max_picks} of
                them, and keeps a copy only if its fingerprint matches the original at {Math.round(s.ranking.fingerprint_min * 100)}% or better.</div></>}
          </details></div></div>}
      </div>
      <div className="group">
        <div className="srow"><div className="srow-body"><div className="k">App version</div><div className="v">Flackey {s.version}</div></div></div>
        <div className="srow"><div className="srow-body"><div className="k">App data</div><div className="v mono">{s.data_dir}</div></div>
          <div className="actions"><button className="btn-secondary" onClick={reveal}>Show in Finder</button><button className="btn-secondary" onClick={showLogs}>Show logs</button></div></div>
      </div>
      <div className="group">
        <div className="srow"><div className="srow-body"><div className="k">Setup</div>
          <div className="v">Run the welcome and setup screens again. Your library folder and Telegram connection are kept.</div></div>
          <div className="actions"><button className="btn-secondary" onClick={resetSetup} disabled={resettingSetup}>Run setup again…</button></div></div>
      </div>
    </div>
  )
}
