import { render, screen, waitFor } from '@testing-library/react'
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
    uploads: [{ id: 'a', peer: 'loginty', file: 'Orphic Thrench.aiff', folder: 'Hallucinogen', size: 51223918,
                bytes: 25611959, pct: 50, state: 'InProgress', speed_bps: 3400000, started_at: '2026-09-08T01:39:19Z', ended_at: null }],
    summary: { total: 1, active: 1, completed: 0, peers: 1, bytes: 25611959 },
  }))
  render(<UploadsPage />)
  await waitFor(() => expect(screen.getByText('loginty')).toBeInTheDocument())
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

it('explains when Soulseek is switched off instead of looking broken', async () => {
  serve(feed({ enabled: false, provider: null }))
  render(<UploadsPage />)
  await waitFor(() => expect(screen.getByText(/Soulseek is off/)).toBeInTheDocument())
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
