import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import PrivacySection from './PrivacySection'
import { api } from '../api'
import type { TelemetryState } from '../api'

const off: TelemetryState = { available: true, enabled: false, mode: 'local', collector: null, sending: false, id_prefix: null,
  opted_in: null, pending_delete: false, last_delete_confirmed: null, last_send: null, interval_s: 60 }
const on: TelemetryState = { ...off, enabled: true, id_prefix: 'abcd1234', opted_in: '2026-11-16',
  collector: 'http://127.0.0.1:8787', sending: true, last_send: { status: 'ok', events: 3, at: '2026-11-16T10:00:00Z' } }

beforeEach(() => { vi.restoreAllMocks() })

it('is off by default and turning it on calls PUT', async () => {
  vi.spyOn(api, 'telemetry').mockResolvedValue(off)
  const put = vi.spyOn(api, 'setTelemetry').mockResolvedValue(on)
  render(<PrivacySection />)
  const sw = await screen.findByRole('switch', { name: 'Share usage stats' })
  expect(sw).toHaveAttribute('aria-checked', 'false')
  fireEvent.click(sw)
  await waitFor(() => expect(put).toHaveBeenCalledWith(true))
  expect(await screen.findByText('abcd1234…')).toBeInTheDocument()
  expect(screen.getByText(/Turning this off also deletes everything that was sent/)).toBeInTheDocument()
})

it('turning it off calls PUT false and says the data is being deleted', async () => {
  vi.spyOn(api, 'telemetry').mockResolvedValue(on)
  const put = vi.spyOn(api, 'setTelemetry').mockResolvedValue({ ...off, pending_delete: true })
  render(<PrivacySection />)
  fireEvent.click(await screen.findByRole('switch', { name: 'Share usage stats' }))
  await waitFor(() => expect(put).toHaveBeenCalledWith(false))
  expect(await screen.findByText('Turned off. What was sent is being deleted.')).toBeInTheDocument()
})

it('New ID calls POST and shows the new prefix', async () => {
  vi.spyOn(api, 'telemetry').mockResolvedValue(on)
  const post = vi.spyOn(api, 'telemetryNewId').mockResolvedValue({ ...on, id_prefix: 'ffff0000' })
  render(<PrivacySection />)
  fireEvent.click(await screen.findByText('New ID'))
  await waitFor(() => expect(post).toHaveBeenCalled())
  expect(await screen.findByText('ffff0000…')).toBeInTheDocument()
})

it('shows the collector, the last send and a confirmed delete', async () => {
  vi.spyOn(api, 'telemetry').mockResolvedValue({ ...on, last_delete_confirmed: '2026-11-15T09:00:00Z' })
  render(<PrivacySection />)
  expect(await screen.findByText(/Sending to http:\/\/127\.0\.0\.1:8787/)).toBeInTheDocument()
  expect(screen.getByText(/Delete received at/)).toBeInTheDocument()
})

it('never says the data is already gone when only the delete request was received', async () => {
  // Final review 2026-11-16, finding 3: the collector accepts the delete at once, and the hourly cleanup
  // erases the rows later. "has been deleted" was said up to an hour early.
  vi.spyOn(api, 'telemetry').mockResolvedValue({ ...on, enabled: false, id_prefix: null,
                                                 last_delete_confirmed: '2026-11-15T09:00:00Z' })
  render(<PrivacySection />)
  expect(await screen.findByText(/erased within the hour/)).toBeInTheDocument()
  expect(screen.queryByText(/has been deleted/)).toBeNull()
})

it('says it is not sending without a collector', async () => {
  vi.spyOn(api, 'telemetry').mockResolvedValue({ ...on, collector: null, sending: false })
  render(<PrivacySection />)
  expect(await screen.findByText('Not sending: this copy of Flackey has no stats server, so reports stay on this computer.')).toBeInTheDocument()
})

it("shows pending and sent events as JSON under What's sent", async () => {
  vi.spyOn(api, 'telemetry').mockResolvedValue(on)
  vi.spyOn(api, 'telemetryLog').mockResolvedValue({ pending: [{ event: 'setup_step', step: 'folder' }], sent: [] })
  const { container } = render(<PrivacySection />)
  await screen.findByText("What's sent")
  const details = container.querySelector('details') as HTMLDetailsElement
  details.open = true
  fireEvent(details, new Event('toggle'))
  await waitFor(() => expect(container.querySelector('pre.usage-log')?.textContent).toContain('"step": "folder"'))
  expect(screen.getAllByRole('button', { name: 'Copy' })).toHaveLength(2)
})

it('renders nothing when usage stats are not available', async () => {
  const get = vi.spyOn(api, 'telemetry').mockResolvedValue({ available: false, enabled: false })
  const { container } = render(<PrivacySection />)
  await waitFor(() => expect(get).toHaveBeenCalled())
  await Promise.resolve()
  expect(container).toBeEmptyDOMElement()
})

it('says plainly when a delete has not gone through and is still being retried', async () => {
  vi.spyOn(api, 'telemetry').mockResolvedValue({ ...off, pending_delete: true,
    delete_retry: { attempts: 3, error: 'HTTP 403', next_at: '2026-11-16T12:00:00+00:00' } })
  render(<PrivacySection />)
  expect(await screen.findByText(/The delete hasn't gone through yet \(HTTP 403\), so what was sent is still stored\. Flackey keeps trying, next at/))
    .toBeInTheDocument()
})

it('says when the server refused a delete for good, and points to a bug report', async () => {
  vi.spyOn(api, 'telemetry').mockResolvedValue({ ...off, delete_refused: { at: '2026-11-16T12:00:00+00:00', reason: 'not_owner' } })
  const onReport = vi.fn()
  render(<PrivacySection onReport={onReport} />)
  expect(await screen.findByRole('alert')).toHaveTextContent(/Couldn't confirm deletion: our server refused the request at .*Contact us/)
  fireEvent.click(screen.getByRole('button', { name: 'Report a bug…' }))
  expect(onReport).toHaveBeenCalled()
})

it('says the stats server never keeps IPs, and what the network in front of it keeps', async () => {
  vi.spyOn(api, 'telemetry').mockResolvedValue(off)
  render(<PrivacySection />)
  expect(await screen.findByText(/^Flackey’s stats server never stores or logs your IP address\. Like most websites, the network in front of it \(Cloudflare and our hosting firewall\) sees IP addresses to block attacks, keeps them for up to 7 days/)).toBeInTheDocument()
  expect(screen.queryByText(/never .*your IP\.$/)).not.toBeInTheDocument()
})
