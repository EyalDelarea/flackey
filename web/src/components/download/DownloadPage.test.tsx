import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import DownloadPage from './DownloadPage'
import { ApiError, api } from '../../api'
import type { Bundle, Health, Request } from '../../api'
import type { Live } from '../../live'

vi.mock('../../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api')>()
  return { ...actual, api: { ...actual.api, submit: vi.fn(), retry: vi.fn(), removeRequest: vi.fn(), clearFailed: vi.fn() } }
})

function makeLive(bundles: Bundle[], overrides: Partial<Live> = {}): Live {
  const health: Health = { ok: true, version: '0', telegram_authorized: true, worker_running: true, setup_done: true }
  return {
    health, bundles: new Map(bundles.map(b => [b.request.id, b])), playlists: [], stats: null, settings: null,
    fetchProgress: [], libraryVersion: 0, loadError: null, loading: false, connected: true, lastSeen: null,
    upgradeActivity: null, setUpgradeActivity: () => undefined,
    refresh: async () => undefined, refreshLibrary: async () => undefined, retry: async () => undefined,
    setHealth: () => undefined, setSettings: () => undefined, dropBundle: () => undefined,
    ...overrides,
  }
}

const baseRequest: Request = {
  id: 7, created_at: '', updated_at: '', raw_text: 'x', kind: 'track', state: 'error',
  playlist_id: null, playlist_position: null, source_url: null, query_artist: 'Artist', query_title: 'Title',
  query_version: null, query_duration_s: null, chosen_candidate_id: null, catalog_track_id: null, fetch_source: null,
  confidence: null, flag_reason: null, error_message: 'network blip', attempts: 1, retry_after: null, track_id: null,
}
const bundle: Bundle = { request: baseRequest, candidates: [], catalog: null, track: null, rejection: null }
const mk = (id: number, state: Request['state']): Bundle => ({
  request: { ...baseRequest, id, state, query_title: `Track ${id}`, error_message: state === 'error' ? 'network blip' : null },
  candidates: [], catalog: null, track: null, rejection: null,
})

it('shows a red banner with a dismiss action when a row action fails', async () => {
  vi.mocked(api.retry).mockRejectedValueOnce(new ApiError(409, 'That track is already being fetched.'))
  render(<DownloadPage live={makeLive([bundle])} />)
  fireEvent.click(screen.getByRole('button', { name: 'History' }))
  fireEvent.click(screen.getByText('Try again'))
  await waitFor(() => expect(screen.getByText('That track is already being fetched.')).toBeInTheDocument())
  fireEvent.click(screen.getByText('Dismiss'))
  expect(screen.queryByText('That track is already being fetched.')).not.toBeInTheDocument()
})

it('refreshes queue and playlists after a paste submission returns', async () => {
  vi.mocked(api.submit).mockResolvedValueOnce({
    summary: 'Queued 3 of 3 from "Spotify Set" (0 already in library)',
    request_ids: [1, 2, 3],
    playlist_id: 9,
    name: 'Spotify Set',
    total: 3,
    already_in_library: 0,
    already_queued: 0,
  })
  const refresh = vi.fn(async () => undefined)
  render(<DownloadPage live={makeLive([], { refresh })} />)
  fireEvent.change(screen.getByLabelText('Track or playlist link'), {
    target: { value: 'https://open.spotify.com/playlist/pl1' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Add' }))
  await waitFor(() => expect(api.submit).toHaveBeenCalledWith('https://open.spotify.com/playlist/pl1'))
  await waitFor(() => expect(refresh).toHaveBeenCalled())
  expect(await screen.findByText('Queued 3 of 3 from "Spotify Set" (0 already in library)')).toBeInTheDocument()
})

describe('filter bar, remove and clear failed', () => {
  const mixed = [mk(1, 'queued'), mk(2, 'awaiting_review'), mk(3, 'done'), mk(4, 'error')]

  it('clicking Failed leaves only failed rows and hides the rest', async () => {
    render(<DownloadPage live={makeLive(mixed)} />)
    expect(screen.getByText('Artist – Track 1')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'History' }))
    fireEvent.click(screen.getByRole('button', { name: /^Failed/ }))
    await waitFor(() => expect(screen.queryByText('Artist – Track 1')).not.toBeInTheDocument())
    expect(screen.getByText('Artist – Track 4')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Failed/ })).toHaveAttribute('aria-pressed', 'true')
  })

  it('refreshes Downloads after retrying a failed request', async () => {
    vi.mocked(api.retry).mockResolvedValueOnce({ ...baseRequest, state: 'queued' } as any)
    const refresh = vi.fn(async () => undefined)
    render(<DownloadPage live={makeLive([mk(4, 'error')], { refresh })} />)
    fireEvent.click(screen.getByRole('button', { name: 'History' }))
    fireEvent.click(screen.getByText('Try again'))
    await waitFor(() => expect(refresh).toHaveBeenCalled())
  })

  it('Remove on a failed row calls api.removeRequest, then drops it from live.bundles once the call succeeds', async () => {
    vi.mocked(api.removeRequest).mockResolvedValueOnce({ ok: true })
    const dropBundle = vi.fn()
    render(<DownloadPage live={makeLive([mk(4, 'error')], { dropBundle })} />)
    fireEvent.click(screen.getByRole('button', { name: 'History' }))
    fireEvent.click(screen.getByText('Remove'))
    expect(api.removeRequest).toHaveBeenCalledWith(4)
    await waitFor(() => expect(dropBundle).toHaveBeenCalledWith(4))
  })

  it('Remove leaves the row in place when the delete call fails', async () => {
    vi.mocked(api.removeRequest).mockRejectedValueOnce(new ApiError(409, 'That track is still being worked on. Skip it first.'))
    const dropBundle = vi.fn()
    render(<DownloadPage live={makeLive([mk(4, 'error')], { dropBundle })} />)
    fireEvent.click(screen.getByRole('button', { name: 'History' }))
    fireEvent.click(screen.getByText('Remove'))
    await waitFor(() => expect(screen.getByText('That track is still being worked on. Skip it first.')).toBeInTheDocument())
    expect(dropBundle).not.toHaveBeenCalled()
  })

  it('Clear failed calls api.clearFailed', async () => {
    vi.mocked(api.clearFailed).mockResolvedValueOnce({ removed: [4] })
    render(<DownloadPage live={makeLive(mixed)} />)
    fireEvent.click(screen.getByRole('button', { name: 'History' }))
    const clearBtn = screen.getByRole('button', { name: 'Clear failed' })
    expect(clearBtn).not.toBeDisabled()
    fireEvent.click(clearBtn)
    await waitFor(() => expect(api.clearFailed).toHaveBeenCalled())
  })

  it('Clear failed is disabled when there are no failed rows', () => {
    render(<DownloadPage live={makeLive([mk(1, 'done')])} />)
    fireEvent.click(screen.getByRole('button', { name: 'History' }))
    expect(screen.getByRole('button', { name: 'Clear failed' })).toBeDisabled()
  })

  it('badges History when a request finishes while the owner is watching Downloads, and clears on open', () => {
    const { rerender } = render(<DownloadPage live={makeLive([mk(1, 'fetching')])} />)
    expect(screen.getByRole('button', { name: 'History' }).querySelector('.count')).not.toBeInTheDocument()

    rerender(<DownloadPage live={makeLive([mk(1, 'done')])} />)
    expect(screen.getByRole('button', { name: 'History' }).querySelector('.count')).toHaveTextContent('1')

    fireEvent.click(screen.getByRole('button', { name: 'History' }))
    expect(screen.getByRole('button', { name: 'History' }).querySelector('.count')).not.toBeInTheDocument()
  })

  it('shows "Nothing here." when a filter hides every row', () => {
    render(<DownloadPage live={makeLive([mk(1, 'queued')])} />)
    fireEvent.click(screen.getByRole('button', { name: 'History' }))
    expect(screen.getByText('No completed downloads yet.')).toBeInTheDocument()
  })

  it('shows the paste prompt only when there are no requests at all', () => {
    render(<DownloadPage live={makeLive([])} />)
    expect(screen.getByText('Paste a link above to start digging.')).toBeInTheDocument()
  })

  it('hides the filter bar entirely when there are no requests at all', () => {
    render(<DownloadPage live={makeLive([])} />)
    expect(screen.queryByRole('button', { name: /^All/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Clear failed' })).not.toBeInTheDocument()
  })
})
