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
  const [soulseekPending, setSoulseekPending] = useState(false)   // saved, but not signed in yet
  const h = live.health
  const authorized = h?.telegram_authorized ?? true
  useEffect(() => { if (authorized) setReconnecting(false) }, [authorized])
  useEffect(() => { if (live.settings && !libraryRoot) setLibraryRoot(live.settings.library_root) }, [live.settings, libraryRoot])
  const mainScreen = !!h && h.setup_done && !reconnecting
  useEffect(() => { if (h) requestWindowSize(mainScreen ? 1100 : 720, mainScreen ? 720 : 540) }, [h, mainScreen])

  if (!h) {
    return (<div className="app-loading">
      {live.loading ? 'Starting…'
        : <Banner tone="red" text={live.loadError ?? "Can't reach Krater. Is `crate start` running?"}
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
    return (<SetupShell step={current} onBack={back} inset={inset} hint={current === 2 ? 'Waiting for Telegram…' : undefined}>
      {current === 1 && <FolderStep initial={libraryRoot} onDone={p => { setLibraryRoot(p); setStep(2) }} />}
      {current === 2 && <TelegramStep onDone={() => setStep(reconnecting ? 4 : 3)} />}
      {current === 3 && <SoulseekStep onDone={c => { setSoulseekPending(!c); setStep(4) }} onSkip={() => setStep(4)} />}
      {current === 4 && <ReadyStep libraryRoot={libraryRoot} onStart={startApp} error={setupError} soulseekPending={soulseekPending} />}
    </SetupShell>)
  }
  const banner = !authorized ? <Banner tone="amber" text="Telegram signed out. Reconnect to keep digging — tracks already filed are untouched." action={{ label: 'Reconnect', onClick: () => setReconnecting(true) }} /> : undefined
  return (
    <Shell tab={tab} onTab={setTab} telegramAuthorized={authorized} lossless={live.health?.lossless} banner={banner} inset={inset}
      sidebarExtra={tab === 'library' ? <PlaylistNav playlists={live.playlists} selected={selectedPlaylist} onSelect={setSelectedPlaylist} /> : undefined}>
      {tab === 'download' && <DownloadPage live={live} inset={inset} />}
      {tab === 'library' && <LibraryPage live={live} selectedPlaylist={selectedPlaylist} inset={inset} />}
      {tab === 'uploads' && <UploadsPage inset={inset} />}
      {tab === 'settings' && <SettingsPage live={live} onReconnect={() => setReconnecting(true)} />}
    </Shell>
  )
}
