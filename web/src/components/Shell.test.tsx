import { render, screen, fireEvent } from '@testing-library/react'
import Shell from './Shell'

const props = { tab: 'download' as const, telegramAuthorized: true, onTab: vi.fn() }

it('reserves the title-bar strips only inside the inset native window', () => {
  const { container, rerender } = render(<Shell {...props} inset={false}>x</Shell>)
  expect(container.querySelector('.titlebar-spacer')).toBeNull()
  expect(container.querySelector('.titlebar-strip')).toBeNull()
  rerender(<Shell {...props} inset>x</Shell>)
  expect(container.querySelector('.titlebar-spacer')).not.toBeNull()
  expect(container.querySelector('.titlebar-strip')).not.toBeNull()
})

// The window is dragged by AppKit's own title bar. pywebview's JavaScript drag handler answers a drag
// on a `.pywebview-drag-region` by posting an absolute screen position back to Python, which re-adds an
// `NSScreen.mainScreen()` frame snapshotted at window creation -- so with a second display attached the
// window jumped by that screen's origin instead of following the pointer. No element may wear the class.
it('emits no pywebview drag region, inset or not', () => {
  const { container, rerender } = render(<Shell {...props} inset={false}>x</Shell>)
  expect(container.querySelector('.pywebview-drag-region')).toBeNull()
  rerender(<Shell {...props} inset>x</Shell>)
  expect(container.querySelector('.pywebview-drag-region')).toBeNull()
})

it('switches tabs and shows the overall connection state in the footer', () => {
  render(<Shell {...props} inset={false} telegramAuthorized={false}>x</Shell>)
  expect(screen.getByText('Not connected')).toBeInTheDocument()
  fireEvent.click(screen.getByText('Library'))
  expect(props.onTab).toHaveBeenCalledWith('library')
})

it('marks the active tab with aria-current for assistive tech, and only that one', () => {
  render(<Shell {...props} inset={false}>x</Shell>)
  expect(screen.getByText('Download').closest('button')).toHaveAttribute('aria-current', 'page')
  expect(screen.getByText('Library').closest('button')).not.toHaveAttribute('aria-current')
})
