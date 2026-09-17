import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import App from './App'
import { api } from './api'
import type { AppSettings, Health, Stats } from './api'

vi.mock('qrcode', () => ({ default: { toDataURL: vi.fn(async () => 'data:image/png;base64,AAA') } }))

class FakeEventSource {
  static last: FakeEventSource | null = null
  onopen: (() => void) | null = null
  handlers: Record<string, (e: { data: string }) => void> = {}
  addEventListener(name: string, fn: (e: { data: string }) => void) { this.handlers[name] = fn }
  emit(name: string, data: unknown) { this.handlers[name]?.({ data: JSON.stringify(data) }) }
  close() { /* no-op */ }
  constructor(_url: string) { FakeEventSource.last = this }
}

const settings: AppSettings = { library_root: '/lib', data_dir: '/data', version: '0.1.0',
  telegram_configured: true, log_path: '/data/flackey.log' }
const stats: Stats = { tracks: 0, bytes: 0, playlists: 0, rejections: 0, library_root: '/lib',
  playlist_dir: '/lib/Playlists', requests_by_state: {} }
const health = (over: Partial<Health> = {}): Health => ({ ok: true, version: '0.1.0',
  telegram_authorized: false, worker_running: false, setup_done: true, source_enabled: true,
  telegram_configured: true, lossless: { enabled: false, provider: null, fpcalc: true, attempts_24h: {}, raw_mb: 0 },
  ...over })

beforeEach(() => {
  vi.stubGlobal('EventSource', FakeEventSource)
  vi.spyOn(api, 'queue').mockResolvedValue([])
  vi.spyOn(api, 'settings').mockResolvedValue(settings)
  vi.spyOn(api, 'playlists').mockResolvedValue([])
  vi.spyOn(api, 'stats').mockResolvedValue(stats)
  vi.spyOn(api, 'qrStart').mockResolvedValue({ id: 'q1', url: 'tg://x', expires_at: '2999-01-01T00:00:00+00:00' })
  vi.spyOn(api, 'qrState').mockResolvedValue({ state: 'waiting' })
  vi.spyOn(api, 'update').mockResolvedValue({ ok: true, current: '0.1.0', newer: false, available: false,
    latest: '0.1.0', url: null, release_url: null, size: null, size_label: null, published_at: null,
    published_date: null, prerelease: false })
})

/** Signed out, so the sidebar offers Reconnect: the shortest way into setup step 2 from the main screen. */
async function reconnect(over: Partial<Health> = {}) {
  vi.spyOn(api, 'health').mockResolvedValue(health(over))
  render(<App />)
  fireEvent.click(await screen.findByRole('button', { name: 'Reconnect' }))
  await screen.findByText('Telegram')
}

describe('the sidebar after a Reconnect sign-in', () => {
  it('turns the overall indicator green as soon as sign-in lands', async () => {
    vi.spyOn(api, 'health').mockResolvedValue(health({ source_enabled: false, telegram_authorized: false }))
    render(<App />)
    expect(await screen.findByText('Not connected')).toBeInTheDocument()

    const es = FakeEventSource.last!
    es.emit('status', { telegram_authorized: true, source_enabled: true, worker_running: false, setup_done: true })
    await waitFor(() => expect(screen.getByText('Connected')).toBeInTheDocument())
    expect(screen.queryByText('Not connected')).not.toBeInTheDocument()
  })

  it('refetches health on the way out of the wizard, so an old event shape cannot strand it', async () => {
    vi.spyOn(api, 'telegramStatus').mockResolvedValue({ authorized: false, configured: true, phone_masked: null })
    await reconnect()
    const health_ = vi.mocked(api.health)
    const before = health_.mock.calls.length

    health_.mockResolvedValue(health({ telegram_authorized: true, source_enabled: true }))
    FakeEventSource.last!.emit('status', { telegram_authorized: true })
    await waitFor(() => expect(health_.mock.calls.length).toBe(before + 1))
    await waitFor(() => expect(screen.getByText('Connected')).toBeInTheDocument())
  })
})

describe('reconnect and a live Soulseek connection', () => {
  it('never claims Soulseek was skipped, since reconnect only re-runs the Telegram step', async () => {
    vi.spyOn(api, 'telegramStatus').mockResolvedValue({ authorized: false, configured: true, phone_masked: null })
    vi.spyOn(api, 'skipTelegram').mockResolvedValue({ source_enabled: false })
    await reconnect({ lossless: { enabled: true, provider: { name: 'slskd', status: 'ok', username: 'dj' }, fpcalc: true, attempts_24h: {}, raw_mb: 0 } })

    fireEvent.click(screen.getByText('Skip for now'))   // skips Telegram, legitimately -- the Soulseek step never opens
    await screen.findByText("You're set")   // not "Almost set" -- Soulseek is actually connected
    expect(screen.queryByText('Connect a source')).not.toBeInTheDocument()
    const soulseekStep = [...document.querySelectorAll('.step')].find(el => el.textContent?.includes('Soulseek'))
    expect(soulseekStep).not.toHaveClass('skipped')
  })
})

describe('the update banner', () => {
  const availableUpdate = async () => {
    vi.spyOn(api, 'health').mockResolvedValue(health({ telegram_authorized: true }))
    vi.spyOn(api, 'update').mockResolvedValue({ ok: true, current: '0.1.2', newer: true, available: true,
      latest: '0.1.3', url: 'https://example.test/Flackey.pkg', release_url: 'https://example.test/releases/v0.1.3',
      size: 12345678, size_label: '12.3 MB', published_at: '2026-09-17T10:38:25Z', published_date: '2026-09-17',
      prerelease: false })
  }

  it('points at the row that can actually do something about it', async () => {
    const open = vi.spyOn(window, 'open').mockImplementation(() => null)
    await availableUpdate()
    render(<App />)
    fireEvent.click(await screen.findByText('Update'))
    // Settings, where the progress is shown -- not a window the webview would silently drop.
    await screen.findByText('Automatic update checks')
    expect(open).not.toHaveBeenCalled()
  })

  it('can be waved away, and leaves the sidebar dot behind when it is', async () => {
    await availableUpdate()
    render(<App />)
    await screen.findByText('Flackey 0.1.3 is available.')
    expect(document.querySelector('.nav-badge')).toBeInTheDocument()
    fireEvent.click(screen.getByLabelText('Dismiss'))
    await waitFor(() => expect(screen.queryByText('Flackey 0.1.3 is available.')).not.toBeInTheDocument())
    expect(document.querySelector('.nav-badge')).toBeInTheDocument()
  })

  it('follows the download once one is running', async () => {
    await availableUpdate()
    render(<App />)
    await screen.findByText('Flackey 0.1.3 is available.')
    FakeEventSource.last?.emit('status', { update_download: { state: 'downloading', percent: 37,
      received: 4_500_000, total: 12_345_678, version: '0.1.3', path: null, error: null } })
    await screen.findByText('Downloading Flackey 0.1.3… 37%')
  })

  it('stays silent when automatic checks are off', async () => {
    vi.spyOn(api, 'health').mockResolvedValue(health({ telegram_authorized: true }))
    vi.spyOn(api, 'settings').mockResolvedValue({ ...settings, auto_update_check: false })
    const check = vi.spyOn(api, 'update')
    render(<App />)
    await screen.findByText('Paste a link above to start digging.')
    expect(check).not.toHaveBeenCalled()
    expect(document.querySelector('.nav-badge')).not.toBeInTheDocument()
  })

  it('stays quiet when a newer version exists but has no installer yet', async () => {
    vi.spyOn(api, 'health').mockResolvedValue(health({ telegram_authorized: true }))
    vi.spyOn(api, 'update').mockResolvedValue({ ok: true, current: '0.1.2', newer: true, available: false,
      latest: '0.1.3', url: null, release_url: 'https://example.test/releases/v0.1.3', size: null,
      size_label: null, published_at: '2026-09-17T10:38:25Z', published_date: '2026-09-17', prerelease: false })
    render(<App />)
    await screen.findByText('Download')   // the sidebar/tab label, proof the main screen rendered
    expect(screen.queryByText(/is available/)).not.toBeInTheDocument()
  })

  it('never checks when the owner turned auto-update checks off', async () => {
    const updateSpy = vi.spyOn(api, 'update')
    vi.spyOn(api, 'health').mockResolvedValue(health({ telegram_authorized: true }))
    vi.spyOn(api, 'settings').mockResolvedValue({ ...settings, auto_update_check: false })
    render(<App />)
    await screen.findByText('Download')
    expect(updateSpy).not.toHaveBeenCalled()
  })
})

describe('the setup hint on the Telegram step', () => {
  it('is absent while the step is still asking for API keys', async () => {
    vi.spyOn(api, 'telegramStatus').mockResolvedValue({ authorized: false, configured: false, phone_masked: null })
    await reconnect({ telegram_configured: false })
    await screen.findByText(/needs Telegram API keys/)
    expect(screen.queryByText('Waiting for Telegram…')).not.toBeInTheDocument()
  })

  it('is there once the copy has keys and the step is the sign-in', async () => {
    vi.spyOn(api, 'telegramStatus').mockResolvedValue({ authorized: false, configured: true, phone_masked: null })
    await reconnect({ telegram_configured: true })
    expect(await screen.findByText('Waiting for Telegram…')).toBeInTheDocument()
  })
})
