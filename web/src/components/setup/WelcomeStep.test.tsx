import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import WelcomeStep from './WelcomeStep'
import { api } from '../../api'
import { resetTelemetryGate, track } from '../../telemetry'

it('shows the pitch, the website vinyl artwork, and Get started', () => {
  const onStart = vi.fn()
  const { container } = render(<WelcomeStep onStart={onStart} />)
  expect(screen.getByText('Flackey')).toBeInTheDocument()
  expect(screen.getByText(/Paste a YouTube link\. Get the best copy that exists/)).toBeInTheDocument()
  expect(container.querySelector('img.welcome-vinyl-photo')?.getAttribute('src')).toBe('/silver-vinyl.png')
  // Neither source reads as a required step: either one is enough to download.
  expect(screen.getByText('Two minutes: pick a folder, then connect Telegram, Soulseek or both.')).toBeInTheDocument()
  fireEvent.click(screen.getByText('Get started'))
  expect(onStart).toHaveBeenCalled()
})

describe('usage stats opt-in', () => {
  const state = { available: true, enabled: false, mode: 'local' as const, collector: null, sending: false, id_prefix: null,
    opted_in: null, pending_delete: false, last_delete_confirmed: null, last_send: null, interval_s: 60 }
  beforeEach(() => {
    vi.restoreAllMocks()
    resetTelemetryGate()
  })

  it('shows the card with the box unticked', async () => {
    vi.spyOn(api, 'telemetry').mockResolvedValue(state)
    render(<WelcomeStep onStart={() => {}} />)
    const box = await screen.findByRole('checkbox', { name: 'Share usage stats' })
    expect(box).not.toBeChecked()
    expect(screen.getByText('Help make Flackey better')).toBeInTheDocument()
  })

  it('turns stats on before any setup event when continuing with the box ticked', async () => {
    vi.spyOn(api, 'telemetry').mockResolvedValue(state)
    const order: string[] = []
    const put = vi.spyOn(api, 'setTelemetry').mockImplementation(async () => { order.push('put'); return { ...state, enabled: true } })
    vi.spyOn(api, 'telemetryUi').mockImplementation(async () => { order.push('ui'); return { ok: true } })
    const onStart = vi.fn(() => track('setup_step', { step: 'welcome', action: 'completed', time_on_step: '<1s' }))
    render(<WelcomeStep onStart={onStart} />)
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Share usage stats' }))
    fireEvent.click(screen.getByText('Get started'))
    expect(onStart).toHaveBeenCalled()
    expect(put).toHaveBeenCalledWith(true)
    await waitFor(() => expect(order).toEqual(['put', 'ui']))
  })

  it('does not turn stats on when the box is left unticked', async () => {
    vi.spyOn(api, 'telemetry').mockResolvedValue(state)
    const put = vi.spyOn(api, 'setTelemetry').mockResolvedValue(state)
    render(<WelcomeStep onStart={() => {}} />)
    await screen.findByRole('checkbox', { name: 'Share usage stats' })
    fireEvent.click(screen.getByText('Get started'))
    expect(put).not.toHaveBeenCalled()
  })

  it('still starts setup when turning stats on fails', async () => {
    vi.spyOn(api, 'telemetry').mockResolvedValue(state)
    vi.spyOn(api, 'setTelemetry').mockRejectedValue(new Error('down'))
    const onStart = vi.fn()
    render(<WelcomeStep onStart={onStart} />)
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Share usage stats' }))
    fireEvent.click(screen.getByText('Get started'))
    expect(onStart).toHaveBeenCalled()
  })

  it('hides the card when usage stats are not available', async () => {
    const get = vi.spyOn(api, 'telemetry').mockResolvedValue({ available: false, enabled: false })
    render(<WelcomeStep onStart={() => {}} />)
    await waitFor(() => expect(get).toHaveBeenCalled())
    await Promise.resolve()
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
    expect(screen.queryByText('Help make Flackey better')).not.toBeInTheDocument()
  })

  it('lists the catalogue of events and fields', async () => {
    vi.spyOn(api, 'telemetry').mockResolvedValue(state)
    vi.spyOn(api, 'telemetryCatalog').mockResolvedValue({ schema: 1, common: { event: {}, os: {} },
      events: { setup_step: { step: {}, action: {} } }, buckets: {} })
    render(<WelcomeStep onStart={() => {}} />)
    fireEvent.click(await screen.findByText("See exactly what's sent"))
    expect(await screen.findByText('setup_step')).toBeInTheDocument()
    expect(screen.getByText('step, action')).toBeInTheDocument()
    expect(screen.getByText('event, os')).toBeInTheDocument()
  })
})
