import { render, screen, fireEvent } from '@testing-library/react'
import Shell from './Shell'

const props = { tab: 'download' as const, telegramAuthorized: true, onTab: vi.fn() }

it('reserves the title-bar drag strip only inside the inset native window', () => {
  const { container, rerender } = render(<Shell {...props} inset={false}>x</Shell>)
  expect(container.querySelector('.pywebview-drag-region')).toBeNull()
  rerender(<Shell {...props} inset>x</Shell>)
  expect(container.querySelector('.pywebview-drag-region')).not.toBeNull()
})

it('renders the 28px title-bar spacer as a real drag region, only when inset', () => {
  const { container, rerender } = render(<Shell {...props} inset={false}>x</Shell>)
  expect(container.querySelector('.titlebar-spacer')).toBeNull()
  rerender(<Shell {...props} inset>x</Shell>)
  const spacer = container.querySelector('.titlebar-spacer')
  expect(spacer).not.toBeNull()
  expect(spacer).toHaveClass('pywebview-drag-region')
})

it('switches tabs and shows the Telegram state in the footer', () => {
  render(<Shell {...props} inset={false} telegramAuthorized={false}>x</Shell>)
  expect(screen.getByText('Telegram signed out')).toBeInTheDocument()
  fireEvent.click(screen.getByText('Library'))
  expect(props.onTab).toHaveBeenCalledWith('library')
})
