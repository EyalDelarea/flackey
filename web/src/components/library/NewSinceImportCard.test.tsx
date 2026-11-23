import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import NewSinceImportCard from './NewSinceImportCard'
import { ApiError, api } from '../../api'

vi.mock('../../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api')>()
  return { ...actual, api: { ...actual.api, newSinceImport: vi.fn(), markImported: vi.fn() } }
})

const FILE = '/lib/Playlists/Flackey - New.m3u8'

it('counts the new tracks and sends back the newest one it showed when the owner says they imported', async () => {
  vi.mocked(api.newSinceImport).mockResolvedValue({ count: 3, file: FILE, through: '2026-11-20T10:00:00+00:00' })
  vi.mocked(api.markImported).mockResolvedValue({ count: 0, file: FILE, through: null })
  const onShowFile = vi.fn()
  render(<NewSinceImportCard version={0} onShowFile={onShowFile} onError={vi.fn()} />)
  await waitFor(() => expect(screen.getByText('3 new tracks for Rekordbox')).toBeInTheDocument())
  fireEvent.click(screen.getByText('Show playlist file'))
  expect(onShowFile).toHaveBeenCalledWith(FILE)
  fireEvent.click(screen.getByText('I’ve imported these'))
  await waitFor(() => expect(screen.getByText(/Nothing new since your last import/)).toBeInTheDocument())
  expect(api.markImported).toHaveBeenCalledWith('2026-11-20T10:00:00+00:00')
  expect(screen.queryByText('I’ve imported these')).not.toBeInTheDocument()
})

it('re-reads the count when the library changes', async () => {
  vi.mocked(api.newSinceImport).mockResolvedValueOnce({ count: 0, file: FILE, through: null })
    .mockResolvedValueOnce({ count: 1, file: FILE, through: '2026-11-21T00:00:00+00:00' })
  const { rerender } = render(<NewSinceImportCard version={0} onShowFile={vi.fn()} onError={vi.fn()} />)
  await waitFor(() => expect(screen.getByText('Nothing new for Rekordbox')).toBeInTheDocument())
  rerender(<NewSinceImportCard version={1} onShowFile={vi.fn()} onError={vi.fn()} />)
  await waitFor(() => expect(screen.getByText('1 new track for Rekordbox')).toBeInTheDocument())
})

it('says why when the press could not be saved', async () => {
  vi.mocked(api.newSinceImport).mockResolvedValue({ count: 1, file: FILE, through: '2026-11-21T00:00:00+00:00' })
  vi.mocked(api.markImported).mockRejectedValue(new ApiError(500, 'Database is locked'))
  const onError = vi.fn()
  render(<NewSinceImportCard version={0} onShowFile={vi.fn()} onError={onError} />)
  await waitFor(() => expect(screen.getByText('I’ve imported these')).toBeInTheDocument())
  fireEvent.click(screen.getByText('I’ve imported these'))
  await waitFor(() => expect(onError).toHaveBeenCalledWith('Database is locked'))
  expect(screen.getByText('1 new track for Rekordbox')).toBeInTheDocument()
})

it('is one card: the count and its steps, I\u2019ve imported these first, and the first-time tip until dismissed', async () => {
  vi.mocked(api.newSinceImport).mockResolvedValue({ count: 35, file: FILE, through: '2026-11-21T00:00:00+00:00' })
  const guide = { revealLabel: 'Show in Finder', onReveal: vi.fn(), onDismiss: vi.fn() }
  const { container, rerender } = render(<NewSinceImportCard version={0} onShowFile={vi.fn()} onError={vi.fn()} guide={guide} />)
  await waitFor(() => expect(screen.getByText('35 new tracks for Rekordbox')).toBeInTheDocument())
  expect(screen.getByText('File → Import → Playlist → “Flackey - New”.', { exact: false })).toBeInTheDocument()
  expect(screen.getByText('I’ve imported these')).toHaveClass('btn-primary')
  expect(container.querySelectorAll('.pl-card')).toHaveLength(1)
  fireEvent.click(screen.getByText('Show in Finder'))
  expect(guide.onReveal).toHaveBeenCalled()
  fireEvent.click(screen.getByText('Dismiss'))
  expect(guide.onDismiss).toHaveBeenCalled()
  rerender(<NewSinceImportCard version={0} onShowFile={vi.fn()} onError={vi.fn()} guide={null} />)
  expect(screen.queryByText(/First time\?/)).not.toBeInTheDocument()
})
