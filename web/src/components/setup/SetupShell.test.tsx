import { render, screen, fireEvent } from '@testing-library/react'
import { vi } from 'vitest'
import SetupShell from './SetupShell'

it('marks steps done, current and next', () => {
  const { container } = render(<SetupShell step={2}>body</SetupShell>)
  const steps = container.querySelectorAll('.step')
  expect(steps).toHaveLength(4)
  expect(steps[0]).toHaveClass('done')
  expect(steps[1]).toHaveClass('cur')
  expect(steps[2]).not.toHaveClass('done')
  expect(steps[2]).not.toHaveClass('cur')
  expect(screen.getByText('Soulseek')).toBeInTheDocument()
  expect(screen.getByText('Welcome to Flackey')).toBeInTheDocument()
  expect(screen.getByText('body')).toBeInTheDocument()
})

it('marks the new Soulseek step current at step 3', () => {
  const { container } = render(<SetupShell step={3}>body</SetupShell>)
  const steps = container.querySelectorAll('.step')
  expect(steps[1]).toHaveClass('done')
  expect(steps[2]).toHaveClass('cur')
  expect(steps[3]).not.toHaveClass('done')
})

it('marks a skipped source step neutrally instead of with the done checkmark', () => {
  const { container } = render(<SetupShell step={4} skipped={{ 2: true, 3: true }}>body</SetupShell>)
  const steps = container.querySelectorAll('.step')
  expect(steps[1]).toHaveClass('skipped')
  expect(steps[1]).not.toHaveClass('done')
  expect(steps[2]).toHaveClass('skipped')
  expect(steps[2]).not.toHaveClass('done')
  expect(screen.getAllByText('Skipped')).toHaveLength(2)
})

it('shows Back and the hint in the bottom bar when given', () => {
  const onBack = vi.fn()
  render(<SetupShell step={2} onBack={onBack} hint="Waiting for Telegram…">body</SetupShell>)
  fireEvent.click(screen.getByText('Back'))
  expect(onBack).toHaveBeenCalled()
  expect(screen.getByText('Waiting for Telegram…')).toBeInTheDocument()
})
