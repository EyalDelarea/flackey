import { render, screen, fireEvent } from '@testing-library/react'
import WelcomeStep from './WelcomeStep'

it('shows the pitch, the website vinyl artwork, and Get started', () => {
  const onStart = vi.fn()
  const { container } = render(<WelcomeStep onStart={onStart} />)
  expect(screen.getByText('Flackey')).toBeInTheDocument()
  expect(screen.getByText(/Paste a YouTube link\. Get the best copy that exists/)).toBeInTheDocument()
  expect(container.querySelector('img.welcome-vinyl-photo')?.getAttribute('src')).toBe('/silver-vinyl.png')
  // Neither source reads as a required step: either one is enough to download.
  expect(screen.getByText('Two minutes: pick a folder, then connect Telegram, Soulseek or both.')).toBeInTheDocument()
  fireEvent.click(screen.getByText('Get started'))
  expect(onStart).toHaveBeenCalled()
})
