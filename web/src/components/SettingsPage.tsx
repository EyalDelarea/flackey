import { useEffect, useState } from 'react'
import { api, ApiError } from '../api'
import type { AppSettings, LosslessHealth, TelegramStatus, UpdateStatus } from '../api'
import type { Live } from '../live'
import Banner from './Banner'
import CopyButton from './CopyButton'
import FormatOptions, { FORMAT_LABELS } from './FormatOptions'
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

/* Four readings, not two. The bot is reached *through* the Telegram account above it, so "on" is only
   the whole truth while that account is signed in -- on its own, "On" beside a green dot told an owner
   whose Telegram had dropped that the thing was working. Off is grey rather than amber because the owner
   chose it and nothing is wrong; on-but-unreachable is amber, and names Telegram rather than the bot,
   because Telegram is where the fix is. */
function deezerBotLine(authorized: boolean, enabled: boolean): { dot: string; text: string } {
  if (!enabled) return { dot: ' off', text: authorized ? 'Off — requests use Soulseek only' : 'Off — and Telegram is signed out' }
  if (!authorized) return { dot: ' amber', text: 'On, but Telegram is signed out — sign in above and the bot answers again' }
  return { dot: '', text: 'On — Flackey can search and fetch through Telegram' }
}

export default function SettingsPage({ live, onReconnect }: { live: Live; onReconnect: () => void }) {
  const s = live.settings; const authorized = live.health?.telegram_authorized ?? true
  const [editing, setEditing] = useState(false); const [path, setPath] = useState(''); const [err, setErr] = useState<string | null>(null)
  const [revealError, setRevealError] = useState<string | null>(null)
  const [tg, setTg] = useState<TelegramStatus | null>(null)
  const [tgError, setTgError] = useState<string | null>(null)
  const [signingOut, setSigningOut] = useState(false)
  const [sourceBusy, setSourceBusy] = useState(false)
  const [sourceError, setSourceError] = useState<string | null>(null)
  const [pickerAvailable, setPickerAvailable] = useState(false)
  const [busy, setBusy] = useState(false)
  const [resettingSetup, setResettingSetup] = useState(false)
  const [setupResetError, setSetupResetError] = useState<string | null>(null)
  const [reconnecting, setReconnecting] = useState(false)
  const [soulseekError, setSoulseekError] = useState<string | null>(null)
  const [formatBusy, setFormatBusy] = useState(false)
  const [formatError, setFormatError] = useState<string | null>(null)
  const [checkError, setCheckError] = useState<string | null>(null)
  // The saved Soulseek password, once the owner asks for it. Held only in this component's state:
  // nothing fetches it until the button is pressed, and leaving Settings forgets it again.
  const [soulseekPassword, setSoulseekPassword] = useState<string | null>(null)
  const [passwordBusy, setPasswordBusy] = useState(false)
  const [passwordError, setPasswordError] = useState<string | null>(null)
  const [webLogin, setWebLogin] = useState<{ username: string; password: string } | null>(null)
  const [webLoginError, setWebLoginError] = useState<string | null>(null)
  // The owner's own Telegram API keys, if they would rather not use the ones Flackey ships with. Held
  // only long enough to post them: the hash is never read back, so there is nothing to prefill from.
  const [apiId, setApiId] = useState(''); const [apiHash, setApiHash] = useState('')
  const [keysBusy, setKeysBusy] = useState(false)
  const [keysError, setKeysError] = useState<string | null>(null)
  const [keysSaved, setKeysSaved] = useState(false)
  const [update, setUpdate] = useState<UpdateStatus | null>(null)
  const [updateChecking, setUpdateChecking] = useState(true)
  const [autoUpdateBusy, setAutoUpdateBusy] = useState(false)
  const [autoUpdateError, setAutoUpdateError] = useState<string | null>(null)
  const [installStarting, setInstallStarting] = useState(false)
  const [installError, setInstallError] = useState<string | null>(null)
  const [releaseBusy, setReleaseBusy] = useState(false)
  useEffect(() => {
    api.telegramStatus().then(setTg).catch(e => setTgError(getErrorMessage(e, "Couldn't check the Telegram connection.")))
  }, [authorized])
  useEffect(() => {
    api.pickFolderAvailable().then(r => setPickerAvailable(r.available)).catch(() => {})
  }, [])
  // The server has answered for itself, so the local "starting…" stand-in steps aside. Keyed on the
  // object rather than its state, because a retry after a failure moves neither the old state nor the
  // percentage until the first chunk lands.
  useEffect(() => { setInstallStarting(false) }, [live.updateDownload])
  useEffect(() => {
    setUpdateChecking(true)
    api.update().then(setUpdate).catch(() => setUpdate({ ok: false, current: s?.version ?? '', newer: false,
      available: false, latest: null, url: null, release_url: null, size: null, size_label: null,
      published_at: null, published_date: null, prerelease: false, error: 'Could not check for updates.' }))
      .finally(() => setUpdateChecking(false))
  }, [s?.version])
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
  const sourceEnabled = live.health?.source_enabled ?? true
  const bot = deezerBotLine(authorized, sourceEnabled)
  const toggleTelegramSource = () => {
    setSourceBusy(true); setSourceError(null)
    api.telegramSource(!sourceEnabled)
      .then(() => live.refresh())
      .catch(err => setSourceError(getErrorMessage(err, "Couldn't change the Deezer bot setting.")))
      .finally(() => setSourceBusy(false))
  }
  const lossless: LosslessHealth | undefined = live.health?.lossless
  const soulseek = soulseekLine(lossless)
  // The same three conditions the rows in that box carry, asked once: an account's two logins and its
  // sharing state, or the diagnostics. None of them is a given, so neither is the box.
  const soulseekRows = !!s.soulseek_enabled || !!s.ports || !!(lossless?.enabled && s.ranking)
  const reconnectSoulseek = () => {
    setReconnecting(true); setSoulseekError(null)
    api.connectSoulseek()
      .then(() => live.refresh())
      .catch(e => setSoulseekError(getErrorMessage(e, "Couldn't reconnect to Soulseek.")))
      .finally(() => setReconnecting(false))
  }
  const formats = s.filing_formats ?? ['aiff', 'wav', 'flac']
  const currentFormat = s.lossless_filing_format ?? 'aiff'
  const saveFormat = (format: string) => {
    if (format === currentFormat) return
    setFormatBusy(true); setFormatError(null)
    api.saveSettings(s.library_root, { lossless_filing_format: format })
      .then(next => live.setSettings(next))
      .catch(e => setFormatError(getErrorMessage(e, 'Could not save that format.')))
      .finally(() => setFormatBusy(false))
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
  const checkSharing = () => {
    setCheckError(null)
    api.checkSharing().catch(e => setCheckError(getErrorMessage(e, 'Could not start the check. Try again.')))
  }
  // Both of these used to be `window.open`, which does nothing at all inside the webview the app runs in:
  // the press was real, the handler ran, and not one pixel changed. The work belongs to the server, which
  // can reach a browser and the installer -- the same move `reveal()` above already makes.
  const download = live.updateDownload
  const startUpdate = () => {
    setInstallStarting(true); setInstallError(null)
    // The answer arrives on the status event, the way the sharing check's does, so the response here is
    // only worth its failure: a press that could not even start needs saying, and nothing else will.
    // Not cleared when the POST resolves: that only means the server took the job, and the gap until the
    // first status event is exactly the silence this row is here to end. The effect below clears it when
    // the server actually reports back.
    api.installUpdate()
      .catch(e => { setInstallError(getErrorMessage(e, 'Could not start the download. Try again.')); setInstallStarting(false) })
  }
  const openRelease = () => {
    setReleaseBusy(true); setInstallError(null)
    api.openRelease()
      .catch(e => setInstallError(getErrorMessage(e, "Couldn't open the release page.")))
      .finally(() => setReleaseBusy(false))
  }
  const downloading = download?.state === 'downloading' || installStarting
  const mb = (n: number) => `${(n / 1e6).toFixed(1)} MB`
  const autoUpdateOn = s?.auto_update_check !== false
  const toggleAutoUpdate = () => {
    setAutoUpdateBusy(true); setAutoUpdateError(null)
    api.saveSettings(s.library_root, { auto_update_check: !autoUpdateOn })
      .then(next => live.setSettings(next))
      .catch(e => setAutoUpdateError(getErrorMessage(e, 'Could not save that setting.')))
      .finally(() => setAutoUpdateBusy(false))
  }
  const showSoulseekPassword = () => {
    setPasswordBusy(true); setPasswordError(null)
    api.soulseekPassword()
      .then(r => setSoulseekPassword(r.password))
      .catch(e => setPasswordError(getErrorMessage(e, "Couldn't read the saved Soulseek password.")))
      .finally(() => setPasswordBusy(false))
  }
  const showWebLogin = () => {
    setWebLoginError(null)
    api.slskdCredentials().then(setWebLogin)
      .catch(e => setWebLoginError(getErrorMessage(e, "Couldn't read the helper login.")))
  }
  return (
    <div className="scroll settings">
      {revealError && <Banner tone="red" text={revealError} action={{ label: 'Dismiss', onClick: () => setRevealError(null) }} />}
      {tgError && <Banner tone="red" text={tgError} action={{ label: 'Dismiss', onClick: () => setTgError(null) }} />}
      {setupResetError && <Banner tone="red" text={setupResetError} action={{ label: 'Dismiss', onClick: () => setSetupResetError(null) }} />}
      <h1>Settings</h1>
      {/* Every account Flackey holds, in one box and one column of dots. They were spread over two cards
          with the library folder between them, which left the owner counting green dots in two places and
          guessing what the Deezer bot had to do with the Telegram row above it. */}
      <h2>Connections</h2>
      <div className="group">
        <div className="srow"><div className="srow-body"><div className="k">Telegram</div>
          <div className="v"><span className={`status-dot${authorized ? '' : ' amber'}`} />{telegramLine}</div></div>
          <div className="actions">{authorized ? <button className="btn-secondary" onClick={signOut} disabled={signingOut}>Sign out</button>
            : <button className="btn-secondary" onClick={onReconnect}>Reconnect</button>}</div></div>
        {/* Indented under Telegram, not beside it: the bot is not a fourth account to sign into, it is a
            chat Flackey holds inside the account above. The sentence says so as well, because a reader
            who has never met the bot should not have to infer it from an indent. */}
        <div className="srow sub"><div className="srow-body"><div className="k">Deezer bot</div>
          <div className="v"><span className={`status-dot${bot.dot}`} />{bot.text}</div>
          <div className="v">A bot Flackey messages inside Telegram to search for and fetch tracks. It needs the Telegram account above.</div>
          {sourceError && <div className="err">{sourceError}</div>}</div>
          <div className="actions">{!sourceEnabled && !authorized
            ? <button className="btn-secondary" onClick={onReconnect}>Reconnect Telegram</button>
            : <button className="btn-secondary" onClick={toggleTelegramSource} disabled={sourceBusy}>
                {sourceBusy ? 'Saving…' : sourceEnabled ? 'Turn off' : 'Turn on'}</button>}</div></div>
        {/* Flackey ships with keys of its own and almost nobody needs to replace them, so this stays
            folded away rather than sitting open in the panel. It belongs to the Telegram connection --
            it decides which keys the sign-in above uses -- so it follows those two rows and nothing else.
            It exists for the owner whose copy was built without keys, or who would rather use their own. */}
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
        {/* The other account, and the other green dot. Everything else Soulseek needs -- its password,
            the helper login, sharing, the ports -- is a box of its own further down: this row answers
            only "is it connected", which is the question the whole section is here to answer. */}
        <div className="srow"><div className="srow-body"><div className="k">Soulseek</div>
          <div className="v"><span className={`status-dot${soulseek.ok ? '' : ' amber'}`} />{soulseek.text}</div>
          {soulseek.hint && <div className="v">{soulseek.hint}</div>}
          {soulseekError && <div className="err">{soulseekError}</div>}</div>
          <div className="actions">{lossless?.enabled &&
            <button className="btn-secondary" onClick={reconnectSoulseek} disabled={reconnecting}>
              {reconnecting ? 'Reconnecting…' : soulseek.ok ? 'Reconnect' : 'Try again'}</button>}</div></div>
      </div>
      {/* Where tracks land and what they land as: one question, so one box. The format switch used to
          sit with the Soulseek rows, which said -- by position, the way the Deezer bot's dependency used
          to be said -- that it governed Soulseek downloads only. It governs every lossless download,
          whichever source fetched it, so it belongs beside the folder they all land in. */}
      <h2>Library</h2>
      <div className="group">
        <div className="srow"><div className="srow-body"><div className="k">Library folder</div>
          {editing ? <><input className="input" value={path} onChange={e => setPath(e.target.value)} />{err && <div className="err">{err}</div>}</> : <div className="v mono">{s.library_root}</div>}</div>
          <div className="actions">{editing
            ? <>{pickerAvailable && <button className="btn-secondary" onClick={chooseFolderClicked} disabled={busy}>Choose…</button>}<button className="btn-secondary" onClick={() => { setEditing(false); setErr(null) }} disabled={busy}>Cancel</button><button className="btn-primary" onClick={save} disabled={busy}>Save</button></>
            : <button className="btn-secondary" onClick={() => { setPath(s.library_root); setEditing(true); setErr(null) }}>Change</button>}</div></div>
        <div className="srow"><div className="srow-body"><div className="k">File format</div>
          <div className="v">New lossless tracks will be filed as {FORMAT_LABELS[currentFormat] ?? currentFormat.toUpperCase()}.</div>
          <FormatOptions formats={formats} value={currentFormat} onChange={saveFormat} disabled={formatBusy} />
          {formatError && <div className="err">{formatError}</div>}</div></div>
      </div>
      {/* What is left is Soulseek and nothing else: its two logins, whether other people can reach this
          Mac, and the diagnostics. Every row inside is conditional, and with the format switch moved out
          none of them is guaranteed -- so the heading and its box are drawn only when something would
          actually be inside, rather than leaving a titled empty card on a copy with no account. */}
      {soulseekRows && <>
      <h2>Soulseek</h2>
      <div className="group">
        {/* Soulseek has no password reset: the name is bound to the password it was claimed with, and
            Flackey generated both. So the owner has to be able to get this string back -- without it they
            cannot sign in from any other machine, ever, and the account is gone. Behind a press rather than
            printed in the panel, because a secret on screen is a secret over someone's shoulder. */}
        {s.soulseek_enabled && <div className="srow"><div className="srow-body"><div className="k">Soulseek account password</div>
          {soulseekPassword
            ? <div className="v mono">{soulseekPassword}</div>
            : <div className="v">Soulseek cannot reset a password. Keep a copy of this one somewhere safe.</div>}
          {passwordError && <div className="err">{passwordError}</div>}</div>
          <div className="actions">{soulseekPassword
            ? <><CopyButton value={soulseekPassword} />
              <button className="btn-secondary" onClick={() => setSoulseekPassword(null)}>Hide</button></>
            : <button className="btn-secondary" onClick={showSoulseekPassword} disabled={passwordBusy}>
                {passwordBusy ? 'Reading…' : 'Show'}</button>}</div></div>}
        {s.soulseek_enabled && <div className="srow"><div className="srow-body"><div className="k">Helper web login</div>
          <div className="v">The local slskd web page uses a separate username and password, not your Soulseek account password.</div>
          {webLogin && <div className="v mono">{webLogin.username} · {webLogin.password}</div>}
          {webLoginError && <div className="err">{webLoginError}</div>}</div>
          <div className="actions">{webLogin ? <><CopyButton value={webLogin.password} /><button className="btn-secondary" onClick={() => setWebLogin(null)}>Hide</button></>
            : <button className="btn-secondary" onClick={showWebLogin}>Show login</button>}</div></div>}
        {/* Downloading works behind any router; being downloaded from does not, and an account nobody can
            pull from is the one Soulseek eventually stops trusting. The state arrives on the status event,
            so the answer to a re-check lands here by itself and the response is discarded. */}
        {s.soulseek_enabled && <div className="srow"><div className="srow-body"><div className="k">Sharing</div>
          <SharingPanel state={live.health?.sharing ?? null} onCheck={checkSharing} />
          {checkError && <p className="hint-row warn">{checkError}</p>}</div></div>}
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
      </div></>}
      <div className="group">
        <div className="srow"><div className="srow-body"><div className="k">App version</div>
          <div className="v">Flackey {s.version}</div>
          {updateChecking && <div className="v">Checking for updates…</div>}
          {!updateChecking && update?.ok && !update.newer && <div className="v">Up to date.</div>}
          {!updateChecking && update?.ok && update.available && <div className="v">
            Version {update.latest} is available{update.size_label ? ` · ${update.size_label}` : ''}{update.published_date ? ` · ${update.published_date}` : ''}.
          </div>}
          {!updateChecking && update?.ok && update.newer && !update.available && <div className="v">
            Version {update.latest} is out, but the installer isn't published yet. Check back shortly.
          </div>}
          {!updateChecking && update && !update.ok && <div className="err">{update.error || 'Could not check for updates.'}</div>}
          {/* What the press is doing, in the row that was silent before. The percentage only appears once
              the server knows the size; until then the byte count is the honest thing to show. */}
          {downloading && <div className="v">
            {download?.total
              ? `Downloading… ${download.percent}% · ${mb(download.received)} of ${mb(download.total)}`
              : download ? `Downloading… ${mb(download.received)}` : 'Starting the download…'}
          </div>}
          {/* Not when `ready` carries an error: that is the installer that downloaded but would not open,
              and saying it is open directly above the line explaining that it isn't helps nobody. */}
          {download?.state === 'ready' && !download.error && <div className="v">
            Downloaded. The macOS installer is open — follow it through, then reopen Flackey.
          </div>}
          {download?.error && <div className="err">{download.error}</div>}
          {installError && <div className="err">{installError}</div>}
        </div>
          {update?.available && <div className="actions">
            <button className="btn-secondary" onClick={startUpdate} disabled={downloading}>
              {downloading ? (download?.total ? `Downloading… ${download.percent}%` : 'Downloading…')
                : download?.state === 'ready' ? 'Open installer'
                : download?.state === 'error' ? 'Try again' : 'Download update'}</button>
          </div>}
          {update?.newer && !update.available && <div className="actions">
            <button className="btn-secondary" onClick={openRelease} disabled={releaseBusy}>
              {releaseBusy ? 'Opening…' : 'View release'}</button>
          </div>}</div>
        <div className="srow"><div className="srow-body"><div className="k">Automatic update checks</div>
          <div className="v">{autoUpdateOn ? 'On — Flackey checks for updates when it starts.' : 'Off — check for updates here instead.'}</div>
          {autoUpdateError && <div className="err">{autoUpdateError}</div>}</div>
          <div className="actions"><button className="btn-secondary" onClick={toggleAutoUpdate} disabled={autoUpdateBusy}>
            {autoUpdateBusy ? 'Saving…' : autoUpdateOn ? 'Turn off' : 'Turn on'}</button></div></div>
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
