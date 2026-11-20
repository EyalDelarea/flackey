import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import './app.css'
import { api } from './api'
import Banner from './components/Banner'
import Shell from './components/Shell'
import type { Tab } from './components/Sidebar'
import SettingsPage from './components/SettingsPage'
import DownloadPage from './components/download/DownloadPage'
import LibraryPage from './components/library/LibraryPage'
import UploadsPage from './components/uploads/UploadsPage'
import PlaylistNav from './components/library/PlaylistNav'
import ReportBugDialog from './components/ReportBugDialog'
import FormatStep from './components/setup/FormatStep'
import FolderStep from './components/setup/FolderStep'
import ReadyStep from './components/setup/ReadyStep'
import SetupShell from './components/setup/SetupShell'
import SoulseekStep from './components/setup/SoulseekStep'
import TelegramStep from './components/setup/TelegramStep'
import WelcomeStep from './components/setup/WelcomeStep'
import UsageStatsAsk from './components/UsageStatsAsk'
import type { UsageStatsChoice } from './components/UsageStatsAsk'
import { useLive } from './live'
import type { Live } from './live'
import { insetTitlebar, PlatformContext } from './platform'
import { durationBucket, optIn, SETUP_STEPS, track } from './telemetry'
import type { SetupStepName, StepAction } from './telemetry'

const inset = insetTitlebar()

export default function App() {
  const live = useLive()
  return <PlatformContext.Provider value={live.health?.platform ?? 'mac'}><AppBody live={live} /></PlatformContext.Provider>
}

function AppBody({ live }: { live: Live }) {
  const [tab, setTab] = useState<Tab>('download')
  const [selectedPlaylist, setSelectedPlaylist] = useState<number | null>(null)
  const [reconnecting, setReconnecting] = useState(false)
  const [updateDismissed, setUpdateDismissed] = useState(false)
  // What the open report is about: `{}` for the general one, a request id for a failed row's (issue #165).
  const [reporting, setReporting] = useState<{ requestId?: number } | null>(null)
  // A counter, not a boolean: pressing the banner twice should scroll twice, and the second
  // press of a boolean already set to true would change nothing.
  const [focusUpdate, setFocusUpdate] = useState(0)
  const [step, setStep] = useState<1 | 2 | 3 | 4 | 5>(1)
  const [welcomeSeen, setWelcomeSeen] = useState(false)
  const [libraryRoot, setLibraryRoot] = useState('')
  const [filingFormat, setFilingFormat] = useState('aiff')
  const [setupError, setSetupError] = useState<string | null>(null)
  // What the Ready step is allowed to claim, and nothing else: each is set by the step it belongs to.
  const [telegramSkipped, setTelegramSkipped] = useState(false)
  const [soulseekState, setSoulseekState] = useState<'connected' | 'pending' | 'skipped'>('skipped')
  // The one-time usage-stats question for an owner who finished setup before Welcome asked it. Shown only
  // while stats can run here, are off, and the question was never answered; any answer, the × included,
  // is remembered by the server, so it never comes back. Hidden on any error: no prompt beats a broken one.
  const [askUsage, setAskUsage] = useState(false)
  const h = live.health
  const authorized = h?.telegram_authorized ?? true
  // A source the owner switched off is not signed out, so nothing about it is worth a banner.
  const sourceOn = h?.source_enabled ?? true
  // Without keys the Telegram step is a form asking for them, and nothing is waiting on Telegram yet.
  // Absent in a payload written before the field existed, which is the configured case.
  const telegramConfigured = h?.telegram_configured ?? true
  // The refresh is belt and braces: the sign-in's own status event carries `source_enabled`, but a copy
  // whose server predates that would leave the sidebar on "Telegram off" with no way back but a reload.
  // Only on the way out of the wizard, so an ordinary start does not fetch everything twice.
  const refresh = live.refresh
  useEffect(() => { if (authorized && reconnecting) { setReconnecting(false); refresh().catch(() => undefined) } }, [authorized, reconnecting, refresh])
  const setupDone = !!h?.setup_done
  useEffect(() => {
    if (!setupDone) return
    let on = true
    api.telemetry().then(t => { if (on) setAskUsage(!!t?.available && !t.enabled && !t.asked) }).catch(() => undefined)
    return () => { on = false }
  }, [setupDone])
  // Gone at once, whatever the answer: the request runs behind it, and a failure leaves stats off.
  const answerUsage = (c: UsageStatsChoice) => {
    setAskUsage(false)
    if (c === 'share') optIn()
    else api.telemetryAsked().catch(() => undefined)
  }
  useEffect(() => { if (live.settings && !libraryRoot) setLibraryRoot(live.settings.library_root) }, [live.settings, libraryRoot])
  useEffect(() => { if (live.settings?.lossless_filing_format) setFilingFormat(live.settings.lossless_filing_format) }, [live.settings?.lossless_filing_format])
  // Reconnect only re-runs the Telegram step (see `current` below) -- Soulseek's own step never opens, so
  // nothing in the wizard would otherwise set `soulseekState` away from its unvisited default of
  // 'skipped', and Ready would falsely tell a Soulseek-connected owner they skipped it.
  useEffect(() => {
    if (!reconnecting) return
    setSoulseekState(h?.lossless?.provider?.status === 'ok' ? 'connected' : h?.lossless?.enabled ? 'pending' : 'skipped')
  }, [reconnecting])
  // The setup steps used to answer being too tall for the window by resizing the window: the Soulseek
  // step in particular asked for 720x600 on mount and the main shell asked for 1100x720 back. That threw
  // away whatever size the owner had dragged out, twice per run of setup, and snapped the window about
  // mid-session on any re-render that flipped `setup_done`. `.setup-content` scrolls, so a step that does
  // not fit scrolls instead -- the window stays the size it was left at, which desktop.py remembers.

  // Usage-stats funnel: which setup step is on screen, and since when. Posted fire-and-forget, and the
  // server drops it unless the owner opted in. A rerun (Reconnect) is marked so it never reads as a first run.
  const setupStep: SetupStepName | null = !h || (h.setup_done && !reconnecting) ? null
    : !reconnecting && !welcomeSeen ? 'welcome' : SETUP_STEPS[reconnecting && step === 1 ? 3 : step]
  const funnel = useRef<{ step: SetupStepName | null; since: number; started: number }>({ step: null, since: 0, started: 0 })
  // Brings the funnel up to the step this render shows. The effect does it after paint, but a click can
  // land before that effect runs (a fast "Get started"), so leaving a step catches the funnel up first --
  // otherwise it would see no step on screen and the step's `completed` would be lost.
  const enterStep = () => {
    const f = funnel.current
    if (setupStep === f.step) return  // the ref guard keeps StrictMode's second run from posting twice
    const now = Date.now()
    if (f.step === null) f.started = now
    f.step = setupStep; f.since = now
    if (setupStep) track('setup_step', { step: setupStep, action: 'shown', ...(reconnecting ? { rerun: true } : {}) })
  }
  useEffect(enterStep, [setupStep, reconnecting])
  const leaveStep = (action: StepAction) => {
    enterStep()
    const f = funnel.current
    if (!f.step) return
    track('setup_step', { step: f.step, action, time_on_step: durationBucket(Date.now() - f.since), ...(reconnecting ? { rerun: true } : {}) })
  }

  if (!h) {
    return (<div className="app-loading">
      {live.loading ? 'Starting…'
        : <Banner tone="red" text={live.loadError ?? "Can't reach Flackey. Is `flackey start` running?"}
                  action={{ label: 'Try again', onClick: () => { live.retry().catch(() => undefined) } }} />}
    </div>)
  }
  if (!h.setup_done || reconnecting) {
    if (!reconnecting && !welcomeSeen) return <WelcomeStep onStart={() => { leaveStep('completed'); setWelcomeSeen(true) }} />
    const current = reconnecting && step === 1 ? 3 : step
    const startApp = async () => {
      try {
        await api.setupDone()
      } catch {
        setSetupError("Couldn't finish setup. Try again.")
        return
      }
      setSetupError(null)
      leaveStep('completed')
      if (!reconnecting) {
        const fmt: Record<string, string> = ['aiff', 'wav', 'flac'].includes(filingFormat) ? { filing_format: filingFormat } : {}
        track('setup_finished', { total_time: durationBucket(Date.now() - funnel.current.started),
          telegram_connected: !telegramSkipped, soulseek_connected: soulseekState === 'connected', ...fmt })
      }
      await live.refresh()
      setReconnecting(false)
      setStep(1)
    }
    // Refetched on the way back too: keys saved in the step and then backed out of change
    // `telegram_configured`, which only a full health read carries, and Settings would go on saying "Not set up".
    const back = reconnecting && current === 3 ? () => { setReconnecting(false); setStep(1); refresh().catch(() => undefined) }
      : reconnecting && current === 5 ? () => setStep(3)
      : current === 2 ? () => setStep(1) : current === 3 ? () => setStep(2) : current === 4 ? () => setStep(3)
      : current === 5 ? () => setStep(4)
      : current === 1 && !reconnecting ? () => setWelcomeSeen(false) : undefined
    return (<SetupShell step={current} onBack={back && (() => { leaveStep('back'); back() })} inset={inset} hint={current === 3 && telegramConfigured ? 'Waiting for Telegram…' : undefined}
      skipped={{ 3: telegramSkipped, 4: soulseekState === 'skipped' }}>
      {current === 1 && <FolderStep initial={libraryRoot} onDone={p => { leaveStep('completed'); setLibraryRoot(p); setStep(2) }} />}
      {current === 2 && <FormatStep libraryRoot={libraryRoot} initial={filingFormat}
        formats={live.settings?.filing_formats ?? ['aiff', 'wav', 'flac']}
        defaultFormat={live.settings?.default_filing_format}
        onDone={f => { leaveStep('completed'); setFilingFormat(f); setStep(3) }} />}
      {current === 3 && <TelegramStep onDone={() => { leaveStep('completed'); setTelegramSkipped(false); setStep(reconnecting ? 5 : 4) }} onSkip={() => { leaveStep('skipped'); setTelegramSkipped(true); setStep(reconnecting ? 5 : 4) }} />}
      {current === 4 && <SoulseekStep libraryRoot={libraryRoot} onDone={c => { leaveStep('completed'); setSoulseekState(c ? 'connected' : 'pending'); setStep(5) }} onSkip={() => { leaveStep('skipped'); setSoulseekState('skipped'); setStep(5) }} />}
      {current === 5 && <ReadyStep libraryRoot={libraryRoot} onStart={startApp} onConnectSource={() => { leaveStep('back'); setStep(3) }} error={setupError} telegram={telegramSkipped ? 'skipped' : 'connected'} soulseek={soulseekState} />}
    </SetupShell>)
  }
  const update = live.update
  const banners: ReactNode[] = []
  // Only the bot stops: Soulseek has nothing to do with Telegram and keeps downloading. The old line,
  // "Reconnect to keep digging", was true when the whole worker waited on Telegram, which it no longer does.
  // Not "signed out" when Telegram simply did not answer: telling a signed-in owner to sign in sends them
  // after the wrong fix. Reconnect still is the way back -- it asks Telegram again first.
  // A copy with no Telegram keys never had an account to sign out of; the bot still waits on one, so the
  // banner stays, but it names the step that is actually missing.
  if (sourceOn && !authorized) {
    const why = !telegramConfigured ? "Telegram isn't set up" : h.telegram_unreachable ? "can't reach Telegram" : 'Telegram is signed out'
    const fix = telegramConfigured ? 'reconnect' : 'set it up'
    banners.push(<Banner key="telegram" tone="amber" text={`Deezer (through Telegram) paused — ${why}. Soulseek keeps downloading; ${fix} to get Deezer's MP3 copies back.`} action={{ label: telegramConfigured ? 'Reconnect' : 'Set up', onClick: () => setReconnecting(true) }} />)
  }
  // Only when there is something to click: a newer tag with no installer yet is detail for the
  // Settings page, not a launch-time nag with nowhere for the click to go. `live.update` is null when
  // automatic checks are off, so that switch turns this off with it.
  // Dismissal is deliberately session-only: the Settings dot outlives it, and remembering a brush-off
  // across launches would need a new stored setting to say how long "not now" lasts.
  if (update?.available && !updateDismissed) {
    const dl = live.updateDownload
    const text = dl?.state === 'downloading'
      ? `Downloading Flackey ${update.latest}… ${dl.percent}%`
      : dl?.state === 'ready' ? `Flackey ${update.latest} is downloaded — finish in the installer.`
      : `Flackey ${update.latest} is available.`
    // Sends them to the row that shows the download rather than starting one from under a banner that
    // has nowhere to report a failure.
    // `setTab` alone read as a dead button whenever Settings was already the open tab, and even
    // from another tab it left the App version row somewhere below the fold.
    banners.push(<Banner key="update" tone="amber" text={text}
      action={{ label: dl ? 'Show' : 'Update', onClick: () => { setTab('settings'); setFocusUpdate(n => n + 1) } }}
      onDismiss={() => setUpdateDismissed(true)} />)
  }
  if (askUsage) {
    banners.push(<UsageStatsAsk key="usage" className="usage-prompt" value={null} onChoose={answerUsage}
      onDismiss={() => answerUsage('not-now')} />)
  }
  const banner = banners.length ? banners : undefined
  return (
    <Shell tab={tab} onTab={setTab} telegramAuthorized={authorized} lossless={live.health?.lossless} banner={banner} inset={inset} sourceEnabled={sourceOn}
      updateWaiting={!!update?.available} onReport={() => setReporting({})}
      sidebarExtra={tab === 'library' ? <PlaylistNav playlists={live.playlists} selected={selectedPlaylist} onSelect={setSelectedPlaylist} /> : undefined}>
      {tab === 'download' && <DownloadPage live={live} onReport={requestId => setReporting({ requestId })} />}
      {tab === 'library' && <LibraryPage live={live} selectedPlaylist={selectedPlaylist} />}
      {tab === 'uploads' && <UploadsPage />}
      {tab === 'settings' && <SettingsPage live={live} onReconnect={() => setReconnecting(true)} focusUpdate={focusUpdate} onReport={() => setReporting({})} />}
      {reporting && <ReportBugDialog screen={tab} requestId={reporting.requestId} onClose={() => setReporting(null)} />}
    </Shell>
  )
}
