import { useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../api'
import type { AppSettings, LosslessHealth, Platform, TelegramStatus, UpdateStatus } from '../api'
import type { Live } from '../live'
import { revealLabel, thisComputer, usePlatform } from '../platform'
import Banner from './Banner'
import CopyButton from './CopyButton'
import { FORMAT_LABELS, shortFormatNote } from './FormatOptions'
import LayoutOptions from './LayoutOptions'
import Segmented from './Segmented'
import SharingPanel from './SharingPanel'
import Switch from './Switch'

const getErrorMessage = (e: unknown, fallback: string): string => e instanceof ApiError ? e.message : fallback

const PORT_ROWS: [keyof NonNullable<AppSettings['ports']>, string][] = [
  ['app', 'Flackey itself'], ['sidecar', 'the Soulseek helper'], ['soulseek_listen', 'incoming Soulseek transfers'],
]

/* Same two questions the sidebar keeps apart: `enabled` means credentials are saved, `provider.status`
   means the helper answered a probe just now. Neither one alone is "connected". The two on-the-way
   readings get a pulsing grey dot rather than amber: amber beside "Starting…" read as a fault. */
type Dot = '' | ' amber' | ' off' | ' pending'
function soulseekLine(l: LosslessHealth | undefined, platform: Platform): { ok: boolean; dot: Dot; text: string; hint?: string } {
  if (!l?.enabled) return { ok: false, dot: ' amber', text: 'Not set up — run setup again, under Advanced' }
  if (l.provider === null) return { ok: false, dot: ' pending', text: 'Starting…' }
  if (l.provider.status === 'ok') return { ok: true, dot: '', text: `Connected as ${l.provider.username ?? 'your account'}` }
  if (l.provider.status === 'not_logged_in') {
    return { ok: false, dot: ' pending', text: 'Signing in…', hint: 'If it stays here, the name may already be taken.' }
  }
  return { ok: false, dot: ' amber', text: 'Not reachable', hint: `The Soulseek helper isn't answering on ${thisComputer(platform)}.` }
}

/* Four readings, not two. The bot is reached *through* the Telegram account above it, so "on" is only
   the whole truth while that account is signed in -- on its own, "On" beside a green dot told an owner
   whose Telegram had dropped that the thing was working. Off is grey rather than amber because the owner
   chose it and nothing is wrong; on-but-unreachable is amber, and names Telegram rather than the bot,
   because Telegram is where the fix is. */
function deezerBotLine(authorized: boolean, enabled: boolean): { dot: Dot; text: string } {
  if (!enabled) return { dot: ' off', text: authorized ? 'Off — requests use Soulseek only' : 'Off — and Telegram is signed out' }
  if (!authorized) return { dot: ' amber', text: 'On, but Telegram is signed out' }
  return { dot: '', text: 'On — searches and fetches through Telegram' }
}

/* A break opportunity after every separator, so a narrow window wraps a path between folders rather
   than in the middle of "Application". */
const breakablePath = (path: string) => path.split(/(?<=[/\\])/).flatMap((part, i) => i ? [<wbr key={i} />, part] : [part])

export default function SettingsPage({ live, onReconnect, focusUpdate, onReport }: { live: Live; onReconnect: () => void; focusUpdate?: number; onReport?: () => void }) {
  const platform = usePlatform()
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
  const [layoutBusy, setLayoutBusy] = useState(false)
  const [layoutError, setLayoutError] = useState<string | null>(null)
  const [checkError, setCheckError] = useState<string | null>(null)
  // Closed on every visit: what lives here is needed rarely, and mostly when something has gone wrong.
  const [advancedOpen, setAdvancedOpen] = useState(false)
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
  const [restartBusy, setRestartBusy] = useState(false)
  useEffect(() => {
    api.telegramStatus().then(setTg).catch(e => setTgError(getErrorMessage(e, "Couldn't check the Telegram connection.")))
  }, [authorized])
  useEffect(() => {
    api.pickFolderAvailable().then(r => setPickerAvailable(r.available)).catch(() => {})
  }, [])
  // Keyed on the state rather than the object: every status event anywhere in the app -- a queue change,
  // a Telegram flag -- re-parses the whole dict into a fresh `updateDownload`, and keying on identity
  // meant any one of them could clear the flag while the press it belonged to was still in flight, which
  // handed the button back mid-download. The server claims `downloading` before it awaits anything, so a
  // press always drives the state through it and back out, and that round trip is what clears this.
  const downloadState = live.updateDownload?.state
  useEffect(() => { if (downloadState && downloadState !== 'downloading') setInstallStarting(false) }, [downloadState])
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
  const setTelegramSource = (on: boolean) => {
    setSourceBusy(true); setSourceError(null)
    api.telegramSource(on)
      .then(() => live.refresh())
      .catch(err => setSourceError(getErrorMessage(err, "Couldn't change the Deezer bot setting.")))
      .finally(() => setSourceBusy(false))
  }
  const lossless: LosslessHealth | undefined = live.health?.lossless
  const soulseek = soulseekLine(lossless, platform)
  const reconnectSoulseek = () => {
    setReconnecting(true); setSoulseekError(null)
    api.connectSoulseek()
      .then(() => live.refresh())
      .catch(e => setSoulseekError(getErrorMessage(e, "Couldn't reconnect to Soulseek.")))
      .finally(() => setReconnecting(false))
  }
  const formats = s.filing_formats ?? ['aiff', 'wav', 'flac']
  const defaultFormat = s.default_filing_format ?? 'aiff'
  const currentFormat = s.lossless_filing_format ?? defaultFormat
  const saveFormat = (format: string) => {
    if (format === currentFormat) return
    setFormatBusy(true); setFormatError(null)
    api.saveSettings(s.library_root, { lossless_filing_format: format })
      .then(next => live.setSettings(next))
      .catch(e => setFormatError(getErrorMessage(e, 'Could not save that format.')))
      .finally(() => setFormatBusy(false))
  }
  const currentLayout = s.library_layout ?? 'artist'
  const saveLayout = (layout: string) => {
    if (layout === currentLayout) return
    setLayoutBusy(true); setLayoutError(null)
    api.saveSettings(s.library_root, { library_layout: layout })
      .then(next => live.setSettings(next))
      .catch(e => setLayoutError(getErrorMessage(e, 'Could not save that layout.')))
      .finally(() => setLayoutBusy(false))
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
      .then(next => { if (next.state !== 'downloading') setInstallStarting(false) })
      .catch(e => { setInstallError(getErrorMessage(e, 'Could not start the download. Try again.')); setInstallStarting(false) })
  }
  const openRelease = () => {
    setReleaseBusy(true); setInstallError(null)
    api.openRelease()
      .catch(e => setInstallError(getErrorMessage(e, "Couldn't open the release page.")))
      .finally(() => setReleaseBusy(false))
  }
  const restartNow = () => {
    setRestartBusy(true); setInstallError(null)
    // A window that refuses to close answers 200 and still `staged`, not an error.
    api.restartForUpdate()
      .then(next => { if (next.state !== 'installing') {
        setInstallError(next.error ?? "Couldn't restart to finish the update."); setRestartBusy(false) } })
      .catch(e => { setInstallError(getErrorMessage(e, "Couldn't restart to finish the update.")); setRestartBusy(false) })
  }
  // One boolean for "there is something to install", because the server has two independent ways of
  // saying so and the install endpoint accepts either. A release that carries the signed archive but no
  // .pkg yet is `available: false, seamless: true`, and gating the button on `available` alone would
  // render that as "no update" for a release the app can perfectly well install.
  const installable = !!update?.ok && (update.available || !!update.seamless)
  const downloading = download?.state === 'downloading' || download?.state === 'verifying' || installStarting
  const staged = download?.state === 'staged'
  const mb = (n: number) => `${(n / 1e6).toFixed(1)} MB`
  const autoUpdateOn = s?.auto_update_check !== false
  const setAutoUpdate = (on: boolean) => {
    setAutoUpdateBusy(true); setAutoUpdateError(null)
    api.saveSettings(s.library_root, { auto_update_check: on })
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
  // The update banner's button lands here. A ref rather than an id lookup so it cannot go stale, and
  // `focusUpdate` is a counter so a second press scrolls again.
  const updateRow = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    if (!focusUpdate) return
    updateRow.current?.scrollIntoView?.({ behavior: 'smooth', block: 'center' })
  }, [focusUpdate])
  const showWebLogin = () => {
    setWebLoginError(null)
    api.slskdCredentials().then(setWebLogin)
      .catch(e => setWebLoginError(getErrorMessage(e, "Couldn't read the helper login.")))
  }
  const formatOptions = formats.map(f => [f, FORMAT_LABELS[f] ?? f.toUpperCase()] as const)
  const showTech = !!s.ports || !!(lossless?.enabled && s.ranking)
  return (
    <div className="scroll settings">
      {revealError && <Banner tone="red" text={revealError} action={{ label: 'Dismiss', onClick: () => setRevealError(null) }} />}
      {tgError && <Banner tone="red" text={tgError} action={{ label: 'Dismiss', onClick: () => setTgError(null) }} />}
      {setupResetError && <Banner tone="red" text={setupResetError} action={{ label: 'Dismiss', onClick: () => setSetupResetError(null) }} />}
      <h1>Settings</h1>
      {/* One row per account, one column of dots, status only. Everything an account needs less often --
          passwords, keys, sharing -- waits under Advanced, so this box answers "is it connected" and
          nothing else. */}
      <h2>Connections</h2>
      <div className="group">
        <div className="srow"><div className="srow-body"><div className="k">Telegram</div>
          <div className="v"><span className={`status-dot${authorized ? '' : ' amber'}`} />{telegramLine}</div></div>
          <div className="actions">{authorized ? <button className="btn-secondary" onClick={signOut} disabled={signingOut}>Sign out</button>
            : <button className="btn-secondary" onClick={onReconnect}>Reconnect</button>}</div></div>
        {/* The bot is a chat Flackey holds inside the Telegram account, not a fourth account to sign into,
            and the key says so in three words rather than with an indent the reader has to decode. */}
        <div className="srow"><div className="srow-body"><div className="k">Deezer bot <span className="k-note">· uses Telegram</span></div>
          <div className="v"><span className={`status-dot${bot.dot}`} />{bot.text}</div>
          {sourceError && <div className="err">{sourceError}</div>}</div>
          <div className="actions">{!sourceEnabled && !authorized
            ? <button className="btn-secondary" onClick={onReconnect}>Reconnect Telegram</button>
            : <Switch label="Deezer bot" checked={sourceEnabled} onChange={setTelegramSource} busy={sourceBusy} />}</div></div>
        <div className="srow"><div className="srow-body"><div className="k">Soulseek</div>
          <div className="v"><span className={`status-dot${soulseek.dot}`} />{soulseek.text}</div>
          {soulseek.hint && <div className="v">{soulseek.hint}</div>}
          {soulseekError && <div className="err">{soulseekError}</div>}</div>
          <div className="actions">{lossless?.enabled &&
            <button className="btn-secondary" onClick={reconnectSoulseek} disabled={reconnecting}>
              {reconnecting ? 'Reconnecting…' : soulseek.ok ? 'Reconnect' : 'Try again'}</button>}</div></div>
      </div>
      {/* Where tracks land and what they land as. The format governs every lossless download, whichever
          source fetched it, so it sits beside the folder they all land in rather than with Soulseek. */}
      <h2>Library</h2>
      <div className="group">
        <div className="srow"><div className="srow-body"><div className="k">Library folder</div>
          {editing ? <><input className="input" value={path} onChange={e => setPath(e.target.value)} />{err && <div className="err">{err}</div>}</> : <div className="v mono">{breakablePath(s.library_root)}</div>}</div>
          <div className="actions">{editing
            ? <>{pickerAvailable && <button className="btn-secondary" onClick={chooseFolderClicked} disabled={busy}>Choose…</button>}<button className="btn-secondary" onClick={() => { setEditing(false); setErr(null) }} disabled={busy}>Cancel</button><button className="btn-primary" onClick={save} disabled={busy}>Save</button></>
            : <button className="btn-secondary" onClick={() => { setPath(s.library_root); setEditing(true); setErr(null) }}>Change</button>}</div></div>
        {/* Only new downloads follow the layout. Rekordbox finds a track by its full path, so moving the
            ones already filed would leave every one of them "missing" there until relocated by hand. */}
        <div className="srow"><div className="srow-body"><div className="k">Folder layout</div>
          <div className="v">Where new downloads go. Tracks already filed stay put, so Rekordbox keeps finding them.</div>
          <LayoutOptions value={currentLayout} onChange={saveLayout} disabled={layoutBusy} />
          {layoutError && <div className="err">{layoutError}</div>}</div></div>
        <div className="srow"><div className="srow-body"><div className="k">File format</div>
          <div className="v">{shortFormatNote(currentFormat, defaultFormat)}</div>
          {formatError && <div className="err">{formatError}</div>}</div>
          <div className="actions"><Segmented label="File format" options={formatOptions} value={currentFormat}
            onChange={saveFormat} busy={formatBusy} /></div></div>
      </div>
      <h2>Updates</h2>
      <div className="group" ref={updateRow}>
        <div className="srow"><div className="srow-body"><div className="k">App version</div>
          <div className="v">Flackey {s.version}</div>
          {updateChecking && <div className="v">Checking for updates…</div>}
          {!updateChecking && update?.ok && !update.newer && <div className="v">Up to date.</div>}
          {!updateChecking && installable && <div className="v">
            Version {update.latest} is available{update.size_label ? ` · ${update.size_label}` : ''}{update.published_date ? ` · ${update.published_date}` : ''}.
          </div>}
          {!updateChecking && update?.ok && update.newer && !installable && <div className="v">
            Version {update.latest} is out, but the installer isn't published yet. Check back shortly.
          </div>}
          {!updateChecking && update && !update.ok && <div className="err">{update.error || 'Could not check for updates.'}</div>}
          {download?.state === 'verifying' && <div className="v">Checking the update is genuine…</div>}
          {downloading && download?.state !== 'verifying' && <div className="v">
            {download?.total
              ? `Downloading… ${download.percent}% · ${mb(download.received)} of ${mb(download.total)}`
              : download ? `Downloading… ${mb(download.received)}` : 'Starting the download…'}
          </div>}
          {/* Names what a restart would cost while transfers are running. */}
          {staged && <div className="v">
            Version {download?.version} is ready and verified. Flackey will close and reopen to finish.
            {download?.busy ? ` ${download.busy} ${download.busy === 1 ? 'transfer is' : 'transfers are'} still running and would be lost.` : ''}
          </div>}
          {download?.state === 'installing' && <div className="v">Closing to install version {download?.version}…</div>}
          {/* Not when `ready` carries an error: the installer downloaded but would not open. */}
          {download?.state === 'ready' && !download.error && <div className="v">
            {platform === 'windows'
              ? 'Downloaded. The installer is open — follow it through; it will close Flackey and can reopen it when it finishes.'
              : 'Downloaded. The macOS installer is open — follow it through, then reopen Flackey.'}
          </div>}
          {download?.error && <div className="err">{download.error}</div>}
          {installError && <div className="err">{installError}</div>}
        </div>
          {staged && <div className="actions">
            <button className="btn-secondary" onClick={restartNow} disabled={restartBusy}>
              {restartBusy ? 'Restarting…' : 'Restart now'}</button>
          </div>}
          {installable && !staged && download?.state !== 'installing' && <div className="actions">
            <button className="btn-secondary" onClick={startUpdate} disabled={downloading}>
              {download?.state === 'verifying' ? 'Verifying…'
                : downloading ? (download?.total ? `Downloading… ${download.percent}%` : 'Downloading…')
                : download?.state === 'ready' ? 'Open installer'
                : download?.state === 'error' ? 'Try again'
                // Do not promise a restart this press will not perform.
                : update?.seamless ? 'Update' : 'Download update'}</button>
          </div>}
          {update?.newer && !installable && <div className="actions">
            <button className="btn-secondary" onClick={openRelease} disabled={releaseBusy}>
              {releaseBusy ? 'Opening…' : 'View release'}</button>
          </div>}</div>
        <div className="srow"><div className="srow-body"><div className="k">Automatic update checks</div>
          {autoUpdateError && <div className="err">{autoUpdateError}</div>}</div>
          <div className="actions"><Switch label="Automatic update checks" checked={autoUpdateOn}
            onChange={setAutoUpdate} busy={autoUpdateBusy} /></div></div>
      </div>
      <h2>Help</h2>
      <div className="group">
        {/* Stacks under 700px: a long path beside two buttons left the path a word per line. */}
        <div className="srow stack-narrow"><div className="srow-body"><div className="k">App data</div><div className="v mono">{breakablePath(s.data_dir)}</div></div>
          <div className="actions"><button className="btn-secondary" onClick={reveal}>{revealLabel(platform)}</button><button className="btn-secondary" onClick={showLogs}>Show logs</button></div></div>
        {onReport && <div className="srow"><div className="srow-body"><div className="k">Report a problem</div>
          <div className="v">Sends the details we need to fix it.</div></div>
          <div className="actions"><button className="btn-secondary" onClick={onReport}>Report a bug…</button></div></div>}
      </div>
      {/* Everything nobody needs on an ordinary day: secrets, sharing, keys, diagnostics, starting over.
          A real button in the heading rather than a <details>, so the box below stays the heading's next
          sibling and a screen reader hears "Advanced, collapsed". */}
      <h2 className="disclosure"><button type="button" aria-expanded={advancedOpen} aria-controls="settings-advanced"
        onClick={() => setAdvancedOpen(o => !o)}>Advanced</button></h2>
      <div className="group" id="settings-advanced" hidden={!advancedOpen}>
        {/* Soulseek has no password reset: the name is bound to the password it was claimed with, and
            Flackey generated both. So the owner has to be able to get this string back -- without it they
            cannot sign in from any other machine, ever, and the account is gone. Behind a press rather than
            printed in the panel, because a secret on screen is a secret over someone's shoulder. */}
        {s.soulseek_enabled && <div className="srow"><div className="srow-body"><div className="k">Soulseek password</div>
          {soulseekPassword
            ? <div className="v mono">{soulseekPassword}</div>
            : <div className="v">Soulseek can't reset it. Keep a copy somewhere safe.</div>}
          {passwordError && <div className="err">{passwordError}</div>}</div>
          <div className="actions">{soulseekPassword
            ? <><CopyButton value={soulseekPassword} />
              <button className="btn-secondary" onClick={() => setSoulseekPassword(null)}>Hide</button></>
            : <button className="btn-secondary" onClick={showSoulseekPassword} disabled={passwordBusy}>
                {passwordBusy ? 'Reading…' : 'Show'}</button>}</div></div>}
        {s.soulseek_enabled && <div className="srow"><div className="srow-body"><div className="k">Helper web login</div>
          <div className="v">For the local slskd page. Not your Soulseek password.</div>
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
        {/* Flackey ships with keys of its own and almost nobody needs to replace them. It exists for the
            owner whose copy was built without keys, or who would rather use their own. */}
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
        {/* Ports and the pick rules are the two things nobody needs until something is wrong. Folded away,
            not dropped: they are the first thing to ask for when a transfer never starts or a copy is refused. */}
        {showTech && <div className="srow"><div className="srow-body">
          <details className="tech">
            <summary>Technical details</summary>
            {s.ports && <><div className="k">Ports</div>
              <div className="v mono">{PORT_ROWS.map(([key, label]) => {
                const p = s.ports![key]
                return <div key={key}>{p.port} — {label} {p.public ? '· open to other Soulseek users' : `· ${thisComputer(platform)} only`}</div>
              })}</div></>}
            {lossless?.enabled && s.ranking && <><div className="k">How copies are ranked</div>
              <div className="v">Files a peer offers are tried nearest the video's length first
                {s.ranking.max_queue != null ? ` (queues longer than ${s.ranking.max_queue} are skipped)` : ''}; the
                recording check decides. Survivors are then ordered by quality and by who can send now. Flackey tries
                up to {s.ranking.max_picks} of them, and keeps a copy only if its fingerprint matches the original
                at {Math.round(s.ranking.fingerprint_min * 100)}% or better.</div></>}
          </details></div></div>}
        <div className="srow"><div className="srow-body"><div className="k">Run setup again</div>
          <div className="v">Keeps your library folder and Telegram sign-in.</div></div>
          <div className="actions"><button className="btn-secondary destructive" onClick={resetSetup} disabled={resettingSetup}>Run setup again…</button></div></div>
      </div>
    </div>
  )
}
