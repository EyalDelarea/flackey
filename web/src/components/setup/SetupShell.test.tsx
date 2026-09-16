import { render, screen, fireEvent } from '@testing-library/react'
import { vi } from 'vitest'
import SetupShell from './SetupShell'

it('marks steps done, current and next', () => {
  const { container } = render(<SetupShell step={2}>body</SetupShell>)
  const steps = container.querySelectorAll('.step')
  expect(steps).toHaveLength(5)
  expect(steps[0]).toHaveClass('done')
  expect(steps[1]).toHaveClass('cur')
  expect(steps[2]).not.toHaveClass('done')
  expect(steps[2]).not.toHaveClass('cur')
  expect(screen.getByText('Format')).toBeInTheDocument()
  expect(screen.getByText('Soulseek')).toBeInTheDocument()
  expect(screen.getByText('Welcome to Flackey')).toBeInTheDocument()
  expect(screen.getByText('body')).toBeInTheDocument()
})

it('marks the Soulseek step current at step 4', () => {
  const { container } = render(<SetupShell step={4}>body</SetupShell>)
  const steps = container.querySelectorAll('.step')
  expect(steps[2]).toHaveClass('done')
  expect(steps[3]).toHaveClass('cur')
  expect(steps[4]).not.toHaveClass('done')
})

it('marks a skipped source step neutrally instead of with the done checkmark', () => {
  const { container } = render(<SetupShell step={5} skipped={{ 3: true, 4: true }}>body</SetupShell>)
  const steps = container.querySelectorAll('.step')
  expect(steps[2]).toHaveClass('skipped')
  expect(steps[2]).not.toHaveClass('done')
  expect(steps[3]).toHaveClass('skipped')
  expect(steps[3]).not.toHaveClass('done')
  expect(screen.getAllByText('Skipped')).toHaveLength(2)
})

it('shows Back and the hint in the bottom bar when given', () => {
  const onBack = vi.fn()
  render(<SetupShell step={2} onBack={onBack} hint="Waiting for Telegram…">body</SetupShell>)
  fireEvent.click(screen.getByText('Back'))
  expect(onBack).toHaveBeenCalled()
  expect(screen.getByText('Waiting for Telegram…')).toBeInTheDocument()
})
