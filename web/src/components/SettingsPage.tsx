import { Fragment, useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../api'
import type { AppSettings, ExtendedMixes, LosslessHealth, Platform, SharingState, TelegramStatus, UpdateStatus } from '../api'
import type { Live } from '../live'
import { revealLabel, thisComputer, usePlatform } from '../platform'
import Banner from './Banner'
import CopyButton from './CopyButton'
import { FORMAT_LABELS, shortFormatNote } from './FormatOptions'
import LayoutOptions from './LayoutOptions'
import PrivacySection from './PrivacySection'
import Segmented from './Segmented'
import SharingPanel from './SharingPanel'
import Switch from './Switch'

const getErrorMessage = (e: unknown, fallback: string): string => e instanceof ApiError ? e.message : fallback

const PORT_ROWS: [keyof NonNullable<AppSettings['ports']>, string][] = [
  ['app', 'Flackey itself'], ['sidecar', 'the Soulseek helper'], ['soulseek_listen', 'incoming Soulseek transfers'],
]

/* Same two questions the sidebar keeps apart: `enabled` means credentials are saved, `provider.status`
   means the helper answered a probe just now. Neither one alone is "connected". "Starting…" gets a
   pulsing grey dot rather than amber, which beside it read as a fault; "Signing in…" stays amber,
   because a sign-in that never finishes is the fault its hint warns about. */
type Dot = '' | ' amber' | ' off' | ' pending'
function soulseekLine(l: LosslessHealth | undefined, platform: Platform): { ok: boolean; dot: Dot; text: string; hint?: string } {
  if (!l?.enabled) return { ok: false, dot: ' amber', text: 'Not set up — run setup again, under Advanced' }
  if (l.provider === null) return { ok: false, dot: ' pending', text: 'Starting…' }
  if (l.provider.status === 'ok') return { ok: true, dot: '', text: `Connected as ${l.provider.username ?? 'your account'}` }
  if (l.provider.status === 'not_logged_in') {
    return { ok: false, dot: ' amber', text: 'Signing in…', hint: 'If it stays here, the name may already be taken.' }
  }
  return { ok: false, dot: ' amber', text: 'Not reachable', hint: `The Soulseek helper isn't answering on ${thisComputer(platform)}.` }
}

/* The sharing port, read the way the sidebar reads it: amber only once the server has seen it closed on
   two checks a minute apart (`closed_confirmed`), because port tests false-alarm. One closed reading is
   "checking again", not a fault. `fix` is whether Flackey can do anything about it itself: only while no
   router mapping is held. With one held, the router already said yes and the block is past it -- a VPN or a
   second router -- so the row says that instead of offering a button that cannot help. */
function sharingLine(s: SharingState | null | undefined): { dot: Dot; text: string; hint?: string; fix: boolean } {
  if (!s || s.checking) return { dot: ' pending', text: s ? 'Checking…' : 'Not checked yet', fix: false }
  if (s.reachable === true) return { dot: '', text: `Open — other Soulseek users can reach you on port ${s.port}`, fix: false }
  if (s.reachable === false && s.closed_confirmed) {
    const fix = !!s.gateway && s.mapping === null
    return { dot: ' amber', text: `Closed — fewer Soulseek results until port ${s.port} opens`, fix,
      hint: fix ? 'Fix asks your router to open it again.'
        : 'Your router is not what blocks it. Using a VPN? Turn it off for Flackey, or open the port in the VPN app. More under Advanced › Sharing.' }
  }
  if (s.reachable === false) return { dot: ' pending', text: 'Looked closed once — checking again shortly', fix: false }
  return { dot: ' off', text: s.error ?? 'Not checked yet', fix: false }
}

/* Four readings, not two. The bot is reached *through* the Telegram account above it, so "on" is only
   the whole truth while that account is signed in -- on its own, "On" beside a green dot told an owner
   whose Telegram had dropped that the thing was working. Off is grey rather than amber because the owner
   chose it and nothing is wrong; on-but-unreachable is amber and "paused" -- the worker skips it and
   carries on with Soulseek -- and names Telegram rather than the bot, because Telegram is where the fix is.
   A Telegram that was never set up has no account to be signed out of, so it is never called that: off
   with nothing to sign back into is simply off. */
function deezerBotLine(authorized: boolean, enabled: boolean, unreachable = false, configured = true): { dot: Dot; text: string } {
  const why = !configured ? "Telegram isn't set up" : unreachable ? "can't reach Telegram" : 'Telegram is signed out'
  if (!enabled) return { dot: ' off', text: authorized || !configured ? 'Off — requests use Soulseek only' : `Off — and ${why}` }
  if (!authorized) return { dot: ' amber', text: `Paused — ${why}` }
  return { dot: '', text: 'On — searches and fetches through Telegram' }
}

/* A break opportunity after every separator, so a narrow window wraps a path between folders rather
   than in the middle of "Application". */
const breakablePath = (path: string) => path.split(/([/\\])/).map((part, i) => i % 2 ? <Fragment key={i}>{part}<wbr /></Fragment> : part)

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
  // "Run setup again…" asks first (WEB-11): one stray click used to drop the owner out of Settings and into
  // the wizard. Inline in the row rather than window.confirm, which the packaged WKWebView does not draw.
  const [confirmingReset, setConfirmingReset] = useState(false)
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
  const [moreLibraryOpen, setMoreLibraryOpen] = useState(false)
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
  const [extendedBusy, setExtendedBusy] = useState(false)
  const [extendedError, setExtendedError] = useState<string | null>(null)
  const [installStarting, setInstallStarting] = useState(false)
  const [installError, setInstallError] = useState<string | null>(null)
  const [releaseBusy, setReleaseBusy] = useState(false)
  const [restartBusy, setRestartBusy] = useState(false)
  useEffect(() => {
    // An answer about the sign-in state this page has since moved past must not land over the newer one.
    let current = true
    api.telegramStatus().then(t => { if (current) setTg(t) })
      .catch(e => { if (current) setTgError(getErrorMessage(e, "Couldn't check the Telegram connection.")) })
    return () => { current = false }
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
  const checkForUpdate = useCallback(() => {
    setUpdateChecking(true)
    api.update('manual').then(setUpdate).catch(() => setUpdate({ ok: false, current: s?.version ?? '', newer: false,
      available: false, latest: null, url: null, release_url: null, size: null, size_label: null,
      published_at: null, published_date: null, prerelease: false, error: 'Could not check for updates.' }))
      .finally(() => setUpdateChecking(false))
  }, [s?.version])
  // Opening Settings checks on its own only when the owner has left automatic checks on (WEB-15): with
  // them off, contacting the release server unasked is exactly what the switch promised not to do. Waits
  // for the settings to load, so an off switch is never mistaken for an on one in the meantime.
  const autoCheck = s ? s.auto_update_check !== false : null
  useEffect(() => {
    if (autoCheck === null) return
    if (autoCheck) checkForUpdate()
    else setUpdateChecking(false)
  }, [autoCheck, checkForUpdate])
  if (!s) return null
  // After an action has gone through, the refresh is only there to show it sooner: a failed one must not
  // turn into an error about the action itself (WEB-2).
  const refreshAfter = async () => { try { await live.refresh() } catch { /* the stream catches up */ } }
  async function save() {
    setBusy(true)
    try { live.setSettings(await api.saveSettings(path)); setEditing(false); setErr(null) }
    catch (e) { setErr(getErrorMessage(e, 'Could not save that folder.')) }
    finally { setBusy(false) }
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
    api.setupReset().then(refreshAfter)
      .then(() => setSetupResetError(null))
      .catch(err => setSetupResetError(getErrorMessage(err, 'Could not restart setup. Try again.')))
      .finally(() => { setResettingSetup(false); setConfirmingReset(false) })
  }
  const unreachable = live.health?.telegram_unreachable ?? false
  // No API keys means no account was ever made here: "Signed out" and Reconnect named one that does not
  // exist. Absent from an older server's health, which is the configured case.
  const configured = live.health?.telegram_configured ?? s.telegram_configured ?? true
  const telegramLine = authorized ? (tg?.phone_masked ? `Connected as ${tg.phone_masked}` : 'Connected')
    : !configured ? 'Not set up' : unreachable ? "Can't reach Telegram" : 'Signed out'
  const sourceEnabled = live.health?.source_enabled ?? true
  const bot = deezerBotLine(authorized, sourceEnabled, unreachable, configured)
  const setTelegramSource = (on: boolean) => {
    setSourceBusy(true); setSourceError(null)
    api.telegramSource(on)
      .then(refreshAfter)
      .catch(err => setSourceError(getErrorMessage(err, "Couldn't change the Deezer setting.")))
      .finally(() => setSourceBusy(false))
  }
  const lossless: LosslessHealth | undefined = live.health?.lossless
  const soulseek = soulseekLine(lossless, platform)
  const reconnectSoulseek = () => {
    setReconnecting(true); setSoulseekError(null)
    api.connectSoulseek()
      .then(refreshAfter)
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
  const extendedMixes: ExtendedMixes = s.extended_mixes ?? 'off'
  const saveExtended = (mode: ExtendedMixes) => {
    setExtendedBusy(true); setExtendedError(null)
    api.saveSettings(s.library_root, { extended_mixes: mode })
      .then(next => live.setSettings(next))
      .catch(e => setExtendedError(getErrorMessage(e, 'Could not save that setting.')))
      .finally(() => setExtendedBusy(false))
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
  // The Connections row keeps its own error, so a failure there is not printed twice (once here, once in
  // the Sharing panel under Advanced, which has its own Check again).
  const [portError, setPortError] = useState<string | null>(null)
  const portAction = (fix: boolean) => {
    setPortError(null)
    ;(fix ? api.fixSharing() : api.checkSharing())
      .catch(e => setPortError(getErrorMessage(e, fix ? 'Could not start the fix. Try again.' : 'Could not start the check. Try again.')))
  }
  const sharing = live.health?.sharing
  const port = sharingLine(sharing)
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
  const showTech = !!s.ports || !!(lossless?.enabled && s.ranking) || !!s.soulseek_enabled
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
        {/* Signed out is only a fault while the bot needs it. With the bot off nothing waits on Telegram,
            so the dot goes grey, the way the bot's own row and the sidebar already treat it. Never set up
            is grey too -- nothing broke -- and its button opens the same setup step, named for what it does. */}
        <div className="srow"><div className="srow-body"><div className="k">Telegram</div>
          <div className="v"><span className={`status-dot${authorized ? '' : sourceEnabled && configured ? ' amber' : ' off'}`} />{telegramLine}</div></div>
          <div className="actions">{authorized ? <button className="btn-secondary" onClick={signOut} disabled={signingOut}>Sign out</button>
            : <button className="btn-secondary" onClick={onReconnect}>{configured ? 'Reconnect' : 'Set up'}</button>}</div></div>
        {/* The bot is a chat Flackey holds inside the Telegram account, not a fourth account to sign into,
            and the name says so -- "Deezer (through Telegram)" -- rather than with an indent to decode. Its
            switch is always there and Reconnect lives on the Telegram row alone: two Reconnects stacked one
            above the other read as two different fixes. Switched on while signed out, it reads as paused. */}
        <div className="srow"><div className="srow-body"><div className="k">Deezer <span className="k-note">(through Telegram)</span></div>
          <div className="v"><span className={`status-dot${bot.dot}`} />{bot.text}</div>
          {sourceError && <div className="err">{sourceError}</div>}</div>
          <div className="actions"><Switch label="Deezer (through Telegram)" checked={sourceEnabled} onChange={setTelegramSource} busy={sourceBusy} /></div></div>
        <div className="srow"><div className="srow-body"><div className="k">Soulseek</div>
          <div className="v"><span className={`status-dot${soulseek.dot}`} />{soulseek.text}</div>
          {soulseek.hint && <div className="v">{soulseek.hint}</div>}
          {soulseekError && <div className="err">{soulseekError}</div>}</div>
          <div className="actions">{lossless?.enabled &&
            <button className="btn-secondary" onClick={reconnectSoulseek} disabled={reconnecting}>
              {reconnecting ? 'Reconnecting…' : soulseek.ok ? 'Reconnect' : 'Try again'}</button>}</div></div>
        {/* The third light: Soulseek works without it, but a closed port cuts how many peers can answer.
            Where the sidebar's amber "port closed" lands. Fix only where Flackey can act (see sharingLine);
            the router-by-hand instructions stay under Advanced › Sharing. */}
        {lossless?.enabled && <div className="srow"><div className="srow-body"><div className="k">Sharing port</div>
          <div className="v"><span className={`status-dot${port.dot}`} />{port.text}</div>
          {port.hint && <div className="v">{port.hint}</div>}
          {portError && <div className="err">{portError}</div>}</div>
          <div className="actions">{port.fix
            ? <button className="btn-secondary" onClick={() => portAction(true)} aria-label="Fix the sharing port">Fix</button>
            : <button className="btn-secondary" onClick={() => portAction(false)} disabled={!!sharing?.checking}
                aria-label="Check the sharing port again">
                {sharing?.checking ? 'Checking…' : 'Check again'}</button>}</div></div>}
      </div>
      {/* Where tracks land and what they land as. The format governs every lossless download, whichever
          source fetched it, so it sits with the folder they all land in rather than with Soulseek. */}
      <h2>Library</h2>
      <div className="group">
        <div className="srow stack-narrow"><div className="srow-body"><div className="k">Library folder</div>
          {!editing && <div className="v">Where Flackey saves your tracks.</div>}
          {editing ? <><input className="input" value={path} onChange={e => setPath(e.target.value)} />{err && <div className="err">{err}</div>}</> : <div className="v mono">{breakablePath(s.library_root)}</div>}</div>
          <div className="actions">{editing
            ? <>{pickerAvailable && <button className="btn-secondary" onClick={chooseFolderClicked} disabled={busy}>Choose…</button>}<button className="btn-secondary" onClick={() => { setEditing(false); setErr(null) }} disabled={busy}>Cancel</button><button className="btn-primary" onClick={save} disabled={busy}>Save</button></>
            : <button className="btn-secondary" onClick={() => { setPath(s.library_root); setEditing(true); setErr(null) }}>Change</button>}</div></div>
        {/* Issue #187. On by default, and asking: every download is still the version asked for. In the
            Library box, not a box of its own: it is the one download choice a DJ changes, and a heading over
            a single row cost the page a screen. */}
        <div className="srow"><div className="srow-body"><div className="k">Offer extended mixes when available</div>
          {/* By mode: in "always" nothing is offered -- the longer mix is simply fetched -- and the row that
              says so is inside the fold, so this line has to be right on its own. */}
          <div className="v">{extendedMixes === 'always'
            ? 'Gets the longer extended or club mix instead, when there is one.'
            : 'If a longer extended or club mix turns up, its Done row offers it.'}</div>
          {extendedError && <div className="err">{extendedError}</div>}</div>
          <div className="actions"><Switch label="Offer extended mixes when available" checked={extendedMixes !== 'off'}
            onChange={on => saveExtended(on ? 'ask' : 'off')} busy={extendedBusy} /></div></div>
        {/* Set once, at setup, and seldom again: folded so the box answers "where do my tracks go" at a
            glance. Closed on every visit, like Advanced. An error inside opens nothing by itself, but every
            save here starts from a press inside the fold, so the error is drawn where the press was. */}
        <div className="srow fold-row"><button type="button" className="fold" aria-expanded={moreLibraryOpen}
          aria-controls="settings-more-library" onClick={() => setMoreLibraryOpen(o => !o)}>More library options</button></div>
        <div className="fold-body" id="settings-more-library" hidden={!moreLibraryOpen}>
          {/* Only new downloads follow the layout. Rekordbox finds a track by its full path, so moving the
              ones already filed would leave every one of them "missing" there until relocated by hand. */}
          <div className="srow"><div className="srow-body"><div className="k">Folder layout</div>
            <div className="v">For new downloads. Filed tracks stay put for Rekordbox.</div>
            <LayoutOptions value={currentLayout} onChange={saveLayout} busy={layoutBusy} />
            {layoutError && <div className="err">{layoutError}</div>}</div></div>
          <div className="srow stack-narrow"><div className="srow-body"><div className="k">File format</div>
            <div className="v">{shortFormatNote(currentFormat, defaultFormat)}</div>
            {formatError && <div className="err">{formatError}</div>}</div>
            <div className="actions"><Segmented label="File format" options={formatOptions} value={currentFormat}
              onChange={saveFormat} busy={formatBusy} /></div></div>
          {extendedMixes !== 'off' && (
            <div className="srow stack-narrow"><div className="srow-body"><div className="k">When an extended mix is found</div>
              <div className="v">{extendedMixes === 'always'
                ? 'Look for the extended mix first and download it instead. If there is none, you get the normal one.'
                : 'Ask on the Done row. Nothing waits for your answer.'}</div></div>
              <div className="actions"><Segmented label="When one is found"
                options={[['ask', 'Always ask'], ['always', 'Always get extended']] as const}
                value={extendedMixes} onChange={saveExtended} busy={extendedBusy} /></div></div>)}
        </div>
      </div>
      <h2>Updates</h2>
      <div className="group" ref={updateRow}>
        <div className="srow"><div className="srow-body"><div className="k">App version</div>
          <div className="v">Flackey {s.version}</div>
          {updateChecking && <div className="v">Checking for updates…</div>}
          {!updateChecking && !update && s.auto_update_check === false && <div className="v">
            Automatic update checks are off.
            <button className="btn-secondary" onClick={checkForUpdate}>Check now</button></div>}
          {!updateChecking && update?.ok && !update.newer && <div className="v">Up to date.</div>}
          {!updateChecking && installable && <div className="v">
            Version {update.latest} is available{update.size_label ? ` · ${update.size_label}` : ''}{update.published_date ? ` · ${update.published_date}` : ''}.
          </div>}
          {!updateChecking && update?.ok && update.newer && update.elsewhere && <div className="v">
            Version {update.latest} is out. This copy runs from {update.elsewhere}, so it can't update itself. Open Flackey from your Applications folder to update.
          </div>}
          {!updateChecking && update?.ok && update.newer && !installable && !update.elsewhere && update.verifiable !== false && <div className="v">
            Version {update.latest} is out, but the installer isn't published yet. Check back shortly.
          </div>}
          {!updateChecking && update?.ok && update.newer && !installable && !update.elsewhere && update.verifiable === false && <div className="v">
            Version {update.latest} is out. This copy can't check updates are genuine, so download it from the release page.
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
      </div>
      {/* Privacy (the "Share usage stats" switch) sits on the main view, between Updates and Advanced: a DJ
          asked "does this send anything?" finds the answer without opening the technical fold. Drawn only when
          the build has usage stats at all. */}
      <PrivacySection onReport={onReport} />
      {/* Everything nobody needs on an ordinary day: secrets, sharing, keys, logs, diagnostics, starting
          over. A real button in the heading rather than a <details>, so a screen reader hears "Advanced,
          collapsed", and the line under it says what is inside before anyone opens it. Grouped inside by
          what each thing belongs to, so a DJ sent here to fix one thing finds it under a name they know.
          Stays named "Advanced": the Soulseek row and the Uploads banner both send people "under Advanced". */}
      <div className="disclosure-head">
        <h2 className="disclosure"><button type="button" aria-expanded={advancedOpen} aria-controls="settings-advanced"
          onClick={() => setAdvancedOpen(o => !o)}>Advanced</button></h2>
        <p className="disclosure-note">Update checks, passwords, sharing, problem reports and starting over. Only needed when something isn't working.</p>
      </div>
      <div className="advanced" id="settings-advanced" hidden={!advancedOpen}>
        {/* Set once and left alone: on by default. Off, the update row on the main page checks only when
            its Check now button is pressed. */}
        <h3>General</h3>
        <div className="group">
          <div className="srow"><div className="srow-body"><div className="k">Check for updates automatically</div>
            <div className="v">Tells you when a new version of Flackey is out.</div>
            {autoUpdateError && <div className="err">{autoUpdateError}</div>}</div>
            <div className="actions"><Switch label="Check for updates automatically" checked={autoUpdateOn}
              onChange={setAutoUpdate} busy={autoUpdateBusy} /></div></div>
        </div>
        {s.soulseek_enabled && <>
          <h3>Soulseek account</h3>
          <div className="group">
            {/* Soulseek has no password reset: the name is bound to the password it was claimed with, and
                Flackey generated both. So the owner has to be able to get this string back -- without it they
                cannot sign in from any other machine, ever, and the account is gone. Behind a press rather than
                printed in the panel, because a secret on screen is a secret over someone's shoulder. */}
            <div className="srow"><div className="srow-body"><div className="k">Soulseek password</div>
              {soulseekPassword
                ? <div className="v mono">{soulseekPassword}</div>
                : <div className="v">Soulseek can't reset it. Keep a copy somewhere safe.</div>}
              {passwordError && <div className="err">{passwordError}</div>}</div>
              <div className="actions">{soulseekPassword
                ? <><CopyButton value={soulseekPassword} />
                  <button className="btn-secondary" onClick={() => setSoulseekPassword(null)}>Hide</button></>
                : <button className="btn-secondary" onClick={showSoulseekPassword} disabled={passwordBusy}>
                    {passwordBusy ? 'Reading…' : 'Show'}</button>}</div></div>
            {/* Downloading works behind any router; being downloaded from does not, and an account nobody can
                pull from is the one Soulseek eventually stops trusting. The state arrives on the status event,
                so the answer to a re-check lands here by itself and the response is discarded. */}
            <div className="srow"><div className="srow-body"><div className="k">Sharing</div>
              <SharingPanel state={live.health?.sharing ?? null} onCheck={checkSharing} />
              {checkError && <p className="hint-row warn">{checkError}</p>}</div></div>
          </div>
        </>}
        <h3>Telegram</h3>
        <div className="group">
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
        </div>
        <h3>Troubleshooting</h3>
        <div className="group">
          {onReport && <div className="srow"><div className="srow-body"><div className="k">Report a problem</div>
            <div className="v">Sends the details we need to fix it.</div></div>
            <div className="actions"><button className="btn-secondary" onClick={onReport}>Report a bug…</button></div></div>}
          {/* Stacks under 700px: a long path beside two buttons left the path a word per line. */}
          <div className="srow stack-narrow"><div className="srow-body"><div className="k">App data</div><div className="v mono">{breakablePath(s.data_dir)}</div></div>
            <div className="actions"><button className="btn-secondary" onClick={reveal}>{revealLabel(platform)}</button><button className="btn-secondary" onClick={showLogs}>Show logs</button></div></div>
          {/* Ports and the pick rules are the two things nobody needs until something is wrong. Folded away,
              not dropped: they are the first thing to ask for when a transfer never starts or a copy is refused. */}
          {showTech && <div className="srow"><div className="srow-body">
            <details className="tech">
              <summary>Technical details</summary>
              {/* The helper is slskd, a program Flackey runs for Soulseek; its own web page is a developer's
                  tool, so its login waits in here rather than beside the account password. */}
              {s.soulseek_enabled && <><div className="k">Helper web login</div>
                <div className="v">For the helper's own web page (slskd). Not your Soulseek password.</div>
                {webLogin && <div className="v mono">{webLogin.username} · {webLogin.password}</div>}
                {webLoginError && <div className="err">{webLoginError}</div>}
                <div className="row-gap">{webLogin ? <><CopyButton value={webLogin.password} /><button className="btn-secondary" onClick={() => setWebLogin(null)}>Hide</button></>
                  : <button className="btn-secondary" onClick={showWebLogin}>Show login</button>}</div></>}
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
          {confirmingReset
            ? <div className="srow" role="group" aria-label="Confirm running setup again"><div className="srow-body"><div className="k">Run setup again?</div>
                <div className="v">Setup opens now. Nothing is deleted or signed out.</div></div>
                <div className="actions"><button className="btn-secondary" onClick={() => setConfirmingReset(false)} disabled={resettingSetup}>Cancel</button>
                  <button className="btn-secondary destructive" onClick={resetSetup} disabled={resettingSetup} autoFocus>
                    {resettingSetup ? 'Opening setup…' : 'Run setup'}</button></div></div>
            : <div className="srow"><div className="srow-body"><div className="k">Run setup again</div>
                <div className="v">Keeps your library folder and Telegram sign-in.</div></div>
                <div className="actions"><button className="btn-secondary destructive" onClick={() => setConfirmingReset(true)}>Run setup again…</button></div></div>}
        </div>
      </div>
    </div>
  )
}
