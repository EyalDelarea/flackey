import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import DownloadPage from './DownloadPage'
import { ApiError, api } from '../../api'
import type { Bundle, Health, Request } from '../../api'
import type { Live } from '../../live'

vi.mock('../../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api')>()
  return { ...actual, api: { ...actual.api, submit: vi.fn(), retry: vi.fn(), removeRequest: vi.fn(), clearFailed: vi.fn(), retryFailed: vi.fn() } }
})

function makeLive(bundles: Bundle[], overrides: Partial<Live> = {}): Live {
  const health: Health = { ok: true, version: '0', telegram_authorized: true, worker_running: true, setup_done: true }
  return {
    health, bundles: new Map(bundles.map(b => [b.request.id, b])), playlists: [], stats: null, settings: null,
    fetchProgress: [], libraryVersion: 0, loadError: null, loading: false, connected: true, lastSeen: null,
    upgradeActivity: null, setUpgradeActivity: () => undefined, update: null, updateDownload: null,
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
  failed_stage: null,
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

  it('does not badge History for completions that were already there on the first load', () => {
    render(<DownloadPage live={makeLive([mk(1, 'done'), mk(2, 'not_found')])} />)
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

describe('Retry all on the Failed tab', () => {
  const openFailed = () => fireEvent.click(screen.getByRole('button', { name: /^Failed/ }))

  it('counts only the failed rows that can actually be re-queued', () => {
    render(<DownloadPage live={makeLive([mk(1, 'error'), mk(2, 'not_found'), mk(3, 'rejected'), mk(4, 'cancelled')])} />)
    openFailed()
    expect(screen.getByRole('button', { name: 'Retry all 2' })).toBeInTheDocument()
  })

  it('re-queues every retryable row in the view in one call, then refreshes', async () => {
    vi.mocked(api.retryFailed).mockResolvedValueOnce({ retried: [1, 2], skipped: [] })
    const refresh = vi.fn(async () => undefined)
    render(<DownloadPage live={makeLive([mk(1, 'error'), mk(2, 'not_found'), mk(3, 'rejected')], { refresh })} />)
    openFailed()
    fireEvent.click(screen.getByRole('button', { name: 'Retry all 2' }))
    // Newest first, because the Failed tab lands on the All chip now rather than pinning a bucket into a
    // bar of stage chips -- so it sorts the way every other tab on All does. The set is what matters.
    await waitFor(() => expect(api.retryFailed).toHaveBeenCalled())
    expect([...vi.mocked(api.retryFailed).mock.calls[0][0]].sort()).toEqual([1, 2])
    await waitFor(() => expect(refresh).toHaveBeenCalled())
  })

  it('sits there disabled when the failures on screen are all unretryable', () => {
    render(<DownloadPage live={makeLive([mk(1, 'rejected'), mk(2, 'cancelled')])} />)
    openFailed()
    expect(screen.getByRole('button', { name: 'Retry all 0' })).toBeDisabled()
  })

  it('is not offered when there is nothing failed at all', () => {
    render(<DownloadPage live={makeLive([mk(1, 'done')])} />)
    openFailed()
    expect(screen.queryByRole('button', { name: /^Retry all/ })).not.toBeInTheDocument()
  })

  it('is not offered on the other tabs', () => {
    render(<DownloadPage live={makeLive([mk(1, 'error')])} />)
    expect(screen.queryByRole('button', { name: /^Retry all/ })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'History' }))
    expect(screen.queryByRole('button', { name: /^Retry all/ })).not.toBeInTheDocument()
  })

  it('disables itself while the batch is in flight', async () => {
    let finish = (_: { retried: number[]; skipped: number[] }) => undefined as void
    vi.mocked(api.retryFailed).mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
    render(<DownloadPage live={makeLive([mk(1, 'error')])} />)
    openFailed()
    fireEvent.click(screen.getByRole('button', { name: 'Retry all 1' }))
    expect(await screen.findByRole('button', { name: 'Retrying…' })).toBeDisabled()
    finish({ retried: [1], skipped: [] })
    await waitFor(() => expect(screen.getByRole('button', { name: 'Retry all 1' })).not.toBeDisabled())
  })

  it('shows the error when the batch call fails', async () => {
    vi.mocked(api.retryFailed).mockRejectedValueOnce(new ApiError(503, 'Soulseek is not connected.'))
    render(<DownloadPage live={makeLive([mk(1, 'error')])} />)
    openFailed()
    fireEvent.click(screen.getByRole('button', { name: 'Retry all 1' }))
    expect(await screen.findByText('Soulseek is not connected.')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Retry all 1' })).not.toBeDisabled())
  })

  it('explains the gap between the Failed badge and its own count, rather than leaving two numbers', () => {
    render(<DownloadPage live={makeLive([mk(1, 'error'), mk(2, 'rejected'), mk(3, 'cancelled')])} />)
    expect(screen.getByRole('button', { name: /^Failed/ })).toHaveTextContent('Failed 3')
    openFailed()
    expect(screen.getByRole('button', { name: 'Retry all 1' })).toBeInTheDocument()
    expect(screen.getByText(/2 of these 3 cannot be tried again — 1 failed the quality check, 1 you stopped/))
      .toBeInTheDocument()
  })

  it('leaves the sentence out when the two counts already agree', () => {
    render(<DownloadPage live={makeLive([mk(1, 'error'), mk(2, 'not_found')])} />)
    openFailed()
    expect(screen.getByRole('button', { name: 'Retry all 2' })).toBeInTheDocument()
    expect(screen.queryByText(/cannot be tried again/)).not.toBeInTheDocument()
  })

  it('tells every failed row why it is here and whether it can come back', () => {
    render(<DownloadPage live={makeLive([mk(1, 'error'), mk(2, 'not_found'), mk(3, 'rejected'), mk(4, 'cancelled')])} />)
    openFailed()
    // The bar: no row sits in a list called "Failed" saying nothing about what happens to it next.
    expect(screen.getAllByText(/Try again starts the search over|searches again from scratch/)).toHaveLength(2)
    expect(screen.getAllByText(/paste the link again/i)).toHaveLength(2)
  })

  it('says so when the call succeeds but nothing was re-queued', async () => {
    vi.mocked(api.retryFailed).mockResolvedValueOnce({ retried: [], skipped: [1] })
    render(<DownloadPage live={makeLive([mk(1, 'error')])} />)
    openFailed()
    fireEvent.click(screen.getByRole('button', { name: 'Retry all 1' }))
    expect(await screen.findByText('Nothing could be retried — this list may be out of date.')).toBeInTheDocument()
  })
})

/* Issue #60: the list split by pipeline stage. "In progress 49" was one undifferentiated lump of which 41
   rows were parked on a Soulseek backoff doing nothing, so the chips, the batch bar and the Failed tab all
   read from one stage axis now. */
describe('stage chips and the batch bar', () => {
  const openFailed = () => fireEvent.click(screen.getByRole('button', { name: /^Failed/ }))
  const playlists = [{ id: 7, source_url: 'u', name: 'Goa Trance Classics', created_at: '', updated_at: '',
    track_ids: [], file: '/lib/Playlists/Goa.m3u8' }]
  const inPlaylist = (id: number, state: Request['state'], over: Partial<Request> = {}): Bundle => ({
    request: { ...baseRequest, id, state, playlist_id: 7, query_title: `Track ${id}`,
      error_message: state === 'error' ? 'network blip' : null, ...over },
    candidates: [], catalog: null, track: null, rejection: null,
  })
  const parked = (id: number) => inPlaylist(id, 'queued', { retry_after: '2099-01-01T00:00:00+00:00', attempts: 2 })

  it('splits the one In progress chip into the stages it was hiding, Waiting last', () => {
    render(<DownloadPage live={makeLive([
      inPlaylist(1, 'identifying'), inPlaylist(2, 'awaiting_review'), inPlaylist(3, 'fetching'),
      inPlaylist(4, 'verifying'), parked(5), parked(6),
    ], { playlists })} />)
    const chips = [...document.querySelector('.filterbar')!.querySelectorAll('.chip')].map(b => b.textContent)
    expect(chips).toEqual(['All 6', 'Searching 1', 'Needs you 1', 'Downloading 1', 'Verifying 1', 'Waiting 2'])
    // Waiting is not a rung of the pipeline, so it reads behind a rule rather than in line with them.
    expect(document.querySelector('.filterbar')!.querySelector('.chip-divider')!.nextElementSibling!.textContent)
      .toBe('Waiting 2')
  })

  it('narrows the list on the stage axis, so the parked rows can be looked at on their own', () => {
    render(<DownloadPage live={makeLive([inPlaylist(1, 'identifying'), parked(5), parked(6)], { playlists })} />)
    fireEvent.click(screen.getByRole('button', { name: 'Waiting 2' }))
    expect(screen.getByRole('button', { name: 'Waiting 2' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.queryByText('Artist – Track 1')).not.toBeInTheDocument()
    expect(screen.getByText('Artist – Track 5')).toBeInTheDocument()
  })

  it('lands the Failed tab on a chip that reads as pressed', () => {
    // `setFilter('failed')` pinned a Bucket into a bar of Stage chips: nothing read as pressed while the
    // rows happened to be right, because every row on that tab is in the failed bucket anyway.
    render(<DownloadPage live={makeLive([inPlaylist(1, 'error', { failed_stage: 'download' })], { playlists })} />)
    openFailed()
    expect(screen.getByRole('button', { name: 'All 1' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('splits the Failed tab by where the track stopped, hiding Unknown until something lands there', () => {
    render(<DownloadPage live={makeLive([
      inPlaylist(1, 'not_found'), inPlaylist(2, 'error', { failed_stage: 'download' }),
      inPlaylist(3, 'rejected'), inPlaylist(4, 'cancelled'),
    ], { playlists })} />)
    openFailed()
    expect(screen.getByRole('button', { name: 'Search 1' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Download 1' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Verify 1' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Stopped 1' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Unknown/ })).not.toBeInTheDocument()
  })

  it('offers Unknown once a run stopped somewhere it could not name', () => {
    render(<DownloadPage live={makeLive([inPlaylist(1, 'error', { failed_stage: null })], { playlists })} />)
    openFailed()
    expect(screen.getByRole('button', { name: 'Unknown 1' })).toBeInTheDocument()
  })

  it('keeps the sentence and Retry all counting the same rows once a chip narrows the tab', () => {
    // `failedSummary` read the whole tab while `Retry all` read the filtered rows. They agreed only
    // because the filter never moved off `failed` here -- and the comment above them said they could not
    // disagree. Pick Verify and the two must still be about the same five rows.
    render(<DownloadPage live={makeLive([
      inPlaylist(1, 'error', { failed_stage: 'download' }), inPlaylist(2, 'error', { failed_stage: 'download' }),
      inPlaylist(3, 'error', { failed_stage: 'download' }), inPlaylist(4, 'rejected'), inPlaylist(5, 'rejected'),
    ], { playlists })} />)
    openFailed()
    expect(screen.getByText(/2 of these 5 cannot be tried again/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Verify 2' }))
    expect(screen.getByRole('button', { name: 'Retry all 0' })).toBeDisabled()
    expect(screen.getByText(/^Nothing here can be tried again — 2 failed the quality check/)).toBeInTheDocument()
  })

  it('draws one filter bar on the Failed tab, not two stacked on each other', () => {
    render(<DownloadPage live={makeLive([inPlaylist(1, 'rejected')], { playlists })} />)
    openFailed()
    expect(document.querySelectorAll('.filterbar')).toHaveLength(1)
    expect(document.querySelector('.filterbar')!.className).toContain('wide')
  })

  it('counts the whole batch in the bar, not the slice the tab is showing', () => {
    // The Downloads tab drops every finished row, so a bar built from what it shows reads "0 filed" on a
    // playlist that is in fact half filed. The bar is about the batch; the chips are about the tab.
    render(<DownloadPage live={makeLive([
      inPlaylist(1, 'done'), inPlaylist(2, 'done'), inPlaylist(3, 'rejected'),
      inPlaylist(4, 'fetching'), parked(5),
    ], { playlists })} />)
    expect(screen.getByText('5 tracks')).toBeInTheDocument()
    const legend = document.querySelector('.group-legend')!.textContent
    expect(legend).toBe('2 filed1 working1 waiting to retry1 failed')
    // Trap 3: the header used to say "1 in progress" an inch above a bar drawn to correct exactly that.
    expect(screen.queryByText(/in progress/)).not.toBeInTheDocument()
  })

  it('splits the Failed tab bar by where the tracks stopped', () => {
    render(<DownloadPage live={makeLive([
      inPlaylist(1, 'done'), inPlaylist(2, 'not_found'),
      inPlaylist(3, 'error', { failed_stage: 'download' }), inPlaylist(4, 'rejected'), inPlaylist(5, 'cancelled'),
    ], { playlists })} />)
    openFailed()
    expect(screen.getByText('4 of 5 stopped')).toBeInTheDocument()
    expect(document.querySelector('.group-legend')!.textContent)
      .toBe('1 found nothing to download1 could not finish the download1 failed the quality check1 you stopped')
  })
})
