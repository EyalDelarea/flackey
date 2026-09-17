import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import SettingsPage from './SettingsPage'
import { api, ApiError } from '../api'
import type { AppSettings, Health, LosslessHealth, SharingState, UpdateDownload } from '../api'

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
  vi.spyOn(api, 'update').mockResolvedValue({ ok: true, current: '0.1.0', newer: false, available: false,
    latest: '0.1.0', url: 'https://example.test/Flackey.pkg', release_url: null, size: 123, size_label: '123 B',
    published_at: null, published_date: null, prerelease: true })
  mockRefresh.mockResolvedValue(undefined)
})

/* The whole point of the Connections section is where things sit, and a `getByText` passes just as
   happily when the three connections are scattered over three cards again. Everything below asks which
   box, and in what order, rather than only whether the words are on screen. */
const section = (name: string) => screen.getByRole('heading', { name }).nextElementSibling as HTMLElement
const connections = () => section('Connections')
/* Scoped to that box on purpose: "Soulseek" is both a connection row and the heading of the box holding
   everything else about it, so an unscoped lookup would have two answers. */
const connRow = (name: string) => within(connections()).getByText(name).closest('.srow') as HTMLElement
const dot = (r: HTMLElement) => r.querySelector('.status-dot') as HTMLElement

it('shows the settings cards and saves a new folder', async () => {
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

const updateAvailable = { ok: true, current: '0.1.0', newer: true, available: true,
  latest: '0.1.1', url: 'https://example.test/Flackey.pkg', release_url: 'https://example.test/releases/v0.1.1',
  size: 12345678, size_label: '12.3 MB', published_at: '2026-09-15T10:00:00Z', published_date: '2026-09-15',
  prerelease: false } as const

const liveWith = (download: UpdateDownload | null) => ({ ...(live as object), updateDownload: download } as never)

it('shows an available app update', async () => {
  vi.spyOn(api, 'update').mockResolvedValue({ ...updateAvailable })
  render(<SettingsPage live={live} onReconnect={() => {}} />)
  await waitFor(() => expect(screen.getByText(/Version 0.1.1 is available/)).toBeInTheDocument())
  expect(screen.getByText('Download update')).toBeInTheDocument()
})

/* The bug this row was rebuilt for: the press went to `window.open`, which does nothing inside the
   webview, so nothing happened and nothing said so. The download is the server's job now. */
it('hands the download to the server rather than opening a window', async () => {
  const open = vi.spyOn(window, 'open').mockImplementation(() => null)
  const install = vi.spyOn(api, 'installUpdate').mockResolvedValue(
    { state: 'downloading', percent: 0, received: 0, total: 12345678, version: '0.1.1', path: null, error: null })
  vi.spyOn(api, 'update').mockResolvedValue({ ...updateAvailable })
  render(<SettingsPage live={live} onReconnect={() => {}} />)
  fireEvent.click(await screen.findByText('Download update'))
  await waitFor(() => expect(install).toHaveBeenCalled())
  expect(open).not.toHaveBeenCalled()
  // Something to look at between the press and the first status event, so the click is never dead.
  expect(screen.getByText('Starting the download…')).toBeInTheDocument()
})

it('shows how far the download has got', async () => {
  vi.spyOn(api, 'update').mockResolvedValue({ ...updateAvailable })
  render(<SettingsPage live={liveWith({ state: 'downloading', percent: 42, received: 5_200_000,
    total: 12_345_678, version: '0.1.1', path: null, error: null })} onReconnect={() => {}} />)
  await waitFor(() => expect(screen.getByText('Downloading… 42% · 5.2 MB of 12.3 MB')).toBeInTheDocument())
  expect(screen.getByText('Downloading… 42%')).toBeDisabled()
})

it('offers the installer again once the download has finished', async () => {
  vi.spyOn(api, 'update').mockResolvedValue({ ...updateAvailable })
  render(<SettingsPage live={liveWith({ state: 'ready', percent: 100, received: 12_345_678,
    total: 12_345_678, version: '0.1.1', path: '/data/updates/Flackey.pkg', error: null })} onReconnect={() => {}} />)
  await waitFor(() => expect(screen.getByText(/The macOS installer is open/)).toBeInTheDocument())
  expect(screen.getByText('Open installer')).toBeEnabled()
})

it('says why a download failed and lets it be retried', async () => {
  const install = vi.spyOn(api, 'installUpdate').mockResolvedValue(
    { state: 'downloading', percent: 0, received: 0, total: null, version: '0.1.1', path: null, error: null })
  vi.spyOn(api, 'update').mockResolvedValue({ ...updateAvailable })
  render(<SettingsPage live={liveWith({ state: 'error', percent: 0, received: 0, total: null,
    version: '0.1.1', path: null, error: 'The download stopped before it finished.' })} onReconnect={() => {}} />)
  await waitFor(() => expect(screen.getByText('The download stopped before it finished.')).toBeInTheDocument())
  fireEvent.click(screen.getByText('Try again'))
  await waitFor(() => expect(install).toHaveBeenCalled())
})

it('surfaces a download that would not even start', async () => {
  vi.spyOn(api, 'installUpdate').mockRejectedValue(new ApiError(409, 'Flackey is already up to date.'))
  vi.spyOn(api, 'update').mockResolvedValue({ ...updateAvailable })
  render(<SettingsPage live={live} onReconnect={() => {}} />)
  fireEvent.click(await screen.findByText('Download update'))
  await waitFor(() => expect(screen.getByText('Flackey is already up to date.')).toBeInTheDocument())
})

it('says a newer version exists without offering a dead-end download when the installer is missing', async () => {
  vi.spyOn(api, 'update').mockResolvedValue({ ok: true, current: '0.1.2', newer: true, available: false,
    latest: '0.1.3', url: null, release_url: 'https://example.test/releases/v0.1.3', size: null,
    size_label: null, published_at: '2026-09-17T10:38:25Z', published_date: '2026-09-17', prerelease: false })
  render(<SettingsPage live={live} onReconnect={() => {}} />)
  await waitFor(() => expect(screen.getByText(/isn't published yet/)).toBeInTheDocument())
  expect(screen.queryByText('Download update')).not.toBeInTheDocument()
  expect(screen.getByText('View release')).toBeInTheDocument()
})

it('says when the app is up to date', async () => {
  render(<SettingsPage live={live} onReconnect={() => {}} />)
  await waitFor(() => expect(screen.getByText('Up to date.')).toBeInTheDocument())
})

it('defaults automatic update checks to on and lets the owner turn them off', async () => {
  const liveSettings = (live as unknown as { settings: AppSettings }).settings
  const result = { ...liveSettings, auto_update_check: false }
  vi.spyOn(api, 'saveSettings').mockResolvedValue(result)
  render(<SettingsPage live={live} onReconnect={() => {}} />)
  const row = screen.getByText('On — Flackey checks for updates when it starts.').closest('.srow') as HTMLElement
  fireEvent.click(within(row).getByText('Turn off'))
  await waitFor(() => expect(api.saveSettings).toHaveBeenCalledWith(liveSettings.library_root, { auto_update_check: false }))
  expect(mockSetSettings).toHaveBeenCalledWith(result)
})

it('shows automatic update checks as off when the setting is saved that way', async () => {
  const current = live as unknown as { settings: AppSettings; health: Health }
  const off = makeLive({ ...current, settings: { ...current.settings, auto_update_check: false } })
  render(<SettingsPage live={off} onReconnect={() => {}} />)
  expect(screen.getByText('Off — check for updates here instead.')).toBeInTheDocument()
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

it('lets an already signed-in account turn the Deezer bot source back on', async () => {
  const current = live as unknown as { settings: AppSettings; health: Health }
  const offline = makeLive({ ...current, health: { ...current.health, source_enabled: false } })
  vi.spyOn(api, 'telegramSource').mockResolvedValue({ source_enabled: true })
  render(<SettingsPage live={offline} onReconnect={() => {}} />)
  expect(screen.getByText('Off — requests use Soulseek only')).toBeInTheDocument()
  // Switched off on purpose is not a fault: grey, not the amber that means something needs attention.
  expect(dot(connRow('Deezer bot'))).toHaveClass('off')
  fireEvent.click(screen.getByText('Turn on'))
  await waitFor(() => expect(api.telegramSource).toHaveBeenCalledWith(true))
  expect(mockRefresh).toHaveBeenCalled()
})

describe('the Connections section', () => {
  const signedOut = (over: Partial<Health> = {}) => {
    const current = live as unknown as { settings: AppSettings; health: Health }
    vi.spyOn(api, 'telegramStatus').mockResolvedValue({ authorized: false, configured: true, phone_masked: null })
    return makeLive({ ...current, health: { ...current.health, telegram_authorized: false, ...over } })
  }

  it('gathers every account into one titled box, and leaves the library folder out of it', () => {
    render(<SettingsPage live={live} onReconnect={() => {}} />)
    const box = connections()
    expect(box).toHaveClass('group')
    for (const name of ['Telegram', 'Deezer bot', 'Soulseek']) {
      expect(box).toContainElement(within(box).getByText(name))
    }
    expect(box).not.toContainElement(screen.getByText('Library folder'))
    // Three connections, three status lines, one column: the dots are what the owner counts.
    expect(box.querySelectorAll('.status-dot')).toHaveLength(3)
  })

  it('nests the Deezer bot under Telegram and says what it is and what it needs', () => {
    render(<SettingsPage live={live} onReconnect={() => {}} />)
    const bot = connRow('Deezer bot')
    expect(bot).toHaveClass('sub')
    expect(connRow('Telegram').nextElementSibling).toBe(bot)
    expect(within(bot).getByText(/A bot Flackey messages inside Telegram/)).toBeInTheDocument()
    expect(within(bot).getByText(/needs the Telegram account above/)).toBeInTheDocument()
  })

  it('keeps the API keys with the Telegram connection, still folded shut', () => {
    render(<SettingsPage live={live} onReconnect={() => {}} />)
    const details = screen.getByText('Use your own Telegram API keys').closest('details')!
    expect(details).not.toHaveAttribute('open')
    expect(connections()).toContainElement(details)
    // After the two Telegram rows it belongs to, and before the unrelated Soulseek one.
    expect(connRow('Deezer bot').nextElementSibling).toBe(details.closest('.srow'))
    expect(details.closest('.srow')!.nextElementSibling).toBe(connRow('Soulseek'))
  })

  it('does not let the Deezer bot look fine while Telegram is signed out', async () => {
    // The dependent state, told honestly: the bot is still switched on, but it is reached through an
    // account that is gone, so the row is amber and points at Telegram rather than at itself.
    render(<SettingsPage live={signedOut()} onReconnect={() => {}} />)
    await waitFor(() => expect(within(connRow('Telegram')).getByText('Signed out')).toBeInTheDocument())
    const bot = connRow('Deezer bot')
    expect(within(bot).getByText(/On, but Telegram is signed out — sign in above/)).toBeInTheDocument()
    expect(dot(bot)).toHaveClass('amber')
    expect(dot(bot).className).not.toBe('status-dot')
  })

  it('says a switched-off bot is signed out too, and offers the way back', () => {
    const onReconnect = vi.fn()
    render(<SettingsPage live={signedOut({ source_enabled: false })} onReconnect={onReconnect} />)
    const bot = connRow('Deezer bot')
    expect(within(bot).getByText('Off — and Telegram is signed out')).toBeInTheDocument()
    expect(dot(bot)).toHaveClass('off')
    fireEvent.click(within(bot).getByRole('button', { name: 'Reconnect Telegram' }))
    expect(onReconnect).toHaveBeenCalled()
  })

  it('files the format question with the folder, where it belongs', () => {
    // The format applies to every lossless download, whatever fetched it. Under a Soulseek heading it
    // would claim to be a Soulseek setting -- the same implication-by-position this section removes.
    render(<SettingsPage live={live} onReconnect={() => {}} />)
    const library = section('Library')
    expect(library).toContainElement(screen.getByText('Library folder'))
    expect(library).toContainElement(screen.getByText('File format'))
  })

  it('draws no Soulseek box when there is nothing Soulseek-specific to put in it', () => {
    // Nothing in that box is unconditional now the format has moved out, so a copy with no account and
    // no port table must not be given a titled empty card.
    render(<SettingsPage live={live} onReconnect={() => {}} />)
    expect(screen.queryByRole('heading', { name: 'Soulseek' })).not.toBeInTheDocument()
    expect(connRow('Soulseek')).toBeInTheDocument()
  })
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

  it('keeps its own box to rows that really are Soulseek', () => {
    // The two logins, the sharing state and the diagnostics -- but not the filing format, which governs
    // every lossless download and lives with the library folder those downloads land in.
    show(lossless({ provider: { name: 'soulseek', status: 'ok', username: 'digger' } }))
    const box = section('Soulseek')
    for (const name of ['Soulseek account password', 'Helper web login', 'Sharing', 'Technical details']) {
      expect(box).toContainElement(screen.getByText(name))
    }
    expect(box).not.toContainElement(screen.getByText('File format'))
    expect(section('Library')).toContainElement(screen.getByText('File format'))
  })

  it('lets the owner choose the future lossless filing format', async () => {
    const save = vi.spyOn(api, 'saveSettings').mockResolvedValue(settings({ lossless_filing_format: 'wav' }))
    show(lossless())
    expect(screen.getByText(/New lossless tracks will be filed as AIFF/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /WAV/ }))
    await waitFor(() => expect(save).toHaveBeenCalledWith('/tmp/lib', { lossless_filing_format: 'wav' }))
    expect(mockSetSettings).toHaveBeenCalledWith(expect.objectContaining({ lossless_filing_format: 'wav' }))
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

  it('distinguishes the helper web login from the Soulseek account password', async () => {
    const credentials = vi.spyOn(api, 'slskdCredentials')
      .mockResolvedValue({ username: 'flackey', password: 'helper-secret' })
    show(lossless())
    expect(credentials).not.toHaveBeenCalled()
    expect(screen.getByText('Soulseek account password')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Show login' }))
    await waitFor(() => expect(screen.getByText('flackey · helper-secret')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'Hide' }))
    expect(screen.queryByText('helper-secret')).not.toBeInTheDocument()
  })

  it('has nothing to show when no Soulseek account was ever saved', () => {
    show(lossless({ enabled: false }), settings({ soulseek_enabled: false }))
    expect(screen.queryByRole('button', { name: 'Show' })).not.toBeInTheDocument()
  })

  it('hides the ranking diagnostics when no account is set up, but still allows choosing a future format', () => {
    show(lossless({ enabled: false }), settings({ soulseek_enabled: false }))
    expect(screen.getByText(/Not set up — run setup again/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /WAV/ })).toBeInTheDocument()
    expect(screen.queryByText(/keeps a copy only if its fingerprint matches/)).not.toBeInTheDocument()
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

  it('shows the server\'s own reason when the check is refused', async () => {
    vi.spyOn(api, 'checkSharing')
      .mockRejectedValue(new ApiError(409, "Soulseek isn't set up yet, so there is no port to check."))
    show(lossless(), settings(), sharing({ reachable: false }))
    fireEvent.click(screen.getByRole('button', { name: 'Check again' }))
    await waitFor(() => expect(screen.getByText("Soulseek isn't set up yet, so there is no port to check."))
      .toBeInTheDocument())
  })

  it('falls back to its own sentence when the failure carries no server message', async () => {
    vi.spyOn(api, 'checkSharing').mockRejectedValue(new Error('network error'))
    show(lossless(), settings(), sharing({ reachable: false }))
    fireEvent.click(screen.getByRole('button', { name: 'Check again' }))
    await waitFor(() => expect(screen.getByText('Could not start the check. Try again.')).toBeInTheDocument())
  })

  it('says nothing when the check starts fine', async () => {
    const check = vi.spyOn(api, 'checkSharing').mockResolvedValue(sharing({ checking: true }))
    show(lossless(), settings(), sharing({ reachable: false }))
    fireEvent.click(screen.getByRole('button', { name: 'Check again' }))
    await waitFor(() => expect(check).toHaveBeenCalled())
    expect(screen.queryByText('Could not start the check. Try again.')).not.toBeInTheDocument()
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
