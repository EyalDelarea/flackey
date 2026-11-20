import { render, screen, waitFor, fireEvent, act } from '@testing-library/react'
import { useLive } from './live'
import type { Live } from './live'
import { api, ApiError } from './api'
import type { AppSettings, Bundle, Health, Stats } from './api'

class FakeEventSource {
  static last: FakeEventSource | null = null
  onopen: (() => void) | null = null
  handlers: Record<string, (e: { data: string }) => void> = {}
  addEventListener(name: string, fn: (e: { data: string }) => void) { this.handlers[name] = fn }
  emit(name: string, data: unknown) { this.handlers[name]?.({ data: JSON.stringify(data) }) }
  close() { /* no-op */ }
  constructor(_url: string) { FakeEventSource.last = this }
}

const settings: AppSettings = { library_root: '/lib', data_dir: '/data', version: '0.1.0', telegram_configured: true, log_path: '/data/flackey.log' }
const stats: Stats = { tracks: 0, bytes: 0, playlists: 0, rejections: 0, library_root: '/lib', playlist_dir: '/lib/Playlists', requests_by_state: {} }
const health: Health = { ok: true, version: '0.1.0', telegram_authorized: true, worker_running: true, setup_done: true,
  lossless: { enabled: true, provider: null, fpcalc: true, attempts_24h: {}, raw_mb: 0 } }

function Harness({ liveRef }: { liveRef?: { current: Live | null } }) {
  const live = useLive()
  if (liveRef) liveRef.current = live
  return (<div>
    <div>{live.loading ? 'loading' : 'loaded'}</div>
    <div>{live.loadError ?? 'no-error'}</div>
    <div>{live.health ? 'has-health' : 'no-health'}</div>
    <button onClick={() => { live.retry().catch(() => undefined) }}>retry</button>
  </div>)
}

const upToDate = { ok: true, current: '0.1.0', newer: false, available: false, latest: '0.1.0', url: null,
  release_url: null, size: null, size_label: null, published_at: null, published_date: null, prerelease: false }

beforeEach(() => {
  vi.stubGlobal('EventSource', FakeEventSource)
  vi.spyOn(api, 'queue').mockResolvedValue([])
  vi.spyOn(api, 'settings').mockResolvedValue(settings)
  vi.spyOn(api, 'playlists').mockResolvedValue([])
  vi.spyOn(api, 'stats').mockResolvedValue(stats)
  vi.spyOn(api, 'update').mockResolvedValue(upToDate)
})

it('surfaces a plain-words error when the API is unreachable, and clears it on retry', async () => {
  const healthSpy = vi.spyOn(api, 'health').mockRejectedValueOnce(new Error('network down'))
  render(<Harness />)
  await waitFor(() => expect(screen.getByText("Can't reach Flackey. Is `flackey start` running?")).toBeInTheDocument())
  expect(screen.getByText('no-health')).toBeInTheDocument()
  expect(screen.getByText('loaded')).toBeInTheDocument()

  healthSpy.mockResolvedValueOnce(health)
  fireEvent.click(screen.getByText('retry'))
  await waitFor(() => expect(screen.getByText('has-health')).toBeInTheDocument())
  expect(screen.getByText('no-error')).toBeInTheDocument()
})

it('refresh() rethrows on failure so a caller (e.g. the setup screen) can react', async () => {
  const healthSpy = vi.spyOn(api, 'health').mockResolvedValue(health)
  const liveRef: { current: Live | null } = { current: null }
  render(<Harness liveRef={liveRef} />)
  await waitFor(() => expect(screen.getByText('has-health')).toBeInTheDocument())

  healthSpy.mockRejectedValueOnce(new Error('network down'))
  let caught: unknown
  await act(async () => {
    try { await liveRef.current!.refresh() } catch (e) { caught = e }
  })
  expect((caught as Error)?.message).toBe('network down')
  await waitFor(() => expect(screen.getByText("Can't reach Flackey. Is `flackey start` running?")).toBeInTheDocument())
})


describe('the status event', () => {
  const liveRef: { current: Live | null } = { current: null }
  const start = async () => {
    vi.spyOn(api, 'health').mockResolvedValue(health)
    render(<Harness liveRef={liveRef} />)
    await waitFor(() => expect(screen.getByText('has-health')).toBeInTheDocument())
    return FakeEventSource.last!
  }

  it('folds lossless_provider into health.lossless.provider', async () => {
    // The server's status dict is flat and /api/health is nested. Spreading the event straight in left
    // health.lossless.provider frozen at whatever the last full refresh saw, so the Soulseek line could
    // sit on "Signing in…" long after the worker had signed in.
    const es = await start()
    act(() => es.emit('status', { telegram_authorized: true, worker_running: true, setup_done: true,
      lossless_provider: { name: 'soulseek', status: 'ok', username: 'digger' } }))
    expect(liveRef.current!.health!.lossless!.provider)
      .toEqual({ name: 'soulseek', status: 'ok', username: 'digger' })
  })

  it('still applies the flat flags it carries', async () => {
    const es = await start()
    act(() => es.emit('status', { telegram_authorized: false, worker_running: true, setup_done: true }))
    expect(liveRef.current!.health!.telegram_authorized).toBe(false)
  })

  it('leaves the last known provider alone when an event does not mention it', async () => {
    const es = await start()
    act(() => es.emit('status', { lossless_provider: { name: 'soulseek', status: 'ok', username: 'digger' } }))
    act(() => es.emit('status', { telegram_authorized: false }))
    expect(liveRef.current!.health!.lossless!.provider?.status).toBe('ok')
  })

  it('does not leave a stray flat key on the health object', async () => {
    const es = await start()
    act(() => es.emit('status', { lossless_provider: { name: 'soulseek', status: 'ok', username: 'digger' } }))
    expect('lossless_provider' in liveRef.current!.health!).toBe(false)
  })
})

describe('the automatic update check', () => {
  it('checks once at launch when auto_update_check is not disabled', async () => {
    vi.spyOn(api, 'health').mockResolvedValue(health)
    const updateSpy = vi.spyOn(api, 'update').mockResolvedValue(upToDate)
    const liveRef: { current: Live | null } = { current: null }
    render(<Harness liveRef={liveRef} />)
    await waitFor(() => expect(liveRef.current!.update).toEqual(upToDate))
    expect(updateSpy).toHaveBeenCalledTimes(1)
    expect(updateSpy).toHaveBeenCalledWith('auto')
  })

  it('never calls /api/update when the owner turned auto-check off', async () => {
    vi.spyOn(api, 'health').mockResolvedValue(health)
    vi.spyOn(api, 'settings').mockResolvedValue({ ...settings, auto_update_check: false })
    const updateSpy = vi.spyOn(api, 'update')
    const liveRef: { current: Live | null } = { current: null }
    render(<Harness liveRef={liveRef} />)
    await waitFor(() => expect(screen.getByText('has-health')).toBeInTheDocument())
    expect(updateSpy).not.toHaveBeenCalled()
    expect(liveRef.current!.update).toBeNull()
  })

  it('does not re-check on an SSE reconnect', async () => {
    vi.spyOn(api, 'health').mockResolvedValue(health)
    const updateSpy = vi.spyOn(api, 'update').mockResolvedValue(upToDate)
    const liveRef: { current: Live | null } = { current: null }
    render(<Harness liveRef={liveRef} />)
    await waitFor(() => expect(updateSpy).toHaveBeenCalledTimes(1))
    const es = FakeEventSource.last!
    act(() => { es.onopen?.() })   // the initial open: a no-op beyond clearing the "first open" flag
    act(() => { es.onopen?.() })   // a real reconnect: this one re-runs refresh()
    await waitFor(() => expect(screen.getByText('has-health')).toBeInTheDocument())
    expect(updateSpy).toHaveBeenCalledTimes(1)
  })
})

describe('the update download', () => {
  /* The bus drops events rather than blocking when a subscriber falls behind, and the frame it drops
     could be the terminal one -- which would leave the page believing a finished download is still
     running, with the button disabled for good. */
  it('polls while one is running, so a dropped terminal frame still lands', async () => {
    vi.useFakeTimers()
    try {
      vi.spyOn(api, 'health').mockResolvedValue(health)
      const poll = vi.spyOn(api, 'updateProgress').mockResolvedValue({ state: 'ready', percent: 100,
        received: 8, total: 8, version: '0.1.1', path: '/data/updates/Flackey.pkg', error: null,
        seamless: false, busy: 0 })
      const liveRef: { current: Live | null } = { current: null }
      render(<Harness liveRef={liveRef} />)
      await vi.waitFor(() => expect(FakeEventSource.last).not.toBeNull())
      const es = FakeEventSource.last!
      act(() => { es.emit('status', { update_download: { state: 'downloading', percent: 50, received: 4,
        total: 8, version: '0.1.1', path: null, error: null } }) })
      expect(liveRef.current?.updateDownload?.state).toBe('downloading')
      expect(poll).not.toHaveBeenCalled()   // the poll is a backstop, not the channel
      await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
      expect(poll).toHaveBeenCalled()
      expect(liveRef.current?.updateDownload?.state).toBe('ready')
      // And it stops once there is nothing left to watch.
      const settled = poll.mock.calls.length
      await act(async () => { await vi.advanceTimersByTimeAsync(9000) })
      expect(poll.mock.calls.length).toBe(settled)
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('the first load failing on a server that is up (WEB-1)', () => {
  it('shows what the server said instead of claiming it cannot be reached', async () => {
    vi.spyOn(api, 'health').mockRejectedValueOnce(new ApiError(500, 'database is locked'))
    render(<Harness />)
    await waitFor(() => expect(screen.getByText('database is locked')).toBeInTheDocument())
    expect(screen.queryByText(/Can't reach Flackey/)).not.toBeInTheDocument()
  })

  it('still says something when the error carried no message', async () => {
    vi.spyOn(api, 'health').mockRejectedValueOnce(new ApiError(503, ''))
    render(<Harness />)
    await waitFor(() => expect(screen.getByText(/Flackey hit an error \(503\)/)).toBeInTheDocument())
    expect(screen.queryByText(/Can't reach Flackey/)).not.toBeInTheDocument()
  })
})

describe('queue snapshots that land out of order (WEB-3)', () => {
  const liveRef: { current: Live | null } = { current: null }
  const bundle = (id: number, state: string): Bundle => ({
    request: { id, state } as Bundle['request'], candidates: [], catalog: null, track: null, rejection: null })
  const start = async () => {
    vi.spyOn(api, 'health').mockResolvedValue(health)
    render(<Harness liveRef={liveRef} />)
    await waitFor(() => expect(screen.getByText('has-health')).toBeInTheDocument())
    return FakeEventSource.last!
  }
  const deferred = <T,>() => {
    let resolve!: (v: T) => void
    const promise = new Promise<T>(r => { resolve = r })
    return { promise, resolve }
  }

  it('drops an older /api/queue answer that arrives after a newer one', async () => {
    const es = await start()
    const first = deferred<Bundle[]>(); const second = deferred<Bundle[]>()
    vi.spyOn(api, 'queue').mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    act(() => { es.emit('queue', {}); es.emit('queue', {}) })
    await act(async () => { second.resolve([bundle(1, 'filing')]) })
    await act(async () => { first.resolve([bundle(1, 'fetching')]) })
    expect(liveRef.current!.bundles.get(1)?.request.state).toBe('filing')
  })

  it('keeps a row event that arrived while the snapshot was in flight', async () => {
    const es = await start()
    const snap = deferred<Bundle[]>()
    vi.spyOn(api, 'queue').mockReturnValueOnce(snap.promise)
    act(() => es.emit('queue', {}))
    act(() => es.emit('request', bundle(1, 'filing')))
    await act(async () => { snap.resolve([bundle(1, 'fetching'), bundle(2, 'queued')]) })
    expect(liveRef.current!.bundles.get(1)?.request.state).toBe('filing')
    expect(liveRef.current!.bundles.get(2)?.request.state).toBe('queued')
  })
})

describe('a malformed stream frame (WEB-16)', () => {
  it('is ignored rather than thrown out of the handler', async () => {
    vi.spyOn(api, 'health').mockResolvedValue(health)
    render(<Harness />)
    await waitFor(() => expect(screen.getByText('has-health')).toBeInTheDocument())
    const es = FakeEventSource.last!
    expect(() => act(() => es.handlers.request({ data: '{not json' }))).not.toThrow()
    expect(() => act(() => es.handlers.status({ data: '{not json' }))).not.toThrow()
    expect(screen.getByText('has-health')).toBeInTheDocument()
  })
})
