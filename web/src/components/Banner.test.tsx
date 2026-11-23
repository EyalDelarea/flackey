import { fireEvent, render, screen } from '@testing-library/react'
import Banner from './Banner'

it('announces an amber banner politely and a red one assertively, both as a live status region', () => {
  const { rerender } = render(<Banner tone="amber" text="Reconnecting…" />)
  expect(screen.getByRole('status')).toHaveAttribute('aria-live', 'polite')

  rerender(<Banner tone="red" text="Server error" />)
  expect(screen.getByRole('status')).toHaveAttribute('aria-live', 'assertive')
})

it('draws the action as a plain button and dismissing as the quiet "Dismiss" word at the right edge', () => {
  const onDismiss = vi.fn()
  render(<Banner tone="amber" text="Removed 354 missing files from the library." action={{ label: 'Forget them', onClick: () => {} }} onDismiss={onDismiss} />)
  expect(screen.getByRole('button', { name: 'Forget them' })).toHaveClass('btn-secondary')
  expect(screen.getByRole('button', { name: 'Forget them' })).not.toHaveClass('btn-primary')
  const dismiss = screen.getByRole('button', { name: 'Dismiss' })
  expect(dismiss).toHaveClass('btn-link', 'muted')
  fireEvent.click(dismiss)
  expect(onDismiss).toHaveBeenCalled()
})
