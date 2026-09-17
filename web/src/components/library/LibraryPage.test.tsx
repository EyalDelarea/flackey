import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import LibraryPage from './LibraryPage'
import { ApiError, api } from '../../api'
import type { Bundle, Playlist, Stats } from '../../api'
import type { Live } from '../../live'

vi.mock('../../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api')>()
  return { ...actual, api: { ...actual.api, library: vi.fn(), reveal: vi.fn(), retry: vi.fn() } }
})

function makeLive(): Live {
  const stats: Stats = { tracks: 2, bytes: 1000, playlists: 1, rejections: 0, library_root: '/lib', playlist_dir: '/lib/Playlists', requests_by_state: {} }
  return {
    health: null, bundles: new Map(), playlists: [],
    stats, settings: null,
    fetchProgress: [], libraryVersion: 0, loadError: null, loading: false, connected: true, lastSeen: null,
    upgradeActivity: null, setUpgradeActivity: () => undefined, update: null, updateDownload: null,
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

it('refreshes missing files and re-reads the library without relaunching', async () => {
  vi.mocked(api.library).mockResolvedValue([])
  const refreshApi = vi.spyOn(api, 'refreshLibrary').mockResolvedValue({ removed: 1 })
  const refreshLive = vi.fn().mockResolvedValue(undefined)
  render(<LibraryPage live={{ ...makeLive(), refreshLibrary: refreshLive }} selectedPlaylist={null} />)
  fireEvent.click(screen.getByRole('button', { name: 'Refresh library' }))
  await waitFor(() => expect(refreshApi).toHaveBeenCalled())
  await waitFor(() => expect(refreshLive).toHaveBeenCalled())
  expect(screen.getByText('Removed 1 missing file from the library.')).toBeInTheDocument()
})

it('leaves the toolbar free of drag layers, and search still works', async () => {
  vi.mocked(api.library).mockResolvedValue([])
  const { container } = render(<LibraryPage live={makeLive()} selectedPlaylist={null} />)
  expect(container.querySelector('.toolbar-drag')).toBeNull()
  expect(container.querySelector('.pywebview-drag-region')).toBeNull()

  fireEvent.change(screen.getByLabelText('search library'), { target: { value: 'abc' } })
  await waitFor(() => expect(api.library).toHaveBeenCalledWith('abc', null))
})

it('resets the folder filter, not just its display, when the folder disappears from a new result set', async () => {
  const trackIn = (folder: string, id: number, title: string) => ({
    id, path: `/lib/${folder}/${title}.mp3`, fmt: 'mp3', bitrate_kbps: 320, cutoff_hz: 20000, file_size: 100,
    artist: 'Artist', title, mix_name: '', duration_s: 180, isrc: null, catalog_track_id: null,
    request_id: null, added_at: '', verified_at: null, spectrogram_path: null, catalog: null,
    source: 'deezer_bot', source_fmt: null, bit_depth: null, sample_rate: null,
  })
  vi.mocked(api.library).mockResolvedValueOnce([trackIn('Example Artist', 1, 'Old Track'), trackIn('Other Artist', 2, 'Other Track')])
  render(<LibraryPage live={makeLive()} selectedPlaylist={null} />)
  await screen.findByText('Artist – Old Track')

  fireEvent.change(screen.getByLabelText('Folder'), { target: { value: 'Example Artist' } })
  await waitFor(() => expect(screen.queryByText('Artist – Other Track')).not.toBeInTheDocument())

  // A search that only matches a track outside the selected folder: the new result set no longer offers
  // "Example Artist" as an option at all.
  vi.mocked(api.library).mockResolvedValueOnce([trackIn('Other Artist', 3, 'Summer Anthem')])
  fireEvent.change(screen.getByLabelText('search library'), { target: { value: 'Summer' } })

  await screen.findByText('Artist – Summer Anthem')
  expect(screen.getByLabelText('Folder')).toHaveValue('all')
})

it('offers Clear filters and explains an empty first library differently from an over-filtered one', async () => {
  vi.mocked(api.library).mockResolvedValueOnce([])
  render(<LibraryPage live={makeLive()} selectedPlaylist={null} />)
  await screen.findByText(/Your finished tracks will appear here/)
  expect(screen.queryByText('Clear filters')).not.toBeInTheDocument()

  vi.mocked(api.library).mockResolvedValueOnce([])
  fireEvent.change(screen.getByLabelText('search library'), { target: { value: 'nothing matches' } })
  await screen.findByText(/No tracks match these filters/)
  fireEvent.click(screen.getAllByText('Clear filters')[0])
  expect(screen.getByLabelText('search library')).toHaveValue('')
})

it('offers a Rekordbox import guide once tracks exist, pointing at the library folder', async () => {
  vi.mocked(api.library).mockResolvedValueOnce([])
  render(<LibraryPage live={makeLive()} selectedPlaylist={null} />)
  await screen.findByText('Ready for Rekordbox')

  vi.mocked(api.reveal).mockResolvedValue({ ok: true })
  fireEvent.click(screen.getByText('Show in Finder'))
  await waitFor(() => expect(api.reveal).toHaveBeenCalledWith('/lib'))

  fireEvent.click(screen.getByText('Got it'))
  expect(screen.queryByText('Ready for Rekordbox')).not.toBeInTheDocument()
})

it('shows failed and in-progress playlist entries beside the filed tracks', async () => {
  vi.mocked(api.library).mockResolvedValue([])
  const playlist: Playlist = { id: 7, source_url: 'u', name: 'Goa Set', created_at: '', updated_at: '', track_ids: [1], track_positions: [1, 4], file: '/lib/Goa.m3u8' }
  const request = (id: number, position: number, raw_text: string, state: Bundle['request']['state']) => ({
    request: { id, playlist_id: 7, playlist_position: position, raw_text, state },
  }) as Bundle
  const live = { ...makeLive(), playlists: [playlist], bundles: new Map([
    [2, request(2, 2, 'Unmatched track', 'not_found')],
    [3, request(3, 3, 'Still searching', 'fetching')],
    [4, request(4, 4, 'Old failure, now filed', 'not_found')],
  ]) }
  render(<LibraryPage live={live} selectedPlaylist={7} />)
  expect(await screen.findByText('Import status')).toBeInTheDocument()
  expect(screen.getByText('Unmatched track')).toBeInTheDocument()
  expect(screen.getByText('No match found')).toBeInTheDocument()
  expect(screen.getByText('Still searching')).toBeInTheDocument()
  expect(screen.queryByText('Old failure, now filed')).not.toBeInTheDocument()
  expect(screen.getByText('Getting file')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
})
