import { useEffect, useState } from 'react'
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
import FormatStep from './components/setup/FormatStep'
import FolderStep from './components/setup/FolderStep'
import ReadyStep from './components/setup/ReadyStep'
import SetupShell from './components/setup/SetupShell'
import SoulseekStep from './components/setup/SoulseekStep'
import TelegramStep from './components/setup/TelegramStep'
import WelcomeStep from './components/setup/WelcomeStep'
import { useLive } from './live'
import { insetTitlebar } from './platform'

const inset = insetTitlebar()

export default function App() {
  const live = useLive()
  const [tab, setTab] = useState<Tab>('download')
  const [selectedPlaylist, setSelectedPlaylist] = useState<number | null>(null)
  const [reconnecting, setReconnecting] = useState(false)
  const [updateDismissed, setUpdateDismissed] = useState(false)
  const [step, setStep] = useState<1 | 2 | 3 | 4 | 5>(1)
  const [welcomeSeen, setWelcomeSeen] = useState(false)
  const [libraryRoot, setLibraryRoot] = useState('')
  const [filingFormat, setFilingFormat] = useState('aiff')
  const [setupError, setSetupError] = useState<string | null>(null)
  // What the Ready step is allowed to claim, and nothing else: each is set by the step it belongs to.
  const [telegramSkipped, setTelegramSkipped] = useState(false)
  const [soulseekState, setSoulseekState] = useState<'connected' | 'pending' | 'skipped'>('skipped')
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

  if (!h) {
    return (<div className="app-loading">
      {live.loading ? 'Starting…'
        : <Banner tone="red" text={live.loadError ?? "Can't reach Flackey. Is `flackey start` running?"}
                  action={{ label: 'Try again', onClick: () => { live.retry().catch(() => undefined) } }} />}
    </div>)
  }
  if (!h.setup_done || reconnecting) {
    if (!reconnecting && !welcomeSeen) return <WelcomeStep onStart={() => setWelcomeSeen(true)} />
    const current = reconnecting && step === 1 ? 3 : step
    const startApp = async () => {
      try {
        await api.setupDone()
      } catch {
        setSetupError("Couldn't finish setup. Try again.")
        return
      }
      setSetupError(null)
      await live.refresh()
      setReconnecting(false)
      setStep(1)
    }
    const back = reconnecting && current === 3 ? () => { setReconnecting(false); setStep(1) }
      : reconnecting && current === 5 ? () => setStep(3)
      : current === 2 ? () => setStep(1) : current === 3 ? () => setStep(2) : current === 4 ? () => setStep(3)
      : current === 5 ? () => setStep(4)
      : current === 1 && !reconnecting ? () => setWelcomeSeen(false) : undefined
    return (<SetupShell step={current} onBack={back} inset={inset} hint={current === 3 && telegramConfigured ? 'Waiting for Telegram…' : undefined}
      skipped={{ 3: telegramSkipped, 4: soulseekState === 'skipped' }}>
      {current === 1 && <FolderStep initial={libraryRoot} onDone={p => { setLibraryRoot(p); setStep(2) }} />}
      {current === 2 && <FormatStep libraryRoot={libraryRoot} initial={filingFormat}
        formats={live.settings?.filing_formats ?? ['aiff', 'wav', 'flac']}
        onDone={f => { setFilingFormat(f); setStep(3) }} />}
      {current === 3 && <TelegramStep onDone={() => { setTelegramSkipped(false); setStep(reconnecting ? 5 : 4) }} onSkip={() => { setTelegramSkipped(true); setStep(reconnecting ? 5 : 4) }} />}
      {current === 4 && <SoulseekStep libraryRoot={libraryRoot} onDone={c => { setSoulseekState(c ? 'connected' : 'pending'); setStep(5) }} onSkip={() => { setSoulseekState('skipped'); setStep(5) }} />}
      {current === 5 && <ReadyStep libraryRoot={libraryRoot} onStart={startApp} onConnectSource={() => setStep(3)} error={setupError} telegram={telegramSkipped ? 'skipped' : 'connected'} soulseek={soulseekState} />}
    </SetupShell>)
  }
  const update = live.update
  const banners: ReactNode[] = []
  if (sourceOn && !authorized) {
    banners.push(<Banner key="telegram" tone="amber" text="Telegram signed out. Reconnect to keep digging — tracks already filed are untouched." action={{ label: 'Reconnect', onClick: () => setReconnecting(true) }} />)
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
    banners.push(<Banner key="update" tone="amber" text={text}
      action={{ label: dl ? 'Show' : 'Update', onClick: () => setTab('settings') }}
      onDismiss={() => setUpdateDismissed(true)} />)
  }
  const banner = banners.length ? banners : undefined
  return (
    <Shell tab={tab} onTab={setTab} telegramAuthorized={authorized} lossless={live.health?.lossless} banner={banner} inset={inset} sourceEnabled={sourceOn}
      updateWaiting={!!update?.available}
      sidebarExtra={tab === 'library' ? <PlaylistNav playlists={live.playlists} selected={selectedPlaylist} onSelect={setSelectedPlaylist} /> : undefined}>
      {tab === 'download' && <DownloadPage live={live} />}
      {tab === 'library' && <LibraryPage live={live} selectedPlaylist={selectedPlaylist} />}
      {tab === 'uploads' && <UploadsPage />}
      {tab === 'settings' && <SettingsPage live={live} onReconnect={() => setReconnecting(true)} />}
    </Shell>
  )
}
