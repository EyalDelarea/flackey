import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import PasteBar from './PasteBar'
import { ApiError } from '../../api'

const PLACEHOLDER = 'Paste a YouTube, YouTube Music, or Spotify link'

it('submits, clears, and shows the summary', async () => {
  const onSubmit = vi.fn(async () => 'Queued 3 of 5 from "Goa Set" (2 already in library)')
  render(<PasteBar onSubmit={onSubmit} />)
  const input = screen.getByPlaceholderText(PLACEHOLDER) as HTMLInputElement
  fireEvent.change(input, { target: { value: 'https://youtu.be/x' } })
  fireEvent.click(screen.getByText('Add'))
  await waitFor(() => expect(screen.getByText(/Queued 3 of 5/)).toBeInTheDocument())
  expect(onSubmit).toHaveBeenCalledWith('https://youtu.be/x'); expect(input.value).toBe('')
})

it('shows the plain-language error under the field', async () => {
  const onSubmit = vi.fn(async () => { throw new ApiError(400, 'Paste a YouTube, YouTube Music, or Spotify link.') })
  render(<PasteBar onSubmit={onSubmit} />)
  fireEvent.change(screen.getByPlaceholderText(PLACEHOLDER), { target: { value: 'hello' } })
  fireEvent.submit(screen.getByRole('form'))
  await waitFor(() => expect(screen.getByText(/Paste a YouTube, YouTube Music, or Spotify link/)).toBeInTheDocument())
})

it('renders the toolbar drag layer only when inset, and still submits normally either way', async () => {
  const onSubmit = vi.fn(async () => 'Queued 1 of 1')
  const { container, rerender } = render(<PasteBar onSubmit={onSubmit} />)
  expect(container.querySelector('.pywebview-drag-region')).toBeNull()

  rerender(<PasteBar onSubmit={onSubmit} inset />)
  expect(container.querySelector('.toolbar-drag.pywebview-drag-region')).not.toBeNull()

  fireEvent.change(screen.getByPlaceholderText(PLACEHOLDER), { target: { value: 'https://youtu.be/x' } })
  fireEvent.click(screen.getByText('Add'))
  await waitFor(() => expect(onSubmit).toHaveBeenCalledWith('https://youtu.be/x'))
})
