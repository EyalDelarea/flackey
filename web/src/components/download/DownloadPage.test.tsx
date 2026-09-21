import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import DownloadPage from './DownloadPage'
import { ApiError, api } from '../../api'
import type { Bundle, Candidate, Health, Request } from '../../api'
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
    await waitFor(() => expect(api.retryFailed).toHaveBeenCalledWith([1, 2]))
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

// ---- Samples (issue #53) ----------------------------------------------------------------------
// The page holds one <audio> for every row on screen, because two requests can sit in `awaiting_review`
// at once and a sample starting in one has to stop the one already running in the other.
const cand = (id: number, title: string): Candidate => ({ id, request_id: 1, source: 'deezer', source_ref: `${id}`,
  artist: 'Ace Ventura', title, mix_name: null, duration_s: 420, deezer_id: id, isrc: null, rank: 1, score: 90, catalog_track_id: null })
const choiceRow = (id: number, titles: string[]): Bundle => ({
  request: { ...baseRequest, id, state: 'awaiting_review', query_title: `Track ${id}`, error_message: null },
  candidates: titles.map((t, i) => cand(id * 10 + i, t)), catalog: null, track: null, rejection: null })
const withStubbedPlayer = (body: (play: ReturnType<typeof vi.fn>, pause: ReturnType<typeof vi.fn>) => void) => {
  // jsdom implements neither, and the real `play()` returns a promise the page attaches a catch to.
  const play = vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(() => Promise.resolve())
  const pause = vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => undefined)
  try { body(play as never, pause as never) } finally { play.mockRestore(); pause.mockRestore() }
}

it('plays a sample through the page\'s one player, and starting another stops the first', () => {
  withStubbedPlayer((play, pause) => {
    const { container } = render(<DownloadPage live={makeLive([choiceRow(1, ['Original Mix']), choiceRow(2, ['Extended Mix'])])} />)
    const players = container.querySelectorAll('audio')
    expect(players).toHaveLength(1)
    const el = players[0] as HTMLAudioElement
    expect(el.getAttribute('src')).toBeNull()   // nothing is resolved until a play is pressed
    fireEvent.click(screen.getByRole('button', { name: 'Play a sample of Ace Ventura – Original Mix' }))
    expect(el.src).toContain('/api/candidates/10/preview')
    expect(play).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: 'Stop a sample of Ace Ventura – Original Mix' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Play a sample of Ace Ventura – Extended Mix' }))
    expect(pause).toHaveBeenCalled()
    expect(el.src).toContain('/api/candidates/20/preview')
    expect(screen.getByRole('button', { name: 'Play a sample of Ace Ventura – Original Mix' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Stop a sample of Ace Ventura – Extended Mix' })).toBeInTheDocument()
  })
})

it('stops on a second press, and goes back to play when the clip runs out on its own', () => {
  withStubbedPlayer((_play, pause) => {
    const { container } = render(<DownloadPage live={makeLive([choiceRow(1, ['Original Mix'])])} />)
    const el = container.querySelector('audio') as HTMLAudioElement
    fireEvent.click(screen.getByRole('button', { name: 'Play a sample of Ace Ventura – Original Mix' }))
    fireEvent.click(screen.getByRole('button', { name: 'Stop a sample of Ace Ventura – Original Mix' }))
    expect(pause).toHaveBeenCalled()
    expect(el.currentTime).toBe(0)   // a stop rewinds: the next press starts the sample over
    expect(screen.getByRole('button', { name: 'Play a sample of Ace Ventura – Original Mix' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Play a sample of Ace Ventura – Original Mix' }))
    fireEvent.ended(el)   // 30 seconds later, with nobody pressing anything
    expect(screen.getByRole('button', { name: 'Play a sample of Ace Ventura – Original Mix' })).toBeInTheDocument()
  })
})

it('takes the sample button away from the candidate with no preview, and leaves the others alone', () => {
  // A 404 from the route -- no `deezer_id`, or Deezer has no sample for that track -- reaches the page
  // as a load error on the element, and only the candidate that was playing loses its button.
  withStubbedPlayer(() => {
    const { container } = render(<DownloadPage live={makeLive([choiceRow(1, ['Original Mix', 'Extended Mix'])])} />)
    const el = container.querySelector('audio') as HTMLAudioElement
    fireEvent.click(screen.getByRole('button', { name: 'Play a sample of Ace Ventura – Original Mix' }))
    fireEvent.error(el)
    expect(screen.getByRole('button', { name: 'No sample for Ace Ventura – Original Mix' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Play a sample of Ace Ventura – Extended Mix' })).toBeEnabled()
  })
})

it('stops the sample when the page goes away', () => {
  // `App` swaps this page out for Library or Settings; a detached <audio> would otherwise keep playing
  // with no Stop button left anywhere in the app.
  withStubbedPlayer((_play, pause) => {
    const { unmount } = render(<DownloadPage live={makeLive([choiceRow(1, ['Original Mix'])])} />)
    fireEvent.click(screen.getByRole('button', { name: 'Play a sample of Ace Ventura – Original Mix' }))
    pause.mockClear()
    unmount()
    expect(pause).toHaveBeenCalled()
  })
})
