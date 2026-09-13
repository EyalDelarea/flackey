import { render, screen, fireEvent } from '@testing-library/react'
import Sidebar from './Sidebar'
import type { LosslessHealth } from '../api'

const lossless = (over: Partial<LosslessHealth> = {}): LosslessHealth =>
  ({ enabled: true, provider: null, fpcalc: true, attempts_24h: {}, raw_mb: 0, ...over })

const show = (l?: LosslessHealth, telegramAuthorized = true, onTab: (t: 'settings') => void = () => undefined) =>
  render(<Sidebar tab="library" onTab={onTab as never} telegramAuthorized={telegramAuthorized} lossless={l} inset={false} />)

it('says nothing about Soulseek when no account is saved', () => {
  show(lossless({ enabled: false }))
  expect(screen.queryByText(/Soulseek/)).not.toBeInTheDocument()
})

describe('the footer collapses while everything works', () => {
  const ok = { name: 'soulseek', status: 'ok', username: 'dj' }

  it('shows one line, not a list, when nothing is wrong', () => {
    show(lossless({ provider: ok }))
    expect(screen.getByText('Connected')).toBeInTheDocument()
    expect(screen.queryByText('Telegram connected')).not.toBeInTheDocument()
    expect(screen.queryByText('Soulseek connected')).not.toBeInTheDocument()
  })

  it('is not a button while healthy — there is nothing to go and fix', () => {
    show(lossless({ provider: ok }))
    expect(screen.queryByRole('button', { name: /Connected/ })).not.toBeInTheDocument()
  })

  it('names only what is actually wrong', () => {
    show(lossless({ provider: ok }), false)
    expect(screen.getByText('Telegram signed out')).toBeInTheDocument()
    expect(screen.queryByText(/Soulseek/)).not.toBeInTheDocument()   // it is fine; do not mention it
  })

  it('names both when both are unhappy', () => {
    show(lossless({ provider: { name: 'soulseek', status: 'unreachable', username: null } }), false)
    expect(screen.getByText('Telegram signed out')).toBeInTheDocument()
    expect(screen.getByText('Soulseek unreachable')).toBeInTheDocument()
  })

  it('takes you to Settings, where the detail is', () => {
    const onTab = vi.fn()
    show(lossless({ provider: ok }), false, onTab)
    fireEvent.click(screen.getByText('Telegram signed out'))
    expect(onTab).toHaveBeenCalledWith('settings')
  })
})

it('says "starting", not "connected", before the first health probe lands', () => {
  show(lossless({ provider: null }))
  // The cold-start case: credentials exist, but nothing has answered yet. Claiming a connection here
  // would be the dot lying on exactly the screen someone watches while the app boots.
  expect(screen.getByText('Soulseek starting')).toBeInTheDocument()
})

it.each([
  ['not_logged_in', 'Soulseek signing in'],
  ['unreachable', 'Soulseek unreachable'],
])('reports %s as "%s"', (status, text) => {
  show(lossless({ provider: { name: 'soulseek', status, username: 'dj' } }))
  expect(screen.getByText(text)).toBeInTheDocument()
})

it('reports a healthy Soulseek by saying nothing beyond "Connected"', () => {
  show(lossless({ provider: { name: 'soulseek', status: 'ok', username: 'dj' } }))
  expect(screen.getByText('Connected')).toBeInTheDocument()
})

it('collapses to Connected when Telegram is fine and Soulseek is not set up', () => {
  show(undefined)
  expect(screen.getByText('Connected')).toBeInTheDocument()
})

it('says Telegram is off, not signed out, when the source is switched off', () => {
  render(<Sidebar tab="download" onTab={() => {}} telegramAuthorized={false} sourceEnabled={false} inset={false} />)
  expect(screen.getByText('Telegram off')).toBeInTheDocument()
  expect(screen.queryByText('Telegram signed out')).not.toBeInTheDocument()
})
