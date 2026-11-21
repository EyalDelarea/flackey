import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import TidyFoldersCard from './TidyFoldersCard'
import { api } from '../../api'
import type { TidyGroup } from '../../api'

vi.mock('../../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api')>()
  return { ...actual, api: { ...actual.api, tidyPreview: vi.fn(), tidyApply: vi.fn() } }
})

const groups: TidyGroup[] = [{ folder: 'Miranda!', merges: ['Miranda'], left_behind: 0, conflicts: [],
  moves: [{ track_id: 3, source: '/lib/Miranda/Miranda - C.aiff', dest: '/lib/Miranda!/Miranda - C.aiff' }] }]

it('shows nothing when there is nothing to tidy', async () => {
  vi.mocked(api.tidyPreview).mockResolvedValue({ groups: [] })
  const { container } = render(<TidyFoldersCard version={0} onDone={vi.fn()} onError={vi.fn()} />)
  await waitFor(() => expect(api.tidyPreview).toHaveBeenCalled())
  expect(container).toBeEmptyDOMElement()
})

it('moves nothing until the owner has seen the list and the Rekordbox warning, then confirms', async () => {
  vi.mocked(api.tidyPreview).mockResolvedValue({ groups })
  vi.mocked(api.tidyApply).mockResolvedValue({ moved: 1, failed: [] })
  const onDone = vi.fn()
  render(<TidyFoldersCard version={0} onDone={onDone} onError={vi.fn()} />)
  await waitFor(() => expect(screen.getByText('Review')).toBeInTheDocument())
  expect(screen.queryByText('Move 1 file')).not.toBeInTheDocument()
  fireEvent.click(screen.getByText('Review'))
  expect(screen.getByRole('note')).toHaveTextContent('Rekordbox will show these tracks as missing until it relinks them')
  expect(screen.getByText('Miranda/Miranda - C.aiff')).toBeInTheDocument()
  expect(api.tidyApply).not.toHaveBeenCalled()
  fireEvent.click(screen.getByText('Move 1 file'))
  await waitFor(() => expect(onDone).toHaveBeenCalledWith(expect.stringMatching(/^Moved 1 file\. In Rekordbox, relink/)))
  expect(api.tidyApply).toHaveBeenCalledWith(groups[0].moves)
})

it('cancel closes the list without moving anything', async () => {
  vi.mocked(api.tidyPreview).mockResolvedValue({ groups })
  render(<TidyFoldersCard version={0} onDone={vi.fn()} onError={vi.fn()} />)
  await waitFor(() => expect(screen.getByText('Review')).toBeInTheDocument())
  fireEvent.click(screen.getByText('Review'))
  fireEvent.click(screen.getByText('Cancel'))
  expect(screen.queryByRole('note')).not.toBeInTheDocument()
  expect(api.tidyApply).not.toHaveBeenCalled()
})
