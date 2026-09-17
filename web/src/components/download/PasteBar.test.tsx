import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import PasteBar from './PasteBar'
import { ApiError } from '../../api'

const LABEL = 'Track or playlist link'

it('submits, clears, and shows the summary', async () => {
  const onSubmit = vi.fn(async () => 'Queued 3 of 5 from "Goa Set" (2 already in library)')
  render(<PasteBar onSubmit={onSubmit} />)
  const input = screen.getByLabelText(LABEL) as HTMLInputElement
  fireEvent.change(input, { target: { value: 'https://youtu.be/x' } })
  fireEvent.click(screen.getByText('Add'))
  await waitFor(() => expect(screen.getByText(/Queued 3 of 5/)).toBeInTheDocument())
  expect(onSubmit).toHaveBeenCalledWith('https://youtu.be/x'); expect(input.value).toBe('')
})

it('shows the plain-language error under the field', async () => {
  const onSubmit = vi.fn(async () => { throw new ApiError(400, 'Paste a YouTube, YouTube Music, or Spotify link.') })
  render(<PasteBar onSubmit={onSubmit} />)
  fireEvent.change(screen.getByLabelText(LABEL), { target: { value: 'hello' } })
  fireEvent.submit(screen.getByRole('form'))
  await waitFor(() => expect(screen.getByText(/Paste a YouTube, YouTube Music, or Spotify link/)).toBeInTheDocument())
})

it('does not submit and shows a plain error when the field is empty', async () => {
  const onSubmit = vi.fn(async () => 'unused')
  render(<PasteBar onSubmit={onSubmit} />)
  fireEvent.submit(screen.getByRole('form'))
  await waitFor(() => expect(screen.getByText(/Paste a YouTube, YouTube Music, or Spotify link first/)).toBeInTheDocument())
  expect(onSubmit).not.toHaveBeenCalled()
})

it('leaves the toolbar free of drag layers, and still submits normally', async () => {
  const onSubmit = vi.fn(async () => 'Queued 1 of 1')
  const { container } = render(<PasteBar onSubmit={onSubmit} />)
  expect(container.querySelector('.toolbar-drag')).toBeNull()
  expect(container.querySelector('.pywebview-drag-region')).toBeNull()

  fireEvent.change(screen.getByLabelText(LABEL), { target: { value: 'https://youtu.be/x' } })
  fireEvent.click(screen.getByText('Add'))
  await waitFor(() => expect(onSubmit).toHaveBeenCalledWith('https://youtu.be/x'))
})
