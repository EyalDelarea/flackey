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
  await waitFor(() => expect(screen.getByText('· 3 tracks')).toBeInTheDocument())
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
  await waitFor(() => expect(screen.getByText('· 0 tracks')).toBeInTheDocument())
  rerender(<NewSinceImportCard version={1} onShowFile={vi.fn()} onError={vi.fn()} />)
  await waitFor(() => expect(screen.getByText('· 1 track')).toBeInTheDocument())
})

it('says why when the press could not be saved', async () => {
  vi.mocked(api.newSinceImport).mockResolvedValue({ count: 1, file: FILE, through: '2026-11-21T00:00:00+00:00' })
  vi.mocked(api.markImported).mockRejectedValue(new ApiError(500, 'Database is locked'))
  const onError = vi.fn()
  render(<NewSinceImportCard version={0} onShowFile={vi.fn()} onError={onError} />)
  await waitFor(() => expect(screen.getByText('I’ve imported these')).toBeInTheDocument())
  fireEvent.click(screen.getByText('I’ve imported these'))
  await waitFor(() => expect(onError).toHaveBeenCalledWith('Database is locked'))
  expect(screen.getByText('· 1 track')).toBeInTheDocument()
})
