import { useEffect, useState } from 'react'
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
import FolderStep from './components/setup/FolderStep'
import ReadyStep from './components/setup/ReadyStep'
import SetupShell from './components/setup/SetupShell'
import SoulseekStep from './components/setup/SoulseekStep'
import TelegramStep from './components/setup/TelegramStep'
import WelcomeStep from './components/setup/WelcomeStep'
import { useLive } from './live'
import { insetTitlebar, requestWindowSize } from './platform'

const inset = insetTitlebar()

export default function App() {
  const live = useLive()
  const [tab, setTab] = useState<Tab>('download')
  const [selectedPlaylist, setSelectedPlaylist] = useState<number | null>(null)
  const [reconnecting, setReconnecting] = useState(false)
  const [step, setStep] = useState<1 | 2 | 3 | 4>(1)
  const [welcomeSeen, setWelcomeSeen] = useState(false)
  const [libraryRoot, setLibraryRoot] = useState('')
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
  const mainScreen = !!h && h.setup_done && !reconnecting
  // 600, not 540: the Soulseek step carries a heading, a lead, two fields, a warning about a password
  // nothing can reset, two buttons and a footnote, and at 540 that column did not fit -- which is how
  // its heading came to be drawn over the stepper. The window is the thing that was wrong, not the step.
  useEffect(() => { if (h) requestWindowSize(mainScreen ? 1100 : 720, mainScreen ? 720 : 600) }, [h, mainScreen])

  if (!h) {
    return (<div className="app-loading">
      {live.loading ? 'Starting…'
        : <Banner tone="red" text={live.loadError ?? "Can't reach Flackey. Is `crate start` running?"}
                  action={{ label: 'Try again', onClick: () => { live.retry().catch(() => undefined) } }} />}
    </div>)
  }
  if (!h.setup_done || reconnecting) {
    if (!reconnecting && !welcomeSeen) return <WelcomeStep onStart={() => setWelcomeSeen(true)} />
    const current = reconnecting && step === 1 ? 2 : step
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
    const back = reconnecting && current === 2 ? () => { setReconnecting(false); setStep(1) }
      : reconnecting && current === 4 ? () => setStep(2)
      : current === 2 ? () => setStep(1) : current === 3 ? () => setStep(2) : current === 4 ? () => setStep(3)
      : current === 1 && !reconnecting ? () => setWelcomeSeen(false) : undefined
    return (<SetupShell step={current} onBack={back} inset={inset} hint={current === 2 && telegramConfigured ? 'Waiting for Telegram…' : undefined}>
      {current === 1 && <FolderStep initial={libraryRoot} onDone={p => { setLibraryRoot(p); setStep(2) }} />}
      {current === 2 && <TelegramStep onDone={() => { setTelegramSkipped(false); setStep(reconnecting ? 4 : 3) }} onSkip={() => { setTelegramSkipped(true); setStep(reconnecting ? 4 : 3) }} />}
      {current === 3 && <SoulseekStep libraryRoot={libraryRoot} onDone={c => { setSoulseekState(c ? 'connected' : 'pending'); setStep(4) }} onSkip={() => { setSoulseekState('skipped'); setStep(4) }} />}
      {current === 4 && <ReadyStep libraryRoot={libraryRoot} onStart={startApp} error={setupError} telegram={telegramSkipped ? 'skipped' : 'connected'} soulseek={soulseekState} />}
    </SetupShell>)
  }
  const banner = sourceOn && !authorized ? <Banner tone="amber" text="Telegram signed out. Reconnect to keep digging — tracks already filed are untouched." action={{ label: 'Reconnect', onClick: () => setReconnecting(true) }} /> : undefined
  return (
    <Shell tab={tab} onTab={setTab} telegramAuthorized={authorized} lossless={live.health?.lossless} banner={banner} inset={inset} sourceEnabled={sourceOn}
      sidebarExtra={tab === 'library' ? <PlaylistNav playlists={live.playlists} selected={selectedPlaylist} onSelect={setSelectedPlaylist} /> : undefined}>
      {tab === 'download' && <DownloadPage live={live} inset={inset} />}
      {tab === 'library' && <LibraryPage live={live} selectedPlaylist={selectedPlaylist} inset={inset} />}
      {tab === 'uploads' && <UploadsPage inset={inset} />}
      {tab === 'settings' && <SettingsPage live={live} onReconnect={() => setReconnecting(true)} />}
    </Shell>
  )
}
