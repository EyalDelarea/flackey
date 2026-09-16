import { render, screen } from '@testing-library/react'
import Banner from './Banner'

it('announces an amber banner politely and a red one assertively, both as a live status region', () => {
  const { rerender } = render(<Banner tone="amber" text="Reconnecting…" />)
  expect(screen.getByRole('status')).toHaveAttribute('aria-live', 'polite')

  rerender(<Banner tone="red" text="Server error" />)
  expect(screen.getByRole('status')).toHaveAttribute('aria-live', 'assertive')
})
