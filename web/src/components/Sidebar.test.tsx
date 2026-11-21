import { render, screen, fireEvent } from '@testing-library/react'
import Sidebar from './Sidebar'
import type { LosslessHealth, ProviderHealth, SharingState } from '../api'

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

it('offers Report a bug in the footer whenever a handler is given', () => {
  const onReport = vi.fn()
  const { rerender } = render(<Sidebar tab="download" onTab={vi.fn()} telegramAuthorized lossless={lossless()} inset={false} onReport={onReport} />)
  fireEvent.click(screen.getByRole('button', { name: 'Report a bug' }))
  expect(onReport).toHaveBeenCalled()
  rerender(<Sidebar tab="download" onTab={vi.fn()} telegramAuthorized lossless={lossless()} inset={false} />)
  expect(screen.queryByRole('button', { name: 'Report a bug' })).not.toBeInTheDocument()
})

describe('the sharing port', () => {
  const sharing = (over: Partial<SharingState> = {}): SharingState =>
    ({ port: 50300, enabled: true, checking: false, mapping: null, reachable: false, public_ip: null,
      lan_ip: null, gateway: '10.0.0.1', checked_at: null, error: null, closed_confirmed: true, ...over })
  const withPort = (sh: SharingState, l = lossless({ provider: soulseekOk }), telegram = true, onTab = vi.fn()) =>
    render(<Sidebar tab="library" onTab={onTab} telegramAuthorized={telegram} lossless={l} sharing={sh} inset={false} />)

  it('turns amber with a reason, and opens Settings, once the port is confirmed closed', () => {
    const onTab = vi.fn()
    const { container } = withPort(sharing(), undefined, true, onTab)
    expect(screen.getByText('Connected · port closed')).toBeInTheDocument()
    expect(screen.getByText('Fewer Soulseek results until the port opens.')).toBeInTheDocument()
    expect(container.querySelector('.sidebar-footer .status-dot')).toHaveClass('amber')
    fireEvent.click(screen.getByRole('button', { name: /Connected · port closed/ }))
    expect(onTab).toHaveBeenCalledWith('settings')
  })

  it.each<[string, Partial<SharingState>]>([
    ['one unconfirmed closed reading', { closed_confirmed: false }],
    ['an open port', { reachable: true, closed_confirmed: false }],
    ['a check that could not tell', { reachable: null }],
    ['sharing switched off', { enabled: false }],
  ])('stays green for %s', (_, over) => {
    const { container } = withPort(sharing(over))
    expect(screen.getByText('Connected')).toBeInTheDocument()
    expect(container.querySelector('.sidebar-footer .status-dot')).not.toHaveClass('amber')
  })

  it('stays green on Telegram alone: the port only matters while Soulseek is connected', () => {
    withPort(sharing(), lossless({ provider: { name: 'soulseek', status: 'unreachable', username: null } }))
    expect(screen.getByText('Connected')).toBeInTheDocument()
  })

  it('lets red win over amber when nothing can download', () => {
    const { container } = withPort(sharing(), lossless({ enabled: false }), false)
    expect(screen.getByText('Not connected')).toBeInTheDocument()
    expect(container.querySelector('.sidebar-footer .status-dot')).toHaveClass('red')
  })
})
