import { render, screen, waitFor, fireEvent, act } from '@testing-library/react'
import { useLive } from './live'
import type { Live } from './live'
import { api } from './api'
import type { AppSettings, Health, Stats } from './api'

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
