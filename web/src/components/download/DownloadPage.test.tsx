import { act, render, screen, fireEvent, waitFor } from '@testing-library/react'
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
    // One rejected row is final; the stopped one has its own Try again and is not swept, so the button
    // counts one and the sentence has to account for the other two separately (issue #92).
    expect(screen.getByRole('button', { name: 'Retry all 1' })).toBeInTheDocument()
    expect(screen.getByText(/1 of these 3 cannot be tried again — 1 failed the quality check/))
      .toBeInTheDocument()
    expect(screen.getByText(/One of these you stopped yourself, so Retry all leaves it out/))
      .toBeInTheDocument()
  })

  it('does not offer to sweep the tracks the owner stopped', () => {
    render(<DownloadPage live={makeLive([mk(1, 'cancelled'), mk(2, 'cancelled')])} />)
    openFailed()
    // Every row has a Try again; the sweep has nothing to take, and the line under it says why rather
    // than leaving a disabled button beside two live ones.
    expect(screen.getAllByRole('button', { name: 'Try again' })).toHaveLength(2)
    expect(screen.getByRole('button', { name: 'Retry all 0' })).toBeDisabled()
    expect(screen.getByText(/2 of these you stopped yourself, so Retry all leaves them out/))
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
    expect(screen.getAllByText(/paste the link again/i)).toHaveLength(1)
    expect(screen.getAllByText(/Retry all leaves stopped tracks alone/)).toHaveLength(1)
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
// Candidates in one row share a title and differ by version -- that is what a Choose window is for, and
// it is what the buttons have to be able to say apart.
const cand = (id: number, mix: string, hasPreview?: boolean | null, deezerId: number | null = id): Candidate => ({ id, request_id: 1, source: 'deezer', source_ref: `${id}`,
  artist: 'Ace Ventura', title: 'The Tribe', mix_name: mix, duration_s: 420, deezer_id: deezerId, isrc: null, rank: 1, score: 90, catalog_track_id: null,
  has_preview: hasPreview })
const choiceRow = (id: number, mixes: string[]): Bundle => ({
  request: { ...baseRequest, id, state: 'awaiting_review', query_title: `Track ${id}`, error_message: null },
  candidates: mixes.map((m, i) => cand(id * 10 + i, m)), catalog: null, track: null, rejection: null })
const sample = (verb: string, mix: string) => `${verb} a sample of Ace Ventura – The Tribe (${mix})`
const withStubbedPlayer = (body: (play: ReturnType<typeof vi.fn>, pause: ReturnType<typeof vi.fn>) => void) => {
  // jsdom implements none of the three, and the real `play()` returns a promise the page attaches a catch
  // to. `load()` is how the page lets go of the bytes once a clip is over.
  const play = vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(() => Promise.resolve())
  const pause = vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => undefined)
  const load = vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => undefined)
  try { body(play as never, pause as never) } finally { play.mockRestore(); pause.mockRestore(); load.mockRestore() }
}
// The element reports nothing on its own in jsdom, so a position is put on it and the event it would have
// fired is dispatched -- never a sleep, for a state that is entirely time-driven.
const tick = (el: HTMLAudioElement, at: number, of = 30) => {
  Object.defineProperty(el, 'currentTime', { configurable: true, get: () => at, set: () => undefined })
  Object.defineProperty(el, 'duration', { configurable: true, get: () => of })
  fireEvent.timeUpdate(el)
}
// Named by the version, because the candidates of a Choose row share a title and differ only there.
const lit = (container: HTMLElement) => [...container.querySelectorAll('.candidate.lit')]
  .map(c => c.querySelector('.head .version')!.textContent)
const railWidth = (card: Element) => (card.querySelector('.sample-rail-fill') as HTMLElement).style.transform

it('plays a sample through the page\'s one player, and starting another stops the first', () => {
  withStubbedPlayer((play, pause) => {
    const { container } = render(<DownloadPage live={makeLive([choiceRow(1, ['Original Mix']), choiceRow(2, ['Extended Mix'])])} />)
    const players = container.querySelectorAll('audio')
    expect(players).toHaveLength(1)
    const el = players[0] as HTMLAudioElement
    expect(el.getAttribute('src')).toBeNull()   // nothing is resolved until a play is pressed
    fireEvent.click(screen.getByRole('button', { name: sample('Play', 'Original Mix') }))
    expect(el.src).toContain('/api/candidates/10/preview')
    expect(play).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: sample('Stop', 'Original Mix') })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: sample('Play', 'Extended Mix') }))
    expect(pause).toHaveBeenCalled()
    expect(el.src).toContain('/api/candidates/20/preview')
    expect(screen.getByRole('button', { name: sample('Play', 'Original Mix') })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: sample('Stop', 'Extended Mix') })).toBeInTheDocument()
  })
})

it('stops on a second press, and goes back to play when the clip runs out on its own', () => {
  withStubbedPlayer((_play, pause) => {
    const { container } = render(<DownloadPage live={makeLive([choiceRow(1, ['Original Mix'])])} />)
    const el = container.querySelector('audio') as HTMLAudioElement
    // jsdom's `currentTime` is 0 whether or not anything assigns it -- nothing here ever plays -- so the
    // rewind has to be watched at the setter, not read back off the element.
    const seeks: number[] = []
    Object.defineProperty(el, 'currentTime', { configurable: true, get: () => 0, set: (v: number) => { seeks.push(v) } })
    fireEvent.click(screen.getByRole('button', { name: sample('Play', 'Original Mix') }))
    fireEvent.click(screen.getByRole('button', { name: sample('Stop', 'Original Mix') }))
    expect(pause).toHaveBeenCalled()
    expect(seeks).toEqual([0])   // a stop rewinds: the next press starts the sample over
    expect(screen.getByRole('button', { name: sample('Play', 'Original Mix') })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: sample('Play', 'Original Mix') }))
    fireEvent.ended(el)   // 30 seconds later, with nobody pressing anything
    expect(screen.getByRole('button', { name: sample('Play', 'Original Mix') })).toBeInTheDocument()
  })
})

it('offers no sample control at all where there is certainly no sample to play', () => {
  // Availability is settled before the card is drawn -- `has_preview` rides along on the candidate the
  // queue returns -- so a card with no sample shows "Use this" alone, not a button that can only fail.
  // Two ways to be certain: Deezer answered no, or the candidate never had a Deezer identity to ask
  // about. A `null` beside a real `deezer_id` is the genuine unknown -- a row older than the column --
  // and it keeps its button and works as it always has.
  withStubbedPlayer(() => {
    const row: Bundle = { request: { ...baseRequest, id: 1, state: 'awaiting_review', error_message: null },
      candidates: [cand(10, 'Original Mix', false), cand(11, 'Extended Mix', true), cand(12, 'Radio Edit', null),
        cand(13, 'Live', null, null)],
      catalog: null, track: null, rejection: null }
    const { container } = render(<DownloadPage live={makeLive([row])} />)
    const buttons = [...container.querySelectorAll('.candidate')].map(c => c.querySelectorAll('button').length)
    expect(screen.queryByRole('button', { name: /sample of .* \(Original Mix\)/ })).toBeNull()
    // No `deezer_id`, so it never went through enrichment and the route would 404 without calling Deezer.
    expect(screen.queryByRole('button', { name: /sample of .* \(Live\)/ })).toBeNull()
    expect(buttons).toEqual([1, 2, 2, 1])
    expect(screen.getByRole('button', { name: sample('Play', 'Extended Mix') })).toBeEnabled()
    expect(screen.getByRole('button', { name: sample('Play', 'Radio Edit') })).toBeEnabled()
  })
})

it('blames the press, not the candidate, when a load fails -- and keeps the button live', () => {
  // Whether the candidate has a sample was answered before this card existed. A load that fails here is a
  // signed URL that expired or Deezer unreachable at press time: a transient message, never a latch.
  withStubbedPlayer(() => {
    const { container } = render(<DownloadPage live={makeLive([choiceRow(1, ['Original Mix', 'Extended Mix'])])} />)
    const el = container.querySelector('audio') as HTMLAudioElement
    fireEvent.click(screen.getByRole('button', { name: sample('Play', 'Original Mix') }))
    fireEvent.error(el)
    expect(screen.getByText("Couldn't load — try again")).toBeInTheDocument()
    expect(lit(container)).toEqual([])
    expect(screen.getByRole('button', { name: sample('Play', 'Original Mix') })).toBeEnabled()
    // And it is gone the moment it is tried again, rather than sitting there as a verdict.
    fireEvent.click(screen.getByRole('button', { name: sample('Play', 'Original Mix') }))
    expect(screen.queryByText("Couldn't load — try again")).toBeNull()
  })
})

it('lights exactly one card, and moves the light with the press', () => {
  withStubbedPlayer(() => {
    const { container } = render(<DownloadPage live={makeLive([choiceRow(1, ['Original Mix', 'Extended Mix'])])} />)
    expect(lit(container)).toEqual([])
    fireEvent.click(screen.getByRole('button', { name: sample('Play', 'Original Mix') }))
    expect(lit(container)).toEqual(['(Original Mix)'])
    fireEvent.click(screen.getByRole('button', { name: sample('Play', 'Extended Mix') }))
    expect(lit(container)).toEqual(['(Extended Mix)'])
  })
})

it('sweeps an indeterminate sliver until there is a real position, then fills the rail and counts up', () => {
  withStubbedPlayer(() => {
    const { container } = render(<DownloadPage live={makeLive([choiceRow(1, ['Original Mix'])])} />)
    const el = container.querySelector('audio') as HTMLAudioElement
    const card = container.querySelector('.candidate')!
    fireEvent.click(screen.getByRole('button', { name: sample('Play', 'Original Mix') }))
    // Lit on the press, not on the audio: the card is already the playing card, it just has no position.
    expect(card.querySelector('.sample-rail')).toHaveClass('waiting')
    expect(railWidth(card)).toBe('')
    expect(screen.getByText('0:00')).toBeInTheDocument()
    tick(el, 7.5)
    expect(card.querySelector('.sample-rail')).not.toHaveClass('waiting')
    expect(railWidth(card)).toBe('scaleX(0.25)')
    expect(screen.getByText('0:07')).toBeInTheDocument()
  })
})

it('falls back to the length of a Deezer preview while the element still says NaN', () => {
  // `duration` is NaN until metadata lands, and scaleX(NaN) draws nothing and throws nothing.
  withStubbedPlayer(() => {
    const { container } = render(<DownloadPage live={makeLive([choiceRow(1, ['Original Mix'])])} />)
    const el = container.querySelector('audio') as HTMLAudioElement
    fireEvent.click(screen.getByRole('button', { name: sample('Play', 'Original Mix') }))
    tick(el, 15, NaN)
    expect(railWidth(container.querySelector('.candidate')!)).toBe('scaleX(0.5)')
  })
})

it('stops the sliver and says so when a sample is four seconds late', () => {
  vi.useFakeTimers()
  try {
    withStubbedPlayer(() => {
      const { container } = render(<DownloadPage live={makeLive([choiceRow(1, ['Original Mix'])])} />)
      fireEvent.click(screen.getByRole('button', { name: sample('Play', 'Original Mix') }))
      act(() => { vi.advanceTimersByTime(3900) })
      expect(screen.queryByText('Still loading the sample…')).toBeNull()
      act(() => { vi.advanceTimersByTime(200) })
      expect(screen.getByText('Still loading the sample…')).toBeInTheDocument()
      expect(container.querySelector('.sample-rail')).toHaveClass('stalled')
    })
  } finally { vi.useRealTimers() }
})

it('keeps the rail full while the ended card fades, and puts the control back to play', () => {
  // The clock is kept past `ended` on purpose: a rail that emptied as it faded would read as the clip
  // being wound back rather than as one that finished.
  withStubbedPlayer(() => {
    const { container } = render(<DownloadPage live={makeLive([choiceRow(1, ['Original Mix'])])} />)
    const el = container.querySelector('audio') as HTMLAudioElement
    fireEvent.click(screen.getByRole('button', { name: sample('Play', 'Original Mix') }))
    tick(el, 30)
    fireEvent.ended(el)
    expect(lit(container)).toEqual([])
    expect(railWidth(container.querySelector('.candidate')!)).toBe('scaleX(1)')
    expect(screen.getByRole('button', { name: sample('Play', 'Original Mix') })).toBeInTheDocument()
  })
})

it('stops the clip when a candidate is chosen, rather than orphaning it with the row', () => {
  // Choosing unmounts the row, its candidates and its Stop button; the clip used to play on with nothing
  // on screen able to end it.
  const choose = vi.spyOn(api, 'choose').mockResolvedValue(baseRequest)
  try {
    withStubbedPlayer((_play, pause) => {
      const { container } = render(<DownloadPage live={makeLive([choiceRow(1, ['Original Mix'])])} />)
      fireEvent.click(screen.getByRole('button', { name: sample('Play', 'Original Mix') }))
      pause.mockClear()
      fireEvent.click(screen.getByText('Use this'))
      expect(pause).toHaveBeenCalled()
      expect(lit(container)).toEqual([])
      expect(choose).toHaveBeenCalledWith(1, 10)
    })
  } finally { choose.mockRestore() }
})

it('stops whatever is playing on Esc, from anywhere on the page', () => {
  withStubbedPlayer((_play, pause) => {
    const { container } = render(<DownloadPage live={makeLive([choiceRow(1, ['Original Mix'])])} />)
    fireEvent.click(screen.getByRole('button', { name: sample('Play', 'Original Mix') }))
    pause.mockClear()
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(pause).toHaveBeenCalled()
    expect(lit(container)).toEqual([])
    expect(screen.getByRole('button', { name: sample('Play', 'Original Mix') })).toBeInTheDocument()
  })
})

it('lets go of the audio the moment a clip is over', () => {
  // Nothing on screen needs the bytes once the owner has stopped listening, and the next press signs a
  // fresh URL anyway -- so the element is not left holding a buffer it will never play again.
  withStubbedPlayer(() => {
    const load = vi.spyOn(HTMLMediaElement.prototype, 'load')
    const { container } = render(<DownloadPage live={makeLive([choiceRow(1, ['Original Mix'])])} />)
    const el = container.querySelector('audio') as HTMLAudioElement
    fireEvent.click(screen.getByRole('button', { name: sample('Play', 'Original Mix') }))
    expect(el.getAttribute('src')).toContain('/api/candidates/10/preview')
    load.mockClear()
    fireEvent.click(screen.getByRole('button', { name: sample('Stop', 'Original Mix') }))
    expect(el.getAttribute('src')).toBeNull()
    expect(load).toHaveBeenCalled()
    // The same on a clip that runs out on its own, and there is no error to blame on a released element.
    fireEvent.click(screen.getByRole('button', { name: sample('Play', 'Original Mix') }))
    fireEvent.ended(el)
    expect(el.getAttribute('src')).toBeNull()
    fireEvent.error(el)
    expect(screen.queryByText("Couldn't load — try again")).toBeNull()
  })
})

it('stops the sample and lets go of it when the page goes away', () => {
  // `App` swaps this page out for Library or Settings; a detached <audio> would otherwise keep playing
  // with no Stop button left anywhere in the app, and keep its buffer for as long as the app is open.
  withStubbedPlayer((_play, pause) => {
    const load = vi.spyOn(HTMLMediaElement.prototype, 'load')
    const { container, unmount } = render(<DownloadPage live={makeLive([choiceRow(1, ['Original Mix'])])} />)
    const el = container.querySelector('audio') as HTMLAudioElement
    fireEvent.click(screen.getByRole('button', { name: sample('Play', 'Original Mix') }))
    pause.mockClear(); load.mockClear()
    unmount()
    expect(pause).toHaveBeenCalled()
    expect(load).toHaveBeenCalled()
    expect(el.getAttribute('src')).toBeNull()
  })
})

it('tells same-titled candidates apart by the version in the button name', () => {
  // The ordinary Choose window: one track, several versions of it. Both cards are in the same state, so
  // the version is the only thing separating their buttons -- for a screen reader and for getByRole,
  // which throws on two matches.
  withStubbedPlayer(() => {
    render(<DownloadPage live={makeLive([choiceRow(1, ['Original Mix', 'Extended Mix'])])} />)
    expect(screen.getByRole('button', { name: sample('Play', 'Original Mix') })).toBeEnabled()
    expect(screen.getByRole('button', { name: sample('Play', 'Extended Mix') })).toBeEnabled()
  })
})

it('keeps the second sample playing when the first press aborts its own play promise', async () => {
  // Pausing the element and repointing its src -- both of which a switch does -- reject a play() that has
  // not settled yet, with an AbortError that arrives after the new candidate is already the playing one.
  // The route resolves against Deezer before anything starts, so there is real time for a second press.
  let abort: (e: unknown) => void = () => undefined
  const play = vi.spyOn(HTMLMediaElement.prototype, 'play')
    .mockImplementationOnce(() => new Promise<void>((_resolve, reject) => { abort = reject }))
    .mockImplementation(() => Promise.resolve())
  const pause = vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => undefined)
  try {
    render(<DownloadPage live={makeLive([choiceRow(1, ['Original Mix', 'Extended Mix'])])} />)
    fireEvent.click(screen.getByRole('button', { name: sample('Play', 'Original Mix') }))
    fireEvent.click(screen.getByRole('button', { name: sample('Play', 'Extended Mix') }))
    await act(async () => { abort(new DOMException('interrupted by a new load request', 'AbortError')) })
    // The late rejection belongs to the candidate that was interrupted, not to the one now playing.
    expect(screen.getByRole('button', { name: sample('Stop', 'Extended Mix') })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: sample('Play', 'Original Mix') })).toBeEnabled()
  } finally { play.mockRestore(); pause.mockRestore() }
})
