import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import SettingsPage from './SettingsPage'
import { api, ApiError } from '../api'
import type { AppSettings, Health, LosslessHealth, SharingState } from '../api'

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
    log_path: '/tmp/data/flackey.log', soulseek_enabled: true, lossless_filing_format: 'aiff',
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

  const sharing = (over: Partial<SharingState> = {}): SharingState =>
    ({ port: 50300, enabled: true, checking: false, mapping: null, reachable: null,
      public_ip: null, lan_ip: null, gateway: null, checked_at: null, error: null, ...over })

  const show = (l?: LosslessHealth, st: AppSettings = settings(), sh: SharingState | null = null) =>
    render(<SettingsPage live={makeLive({ settings: st, health: { ok: true, version: '0.1.0',
      telegram_authorized: true, worker_running: true, setup_done: true, lossless: l, sharing: sh } })} onReconnect={vi.fn()} />)

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

  it('keeps the ports and the pick rules folded away until they are asked for', () => {
    // Both are diagnostics. They stay in the panel -- the owner needs them the day a transfer never
    // starts -- but a first read of Settings must not open on a port table.
    show(lossless())
    const details = screen.getByText('Technical details').closest('details')!
    expect(details).not.toHaveAttribute('open')
    expect(details).toContainElement(screen.getByText(/50300 — incoming Soulseek transfers/))
    expect(details).toContainElement(screen.getByText(/keeps a copy only if its fingerprint matches/))
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

  it('offers no format to choose: on a Mac there is one right answer', () => {
    // Rekordbox reads no ID3 tag out of a WAV, so a WAV files with no artist, genre or cover -- and FLAC is
    // not playable on every CDJ. AIFF is the only one that is both, which leaves nothing to ask the owner.
    show(lossless())
    expect(screen.queryByRole('group', { name: 'File format' })).not.toBeInTheDocument()
    expect(screen.queryByText('File format')).not.toBeInTheDocument()
  })

  it('does not fetch the saved password until it is asked for, and hides it again', async () => {
    // Soulseek cannot reset a password, so the owner has to be able to get this one back -- but a secret
    // printed in the panel is a secret over their shoulder. It is fetched on the press, not on render.
    const password = vi.spyOn(api, 'soulseekPassword')
      .mockResolvedValue({ username: 'digger', password: 'not-a-real-password' })
    show(lossless({ provider: { name: 'soulseek', status: 'ok', username: 'digger' } }))
    expect(password).not.toHaveBeenCalled()
    expect(screen.queryByText('not-a-real-password')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Show' }))
    await waitFor(() => expect(screen.getByText('not-a-real-password')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'Hide' }))
    expect(screen.queryByText('not-a-real-password')).not.toBeInTheDocument()
  })

  it('has nothing to show when no Soulseek account was ever saved', () => {
    show(lossless({ enabled: false }), settings({ soulseek_enabled: false }))
    expect(screen.queryByRole('button', { name: 'Show' })).not.toBeInTheDocument()
  })

  it('hides the format and ranking rows when no account is set up, and says why', () => {
    show(lossless({ enabled: false }), settings({ soulseek_enabled: false }))
    expect(screen.getByText(/Not set up — run setup again/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'WAV' })).not.toBeInTheDocument()
  })

  it('says whether other people can reach you, and how to open the port when they cannot', () => {
    show(lossless(), settings(), sharing({ reachable: false, lan_ip: '10.0.0.5', gateway: '10.0.0.1' }))
    expect(screen.getByText('Sharing')).toBeInTheDocument()
    expect(screen.getByText(/Your Soulseek port is closed/)).toBeInTheDocument()
    expect(screen.getByText(/forward TCP port 50300 on your router to this Mac \(10\.0\.0\.5\)/)).toBeInTheDocument()
  })

  it('asks the server to check again on request', async () => {
    const check = vi.spyOn(api, 'checkSharing').mockResolvedValue(sharing({ checking: true }))
    show(lossless(), settings(), sharing({ reachable: true }))
    expect(screen.getByText('Other Soulseek users can download from you.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Check again' }))
    await waitFor(() => expect(check).toHaveBeenCalled())
  })

  it('has no sharing row when no Soulseek account was ever saved', () => {
    show(lossless({ enabled: false }), settings({ soulseek_enabled: false }), sharing({ reachable: false }))
    expect(screen.queryByText('Sharing')).not.toBeInTheDocument()
    expect(screen.queryByText(/Your Soulseek port is closed/)).not.toBeInTheDocument()
  })
})

describe('using your own Telegram API keys', () => {
  const open = () => {
    render(<SettingsPage live={live} onReconnect={() => {}} />)
    fireEvent.click(screen.getByText('Use your own Telegram API keys'))
  }

  it('saves the pair and tells the owner what to do next, without echoing the hash back', async () => {
    // The hash is a secret the server keeps. Printing a saved one back into the field would put it on
    // screen for anyone walking past, so the fields are cleared rather than refilled.
    const keys = vi.spyOn(api, 'telegramKeys').mockResolvedValue({ configured: true })
    open()
    fireEvent.change(screen.getByLabelText('API ID'), { target: { value: '12345' } })
    fireEvent.change(screen.getByLabelText('API hash'), { target: { value: 'not-a-real-hash' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save keys' }))
    await waitFor(() => expect(screen.getByText('Saved. Sign in again from the Telegram row.')).toBeInTheDocument())
    expect(keys).toHaveBeenCalledWith('12345', 'not-a-real-hash')
    expect(screen.queryByDisplayValue('not-a-real-hash')).not.toBeInTheDocument()
  })

  it('shows the server\'s own reason when the keys are refused', async () => {
    vi.spyOn(api, 'telegramKeys').mockRejectedValue(new ApiError(400, 'That API ID is not a number.'))
    open()
    fireEvent.change(screen.getByLabelText('API ID'), { target: { value: 'nope' } })
    fireEvent.change(screen.getByLabelText('API hash'), { target: { value: 'not-a-real-hash' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save keys' }))
    await waitFor(() => expect(screen.getByText('That API ID is not a number.')).toBeInTheDocument())
    expect(screen.queryByText('Saved. Sign in again from the Telegram row.')).not.toBeInTheDocument()
  })

  it('stays folded away, and cannot be saved half-filled', () => {
    // Almost nobody needs their own keys; the ones Flackey ships with work. So the override is folded
    // shut on arrival, the same way the ports table is.
    render(<SettingsPage live={live} onReconnect={() => {}} />)
    expect(screen.getByText('Use your own Telegram API keys').closest('details')).not.toHaveAttribute('open')
    expect(screen.getByRole('button', { name: 'Save keys' })).toBeDisabled()
    fireEvent.change(screen.getByLabelText('API ID'), { target: { value: '12345' } })
    expect(screen.getByRole('button', { name: 'Save keys' })).toBeDisabled()
    fireEvent.change(screen.getByLabelText('API hash'), { target: { value: 'not-a-real-hash' } })
    expect(screen.getByRole('button', { name: 'Save keys' })).not.toBeDisabled()
  })
})
