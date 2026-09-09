import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import LibraryPage from './LibraryPage'
import { ApiError, api } from '../../api'
import type { Stats } from '../../api'
import type { Live } from '../../live'

vi.mock('../../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api')>()
  return { ...actual, api: { ...actual.api, library: vi.fn(), reveal: vi.fn() } }
})

function makeLive(): Live {
  const stats: Stats = { tracks: 2, bytes: 1000, playlists: 1, rejections: 0, library_root: '/lib', playlist_dir: '/lib/Playlists', requests_by_state: {} }
  return {
    health: null, bundles: new Map(), playlists: [],
    stats, settings: null,
    fetchProgress: null, libraryVersion: 0, loadError: null, loading: false,
    refresh: async () => undefined, refreshLibrary: async () => undefined, retry: async () => undefined,
    setHealth: () => undefined, setSettings: () => undefined, dropBundle: () => undefined,
  }
}

it('ignores stale responses and shows the latest result', async () => {
  let resolve1: (v: any) => void, resolve2: (v: any) => void
  const promise1 = new Promise(r => { resolve1 = r })
  const promise2 = new Promise(r => { resolve2 = r })

  vi.mocked(api.library).mockReturnValueOnce(promise1 as any).mockReturnValueOnce(promise2 as any)
  vi.mocked(api.reveal).mockResolvedValue({ ok: true })

  const { rerender } = render(<LibraryPage live={makeLive()} selectedPlaylist={null} />)

  // First request starts (with q='', debounce delay is 0)
  await waitFor(() => expect(vi.mocked(api.library)).toHaveBeenCalledTimes(1))

  // Change selectedPlaylist, triggering second request
  rerender(<LibraryPage live={makeLive()} selectedPlaylist={999} />)
  await waitFor(() => expect(vi.mocked(api.library)).toHaveBeenCalledTimes(2))

  // Resolve second request first (returns different data)
  resolve2!([{ id: 2, path: '/lib/B.mp3', fmt: 'mp3', bitrate_kbps: 320, cutoff_hz: 20000, file_size: 100, artist: 'Artist B', title: 'Song B', mix_name: 'Mix B', duration_s: 180, isrc: null, catalog_track_id: null, request_id: null, added_at: '', verified_at: null, spectrogram_path: null, catalog: null }])

  // Resolve first request (should be ignored)
  resolve1!([{ id: 1, path: '/lib/A.mp3', fmt: 'mp3', bitrate_kbps: 128, cutoff_hz: 18000, file_size: 50, artist: 'Artist A', title: 'Song A', mix_name: 'Mix A', duration_s: 120, isrc: null, catalog_track_id: null, request_id: null, added_at: '', verified_at: null, spectrogram_path: null, catalog: null }])

  await waitFor(() => expect(screen.getByText('Artist B – Song B')).toBeInTheDocument())
  expect(screen.queryByText('Artist A – Song A')).not.toBeInTheDocument()
})

it('shows a red banner when api.library fails', async () => {
  vi.mocked(api.library).mockRejectedValueOnce(new ApiError(500, 'Server error'))
  render(<LibraryPage live={makeLive()} selectedPlaylist={null} />)
  await waitFor(() => expect(screen.getByText('Server error')).toBeInTheDocument())
  expect(screen.getByText('Server error')).toBeInTheDocument()
  fireEvent.click(screen.getByText('Dismiss'))
  expect(screen.queryByText('Server error')).not.toBeInTheDocument()
})

it('renders the toolbar drag layer only when inset, and search still works either way', async () => {
  vi.mocked(api.library).mockResolvedValue([])
  const { container, rerender } = render(<LibraryPage live={makeLive()} selectedPlaylist={null} />)
  expect(container.querySelector('.pywebview-drag-region')).toBeNull()

  rerender(<LibraryPage live={makeLive()} selectedPlaylist={null} inset />)
  expect(container.querySelector('.toolbar-drag.pywebview-drag-region')).not.toBeNull()

  fireEvent.change(screen.getByLabelText('search library'), { target: { value: 'abc' } })
  await waitFor(() => expect(api.library).toHaveBeenCalledWith('abc', null))
})
