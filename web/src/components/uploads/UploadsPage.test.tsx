import { act, render, screen, waitFor } from '@testing-library/react'
import { vi, afterEach } from 'vitest'
import UploadsPage from './UploadsPage'
import { api } from '../../api'
import type { UploadFeed } from '../../api'

const feed = (over: Partial<UploadFeed> = {}): UploadFeed => ({
  enabled: true, provider: 'soulseek', error: null, uploads: [],
  summary: { total: 0, active: 0, completed: 0, peers: 0, bytes: 0 }, ...over,
})
const serve = (f: UploadFeed) => vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(f), { status: 200 })))
// restoreAllMocks as well as unstubAllGlobals: the sharing test below spies on the api module, and a spy
// left in place would answer for every test that runs after it.
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

it('lists who is pulling from the shared library, with the totals', async () => {
  serve(feed({
    uploads: [{ id: 'a', peer: 'peer-alpha', file: 'Orphic Thrench.aiff', folder: 'Hallucinogen', size: 51223918,
                bytes: 25611959, pct: 50, state: 'InProgress', speed_bps: 3400000, started_at: '2026-09-08T01:39:19Z', ended_at: null }],
    summary: { total: 1, active: 1, completed: 0, peers: 1, bytes: 25611959 },
  }))
  render(<UploadsPage />)
  await waitFor(() => expect(screen.getByText('peer-alpha')).toBeInTheDocument())
  expect(screen.getByText('Orphic Thrench.aiff')).toBeInTheDocument()
  expect(screen.getByText('Hallucinogen')).toBeInTheDocument()
  expect(screen.getByText('50% · 3.4 MB/s')).toBeInTheDocument()
  expect(screen.getByText('person')).toBeInTheDocument()          // one peer, singular
  expect(screen.getByText('sending now').previousSibling).toHaveTextContent('1')
})

it('says nothing has been uploaded yet rather than showing an empty table', async () => {
  serve(feed())
  render(<UploadsPage />)
  await waitFor(() => expect(screen.getByText(/Nobody has downloaded from you yet/)).toBeInTheDocument())
})

it('explains when Soulseek is not set up instead of looking broken', async () => {
  serve(feed({ enabled: false, provider: null }))
  render(<UploadsPage />)
  await waitFor(() => expect(screen.getByText(/Soulseek isn't set up/)).toBeInTheDocument())
})

it('warns when the port is closed', async () => {
  serve(feed())
  vi.spyOn(api, 'sharing').mockResolvedValue({ port: 50300, enabled: true, checking: false, mapping: null, reachable: false,
    public_ip: null, lan_ip: null, gateway: null, checked_at: null, error: null })
  render(<UploadsPage />)
  await waitFor(() => expect(screen.getByText(/Your Soulseek port is closed/)).toBeInTheDocument())
})

it('says nothing about the port when it is open', async () => {
  serve(feed())
  vi.spyOn(api, 'sharing').mockResolvedValue({ port: 50300, enabled: true, checking: false, mapping: 'natpmp', reachable: true,
    public_ip: null, lan_ip: null, gateway: null, checked_at: null, error: null })
  render(<UploadsPage />)
  await waitFor(() => expect(screen.getByText(/Nobody has downloaded from you yet/)).toBeInTheDocument())
  expect(screen.queryByText(/Your Soulseek port is closed/)).not.toBeInTheDocument()
})

it('renders a peer name as text, never as markup', async () => {
  const nasty = '<img src=x onerror=alert(1)>'
  serve(feed({
    uploads: [{ id: 'b', peer: nasty, file: 'a.flac', folder: '', size: 10, bytes: 10, pct: 100,
                state: 'Completed, Succeeded', speed_bps: 0, started_at: null, ended_at: null }],
    summary: { total: 1, active: 0, completed: 1, peers: 1, bytes: 10 },
  }))
  const { container } = render(<UploadsPage />)
  await waitFor(() => expect(screen.getByText(nasty)).toBeInTheDocument())
  expect(container.querySelector('img')).toBeNull()
})

it('says what the server said rather than claiming it cannot be reached (WEB-8)', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ detail: 'The sidecar is restarting.' }), { status: 503 })))
  render(<UploadsPage />)
  await waitFor(() => expect(screen.getByText('The sidecar is restarting.')).toBeInTheDocument())
  expect(screen.queryByText("Can't reach Flackey.")).not.toBeInTheDocument()
})

it('still says it cannot reach Flackey when nothing answered', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch') }))
  render(<UploadsPage />)
  await waitFor(() => expect(screen.getByText("Can't reach Flackey.")).toBeInTheDocument())
})

it('does not let a slow, older answer replace a newer one, or land after the page is gone (WEB-8)', async () => {
  vi.useFakeTimers()
  try {
    const answers: ((f: UploadFeed) => void)[] = []
    vi.spyOn(api, 'sharing').mockResolvedValue(null as never)
    vi.spyOn(api, 'uploads').mockImplementation(() => new Promise(r => { answers.push(r) }))
    const { unmount } = render(<UploadsPage />)
    await act(async () => { await vi.advanceTimersByTimeAsync(4000) })
    expect(answers).toHaveLength(2)
    const peer = (name: string) => feed({ uploads: [{ id: name, peer: name, file: 'f.aiff', folder: 'd', size: 1, bytes: 1, pct: 100,
      state: 'Completed', speed_bps: 0, started_at: null, ended_at: null }], summary: { total: 1, active: 0, completed: 1, peers: 1, bytes: 1 } })
    await act(async () => { answers[1](peer('newer-peer')) })
    await act(async () => { answers[0](peer('older-peer')) })
    expect(screen.getByText('newer-peer')).toBeInTheDocument()
    expect(screen.queryByText('older-peer')).not.toBeInTheDocument()
    unmount()
  } finally { vi.useRealTimers() }
})

const up = (over: Partial<import('../../api').Upload>) => ({ id: null, peer: 'robsono', file: 'x.aiff', folder: 'DJ Library', size: 72_000_000,
  bytes: 72_000_000, pct: 100, state: 'Completed, Succeeded', speed_bps: 0, started_at: null, ended_at: null, ...over })

it('groups uploads by person: one header with the count and total, compact finished rows, a bar only while sending', async () => {
  const uploads = [
    ...Array.from({ length: 6 }, (_, i) => up({ id: `r${i}`, file: `Track ${i}.aiff` })),
    up({ id: 'r6', file: 'Rezonate.aiff', state: 'Completed, Errored' }),
    up({ id: 'p1', peer: 'peer-alpha', file: 'Orphic Thrench.aiff', folder: 'Hallucinogen', size: 51_000_000, bytes: 25_500_000,
         pct: 50, state: 'InProgress', speed_bps: 3_400_000 }),
  ]
  serve(feed({ uploads, summary: { total: 8, active: 1, completed: 7, peers: 2, bytes: 0 } }))
  const { container } = render(<UploadsPage />)
  await waitFor(() => expect(screen.getByText('robsono')).toBeInTheDocument())
  // Named once per person, not on every row, and whoever is sending right now comes first.
  expect(screen.getAllByText('robsono')).toHaveLength(1)
  const heads = [...container.querySelectorAll('.up-head .who')].map(e => e.textContent)
  expect(heads).toEqual(['peer-alpha', 'robsono'])
  expect(screen.getByText('7 files · 504 MB')).toBeInTheDocument()
  // Only the sending file draws a bar; finished ones get a tick, and a failed one says how it ended.
  expect(container.querySelectorAll('.up-bar')).toHaveLength(1)
  expect(screen.queryByText('Succeeded')).not.toBeInTheDocument()
  // Four rows, then the rest behind "+ N more".
  expect(screen.queryByText('Track 4.aiff')).not.toBeInTheDocument()
  screen.getByRole('button', { name: '+ 3 more' }).click()
  await waitFor(() => expect(screen.getByText('Track 4.aiff')).toBeInTheDocument())
  expect(screen.getAllByLabelText('Sent')).toHaveLength(6)
  expect(screen.getByText('Errored')).toBeInTheDocument()
})

it('never hides a live upload behind "+ N more", however many one person has going', async () => {
  const uploads = [
    ...Array.from({ length: 6 }, (_, i) => up({ id: `s${i}`, file: `Live ${i}.aiff`, pct: 10 * i, bytes: 1, state: 'InProgress', speed_bps: 1e6 })),
    ...Array.from({ length: 3 }, (_, i) => up({ id: `d${i}`, file: `Done ${i}.aiff` })),
  ]
  serve(feed({ uploads, summary: { total: 9, active: 6, completed: 3, peers: 1, bytes: 0 } }))
  const { container } = render(<UploadsPage />)
  await waitFor(() => expect(screen.getByText('Live 5.aiff')).toBeInTheDocument())
  expect(container.querySelectorAll('.up-bar')).toHaveLength(6)
  expect(screen.queryByText('Done 0.aiff')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: '+ 3 more' })).toBeInTheDocument()
})
