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

  it('asks with nothing chosen: both answers unraised and stats off', async () => {
    vi.spyOn(api, 'telemetry').mockResolvedValue(state)
    render(<WelcomeStep onStart={() => {}} />)
    const share = await screen.findByRole('radio', { name: 'Share usage stats' })
    const notNow = screen.getByRole('radio', { name: 'Not now' })
    expect(share).toHaveAttribute('aria-checked', 'false')
    expect(notNow).toHaveAttribute('aria-checked', 'false')
    expect(share.className).toBe(notNow.className)      // equal weight: neither is styled as the default
    expect(screen.getByText('Hey! Eyal here 👋')).toBeInTheDocument()
    expect(screen.getByText('I’m adding usage stats to Flackey.')).toBeInTheDocument()
    expect(screen.getByText(/where downloads fail, so I can fix them faster/)).toBeInTheDocument()
    expect(screen.getByText(/no song names, links or files, just counts like “a Soulseek search found nothing”/)).toBeInTheDocument()
    expect(screen.getByText('My stats server never stores your IP address.')).toBeInTheDocument()
    expect(screen.getByText(/If you’re OK with it, please opt in\./)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: "What's shared?" })).toBeInTheDocument()
    const privacy = screen.getByRole('link', { name: 'Privacy page' })
    expect(privacy).toHaveAttribute('href', 'https://eyaldelarea.github.io/flackey/privacy.html')
    expect(privacy).toHaveAttribute('target', '_blank')
    expect(screen.getByText('You can change this any time in Settings › Privacy.')).toBeInTheDocument()
  })

  it('turns stats on before any setup event when continuing with Share chosen', async () => {
    vi.spyOn(api, 'telemetry').mockResolvedValue(state)
    const order: string[] = []
    const put = vi.spyOn(api, 'setTelemetry').mockImplementation(async () => { order.push('put'); return { ...state, enabled: true } })
    const asked = vi.spyOn(api, 'telemetryAsked').mockResolvedValue({ ...state, asked: true })
    vi.spyOn(api, 'telemetryUi').mockImplementation(async () => { order.push('ui'); return { ok: true } })
    const onStart = vi.fn(() => track('setup_step', { step: 'welcome', action: 'completed', time_on_step: '<1s' }))
    render(<WelcomeStep onStart={onStart} />)
    fireEvent.click(await screen.findByRole('radio', { name: 'Share usage stats' }))
    expect(put).not.toHaveBeenCalled()       // applied at Get started, so changing your mind costs nothing
    fireEvent.click(screen.getByText('Get started'))
    expect(onStart).toHaveBeenCalled()
    expect(put).toHaveBeenCalledWith(true)
    expect(asked).not.toHaveBeenCalled()     // the PUT itself records the answer
    await waitFor(() => expect(order).toEqual(['put', 'ui']))
  })

  it('keeps stats off and records the answer when Not now is chosen', async () => {
    vi.spyOn(api, 'telemetry').mockResolvedValue(state)
    const put = vi.spyOn(api, 'setTelemetry').mockResolvedValue(state)
    const asked = vi.spyOn(api, 'telemetryAsked').mockResolvedValue({ ...state, asked: true })
    render(<WelcomeStep onStart={() => {}} />)
    fireEvent.click(await screen.findByRole('radio', { name: 'Share usage stats' }))
    fireEvent.click(screen.getByRole('radio', { name: 'Not now' }))
    expect(screen.getByRole('radio', { name: 'Not now' })).toHaveAttribute('aria-checked', 'true')
    fireEvent.click(screen.getByText('Get started'))
    expect(put).not.toHaveBeenCalled()
    expect(asked).toHaveBeenCalledTimes(1)
  })

  it('skipping the question leaves stats off, and it is not asked again', async () => {
    vi.spyOn(api, 'telemetry').mockResolvedValue(state)
    const put = vi.spyOn(api, 'setTelemetry').mockResolvedValue(state)
    const asked = vi.spyOn(api, 'telemetryAsked').mockResolvedValue({ ...state, asked: true })
    const onStart = vi.fn()
    render(<WelcomeStep onStart={onStart} />)
    await screen.findByRole('radio', { name: 'Share usage stats' })
    fireEvent.click(screen.getByText('Get started'))
    expect(onStart).toHaveBeenCalled()
    expect(put).not.toHaveBeenCalled()
    expect(asked).toHaveBeenCalledTimes(1)
  })

  it('quitting or reloading before Get started records nothing: not asked, not enabled, nothing sent', async () => {
    vi.spyOn(api, 'telemetry').mockResolvedValue(state)
    const put = vi.spyOn(api, 'setTelemetry').mockResolvedValue({ ...state, enabled: true })
    const asked = vi.spyOn(api, 'telemetryAsked').mockResolvedValue({ ...state, asked: true })
    const ui = vi.spyOn(api, 'telemetryUi').mockResolvedValue({ ok: true })
    const onStart = vi.fn()
    const { unmount } = render(<WelcomeStep onStart={onStart} />)
    fireEvent.click(await screen.findByRole('radio', { name: 'Share usage stats' }))   // chose, then quit
    unmount()
    render(<WelcomeStep onStart={onStart} />)                                          // the reload
    expect(await screen.findByRole('radio', { name: 'Share usage stats' })).toHaveAttribute('aria-checked', 'false')
    expect(screen.getByRole('radio', { name: 'Not now' })).toHaveAttribute('aria-checked', 'false')
    expect(onStart).not.toHaveBeenCalled()
    expect(put).not.toHaveBeenCalled()       // nothing enabled
    expect(asked).not.toHaveBeenCalled()     // telemetry_asked stays false, so the question is asked again
    expect(ui).not.toHaveBeenCalled()        // nothing sent
  })

  it('still starts setup when turning stats on fails', async () => {
    vi.spyOn(api, 'telemetry').mockResolvedValue(state)
    vi.spyOn(api, 'setTelemetry').mockRejectedValue(new Error('down'))
    const onStart = vi.fn()
    render(<WelcomeStep onStart={onStart} />)
    fireEvent.click(await screen.findByRole('radio', { name: 'Share usage stats' }))
    fireEvent.click(screen.getByText('Get started'))
    expect(onStart).toHaveBeenCalled()
  })

  it('hides the card when usage stats are not available', async () => {
    const get = vi.spyOn(api, 'telemetry').mockResolvedValue({ available: false, enabled: false })
    render(<WelcomeStep onStart={() => {}} />)
    await waitFor(() => expect(get).toHaveBeenCalled())
    await Promise.resolve()
    expect(screen.queryByRole('radio')).not.toBeInTheDocument()
    expect(screen.queryByText('Hey! Eyal here 👋')).not.toBeInTheDocument()
  })

  it('lists the catalogue of events and fields', async () => {
    vi.spyOn(api, 'telemetry').mockResolvedValue(state)
    vi.spyOn(api, 'telemetryCatalog').mockResolvedValue({ schema: 1, common: { event: {}, os: {} },
      events: { setup_step: { step: {}, action: {} } }, buckets: {} })
    render(<WelcomeStep onStart={() => {}} />)
    fireEvent.click(await screen.findByText("What's shared?"))
    expect(await screen.findByText('setup_step')).toBeInTheDocument()
    expect(screen.getByText('step, action')).toBeInTheDocument()
    expect(screen.getByText('event, os')).toBeInTheDocument()
    // The detail behind the card's IP line: honest about the network in front of the stats server
    expect(screen.getByText(/stats server never stores or logs your IP address\. .*Cloudflare and our hosting firewall.*up to 7 days.*up to 400 days.*we don’t link those logs to your stats\./)).toBeInTheDocument()
  })
})
