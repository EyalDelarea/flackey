import { render, screen, fireEvent } from '@testing-library/react'
import Sidebar from './Sidebar'
import type { LosslessHealth, ProviderHealth } from '../api'

const lossless = (over: Partial<LosslessHealth> = {}): LosslessHealth =>
  ({ enabled: true, provider: null, fpcalc: true, attempts_24h: {}, raw_mb: 0, ...over })
const soulseekOk = { name: 'soulseek', status: 'ok', username: 'dj' }
const show = (l?: LosslessHealth, telegramAuthorized = true, sourceEnabled = true, onTab = vi.fn()) =>
  render(<Sidebar tab="library" onTab={onTab} telegramAuthorized={telegramAuthorized} sourceEnabled={sourceEnabled} lossless={l} inset={false} />)

it.each([
  [true, true],
  [true, false],
  [false, true],
])('shows Connected when Telegram=%s and Soulseek=%s are connected', (telegram, soulseek) => {
  const { container } = show(lossless({ provider: soulseek ? soulseekOk : null }), telegram)
  expect(screen.getByText('Connected')).toBeInTheDocument()
  expect(container.querySelector('.sidebar-footer .status-dot')).not.toHaveClass('red')
  expect(screen.queryByRole('button', { name: 'Connected' })).not.toBeInTheDocument()
})

it('Soulseek alone keeps the footer green even when Telegram is signed out or off', () => {
  const { container } = show(lossless({ provider: soulseekOk }), false, false)
  expect(screen.getByText('Connected')).toBeInTheDocument()
  expect(container.querySelector('.sidebar-footer .status-dot')).not.toHaveClass('red')
})

it.each<[ProviderHealth | null, boolean]>([
  [null, true],
  [{ name: 'soulseek', status: 'not_logged_in', username: 'dj' }, true],
  [{ name: 'soulseek', status: 'unreachable', username: null }, true],
  [soulseekOk, false],
])('shows red when Telegram is disconnected and Soulseek provider=%j enabled=%s', (provider, enabled) => {
  const onTab = vi.fn()
  const { container } = show(lossless({ provider, enabled }), false, true, onTab)
  expect(screen.getByText('Not connected')).toBeInTheDocument()
  expect(container.querySelector('.sidebar-footer .status-dot')).toHaveClass('red')
  fireEvent.click(screen.getByRole('button', { name: 'Not connected' }))
  expect(onTab).toHaveBeenCalledWith('settings')
})

it('shows red when both sources are turned off', () => {
  const { container } = show(undefined, false, false)
  expect(screen.getByText('Not connected')).toBeInTheDocument()
  expect(container.querySelector('.sidebar-footer .status-dot')).toHaveClass('red')
})
