import { render, screen, fireEvent } from '@testing-library/react'
import WelcomeStep from './WelcomeStep'

it('shows the pitch, the rig with four spinning decks, and Get started', () => {
  const onStart = vi.fn()
  const { container } = render(<WelcomeStep onStart={onStart} />)
  expect(screen.getByText('Flackey')).toBeInTheDocument()
  expect(screen.getByText(/Paste a YouTube link\. Get the best copy that exists/)).toBeInTheDocument()
  expect(container.querySelectorAll('.ring')).toHaveLength(4)
  expect(container.querySelectorAll('.screen')).toHaveLength(4)
  expect(container.querySelectorAll('.meter')).toHaveLength(4)
  expect(container.querySelector('img')?.getAttribute('src')).toBe('/welcome-rig.jpg')
  fireEvent.click(screen.getByText('Get started'))
  expect(onStart).toHaveBeenCalled()
})
