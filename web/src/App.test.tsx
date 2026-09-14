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
