import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import SettingsPage from './SettingsPage'
import { api, ApiError } from '../api'
import type { AppSettings, Health, LosslessHealth } from '../api'

const makeLive = (over: { settings: AppSettings; health: Health }) =>
  ({ ...over, setSettings: mockSetSettings, refresh: mockRefresh } as never)

const mockSetSettings = vi.fn()
const mockRefresh = vi.fn()
const live = { settings: { library_root: '/Users/me/Music/DJ Library', data_dir: '/Users/me/Library/Application Support/Flackey', version: '0.1.0', telegram_configured: true, log_path: '/Users/me/Library/Application Support/Flackey/flackey.log' },
  health: { ok: true, version: '0.1.0', telegram_authorized: true, worker_running: true, setup_done: true }, setSettings: mockSetSettings, refresh: mockRefresh } as never

beforeEach(() => {
  mockSetSettings.mockClear()
  mockRefresh.mockClear()
  vi.clearAllMocks()
  vi.spyOn(api, 'telegramStatus').mockResolvedValue({ authorized: true, configured: true, phone_masked: '+31 6 •••• ••42' })
  vi.spyOn(api, 'pickFolderAvailable').mockResolvedValue({ available: true })
  mockRefresh.mockResolvedValue(undefined)
})

it('shows the four cards and saves a new folder', async () => {
  const result = { library_root: '/tmp/new', data_dir: '/d', version: '0.1.0', telegram_configured: true, log_path: '/d/flackey.log' }
  vi.spyOn(api, 'saveSettings').mockResolvedValue(result)
  render(<SettingsPage live={live} onReconnect={() => {}} />)
  expect(screen.getByText('/Users/me/Music/DJ Library')).toBeInTheDocument()
  expect(screen.getByText('Flackey 0.1.0')).toBeInTheDocument()
  expect(screen.getByText(/Application Support\/Flackey/)).toBeInTheDocument()
  fireEvent.click(screen.getByText('Change'))
  fireEvent.change(screen.getByDisplayValue('/Users/me/Music/DJ Library'), { target: { value: '/tmp/new' } })
  fireEvent.click(screen.getByText('Save'))
  await waitFor(() => expect(api.saveSettings).toHaveBeenCalledWith('/tmp/new'))
  expect(mockSetSettings).toHaveBeenCalledWith(result)
  expect(screen.queryByDisplayValue('/tmp/new')).not.toBeInTheDocument()
  expect(screen.getByText('Change')).toBeInTheDocument()
})

it('shows error message when saveSettings fails with ApiError', async () => {
  const errorMsg = 'Invalid path'
  vi.spyOn(api, 'saveSettings').mockRejectedValue(new ApiError(400, errorMsg))
  render(<SettingsPage live={live} onReconnect={() => {}} />)
  fireEvent.click(screen.getByText('Change'))
  fireEvent.change(screen.getByDisplayValue('/Users/me/Music/DJ Library'), { target: { value: '/invalid' } })
  fireEvent.click(screen.getByText('Save'))
  await waitFor(() => expect(screen.getByText(errorMsg)).toBeInTheDocument())
  // Edit should still be open
  expect(screen.getByDisplayValue('/invalid')).toBeInTheDocument()
  // Cancel clears the error
  fireEvent.click(screen.getByText('Cancel'))
  expect(screen.queryByText(errorMsg)).not.toBeInTheDocument()
  // Click Change again and error should not reappear
  fireEvent.click(screen.getByText('Change'))
  expect(screen.queryByText(errorMsg)).not.toBeInTheDocument()
})

it('shows error banner when Show in Finder fails', async () => {
  const errorMsg = 'Permission denied'
  vi.spyOn(api, 'reveal').mockRejectedValue(new ApiError(403, errorMsg))
  render(<SettingsPage live={live} onReconnect={() => {}} />)
  fireEvent.click(screen.getByText('Show in Finder'))
  await waitFor(() => expect(screen.getByText(errorMsg)).toBeInTheDocument())
  fireEvent.click(screen.getByText('Dismiss'))
  expect(screen.queryByText(errorMsg)).not.toBeInTheDocument()
})

it('signs out on click', async () => {
  vi.spyOn(api, 'logout').mockResolvedValue({ ok: true })
  render(<SettingsPage live={live} onReconnect={() => {}} />)
  await waitFor(() => expect(screen.getByText('Connected as +31 6 •••• ••42')).toBeInTheDocument())
  fireEvent.click(screen.getByText('Sign out'))
  await waitFor(() => expect(api.logout).toHaveBeenCalled())
})

it('shows a banner when checking the Telegram connection fails', async () => {
  vi.spyOn(api, 'telegramStatus').mockRejectedValue(new ApiError(503, 'Telegram is unavailable right now'))
  render(<SettingsPage live={live} onReconnect={() => {}} />)
  await waitFor(() => expect(screen.getByText('Telegram is unavailable right now')).toBeInTheDocument())
})

it('shows logs when Show logs is clicked', async () => {
  vi.spyOn(api, 'reveal').mockResolvedValue({ ok: true })
  render(<SettingsPage live={live} onReconnect={() => {}} />)
  fireEvent.click(screen.getByText('Show logs'))
  await waitFor(() => expect(api.reveal).toHaveBeenCalledWith('/Users/me/Library/Application Support/Flackey/flackey.log'))
})

it('shows error banner when Show logs fails', async () => {
  const errorMsg = 'Permission denied'
  vi.spyOn(api, 'reveal').mockRejectedValue(new ApiError(403, errorMsg))
  render(<SettingsPage live={live} onReconnect={() => {}} />)
  fireEvent.click(screen.getByText('Show logs'))
  await waitFor(() => expect(screen.getByText(errorMsg)).toBeInTheDocument())
})

it('resets setup on click', async () => {
  vi.spyOn(api, 'setupReset').mockResolvedValue({ setup_done: false })
  render(<SettingsPage live={live} onReconnect={() => {}} />)
  fireEvent.click(screen.getByText('Run setup again…'))
  await waitFor(() => expect(api.setupReset).toHaveBeenCalled())
  await waitFor(() => expect(mockRefresh).toHaveBeenCalled())
})

it('shows error banner when setup reset fails', async () => {
  const errorMsg = 'Could not restart setup. Try again.'
  vi.spyOn(api, 'setupReset').mockRejectedValue(new ApiError(500, errorMsg))
  render(<SettingsPage live={live} onReconnect={() => {}} />)
  fireEvent.click(screen.getByText('Run setup again…'))
  await waitFor(() => expect(screen.getByText(errorMsg)).toBeInTheDocument())
})

it('fills the field when Choose… returns a path', async () => {
  vi.spyOn(api, 'pickFolder').mockResolvedValue({ path: '/Users/me/Music/Crates' })
  render(<SettingsPage live={live} onReconnect={() => {}} />)
  fireEvent.click(screen.getByText('Change'))
  await screen.findByText('Choose…')
  fireEvent.click(screen.getByText('Choose…'))
  await waitFor(() => expect(screen.getByDisplayValue('/Users/me/Music/Crates')).toBeInTheDocument())
})

describe('the Soulseek panel', () => {
  const settings = (over: Partial<AppSettings> = {}): AppSettings => ({
    library_root: '/tmp/lib', data_dir: '/tmp/data', version: '0.1.0', telegram_configured: true,
    log_path: '/tmp/data/flackey.log', soulseek_enabled: true, lossless_filing_format: 'wav',
    filing_formats: ['aiff', 'wav', 'flac'],
    ports: {
      app: { port: 8765, host: '127.0.0.1', public: false },
      sidecar: { port: 5030, host: '127.0.0.1', public: false },
      soulseek_listen: { port: 50300, host: '0.0.0.0', public: true },
    },
    ranking: { max_picks: 4, duration_tolerance_s: 3, title_ratio: 90, require_artist: false,
      max_queue: null, fingerprint_min: 0.9 },
    ...over,
  })
  const lossless = (over: Partial<LosslessHealth> = {}): LosslessHealth =>
    ({ enabled: true, provider: null, fpcalc: true, attempts_24h: {}, raw_mb: 0, ...over })

  const show = (l?: LosslessHealth, st: AppSettings = settings()) =>
    render(<SettingsPage live={makeLive({ settings: st, health: { ok: true, version: '0.1.0',
      telegram_authorized: true, worker_running: true, setup_done: true, lossless: l } })} onReconnect={vi.fn()} />)

  it('names the account once the helper has actually answered', () => {
    show(lossless({ provider: { name: 'soulseek', status: 'ok', username: 'digger' } }))
    expect(screen.getByText('Connected as digger')).toBeInTheDocument()
  })

  it('does not claim a connection before the first probe lands', () => {
    show(lossless({ provider: null }))
    expect(screen.getByText('Starting…')).toBeInTheDocument()
  })

  it('says only the Soulseek transfer port is reachable from outside this Mac', () => {
    show(lossless())
    expect(screen.getByText(/50300 — incoming Soulseek transfers · open to other Soulseek users/)).toBeInTheDocument()
    expect(screen.getByText(/8765 — Flackey itself · this Mac only/)).toBeInTheDocument()
    expect(screen.getByText(/5030 — the Soulseek helper · this Mac only/)).toBeInTheDocument()
  })

  it('shows the current file format as chosen rather than as unavailable', () => {
    // A disabled button reads "you can't have this"; the point is "you already have this".
    show(lossless())
    expect(screen.getByRole('button', { name: 'WAV' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: 'WAV' })).not.toBeDisabled()
    expect(screen.getByRole('button', { name: 'AIFF' })).toHaveAttribute('aria-pressed', 'false')
  })

  it('offers a way out of a stuck sign-in instead of leaving the owner watching it', () => {
    const connect = vi.spyOn(api, 'connectSoulseek')
      .mockResolvedValue({ state: 'connecting', username: null, error: null })
    show(lossless({ provider: { name: 'soulseek', status: 'not_logged_in', username: null } }))
    expect(screen.getByText(/somebody else may already use it/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(connect).toHaveBeenCalled()
  })

  it('offers Reconnect even when the connection is healthy', () => {
    show(lossless({ provider: { name: 'soulseek', status: 'ok', username: 'digger' } }))
    expect(screen.getByRole('button', { name: 'Reconnect' })).toBeInTheDocument()
  })

  it('has no reconnect button when there is no account to reconnect', () => {
    show(lossless({ enabled: false }), settings({ soulseek_enabled: false }))
    expect(screen.queryByRole('button', { name: /Reconnect|Try again/ })).not.toBeInTheDocument()
  })

  it('saves a new format through the settings endpoint', async () => {
    const saveSettings = vi.spyOn(api, 'saveSettings').mockResolvedValue(settings({ lossless_filing_format: 'aiff' }))
    show(lossless())
    fireEvent.click(screen.getByRole('button', { name: 'AIFF' }))
    await waitFor(() => expect(saveSettings).toHaveBeenCalledWith('/tmp/lib', { lossless_filing_format: 'aiff' }))
  })

  it('hides the format and ranking rows when no account is set up, and says why', () => {
    show(lossless({ enabled: false }), settings({ soulseek_enabled: false }))
    expect(screen.getByText(/Not set up — run setup again/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'WAV' })).not.toBeInTheDocument()
  })
})
