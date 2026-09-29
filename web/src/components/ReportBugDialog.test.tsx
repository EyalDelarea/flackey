import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import ReportBugDialog from './ReportBugDialog'
import { api, ApiError } from '../api'
import type { BugPreview } from '../api'

const preview: BugPreview = {
  summary: [['Flackey', '0.1.9 (Mac app)'], ['System', 'macOS 26.6 (arm64)'], ['Displays', '2 (built-in 1512×982, external 3440×1440)'], ['Was on', 'Library']],
  files: [{ name: 'report.txt', text: 'Flackey bug report' }, { name: 'flackey.log', text: 'line one\nline <phone> two\n' }],
  log_lines: 1234,
}

beforeEach(() => {
  vi.restoreAllMocks()
  vi.spyOn(api, 'bugPreview').mockResolvedValue(preview)
})

it('says in plain words what goes along, and shows the exact files on request', async () => {
  render(<ReportBugDialog screen="library" onClose={() => {}} />)
  expect(api.bugPreview).toHaveBeenCalledWith(expect.objectContaining({ screen: 'library' }))
  expect(await screen.findByText('Flackey 0.1.9 on macOS 26.6 (arm64)')).toBeInTheDocument()
  expect(screen.getByText(/you were on Library/)).toBeInTheDocument()
  expect(screen.getByText(/activity log \(1,234 lines\)/)).toBeInTheDocument()
  expect(screen.queryByText(/line <phone> two/)).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: "Show everything that's included" }))
  expect(screen.getByText(/line <phone> two/)).toBeInTheDocument()
  expect(screen.getByText('2 (built-in 1512×982, external 3440×1440)')).toBeInTheDocument()
})

it('sends only once something is written, then explains the two steps left', async () => {
  vi.spyOn(api, 'sendBugReport').mockResolvedValue({
    file: 'flackey-bug-report-1.zip', to: 'dev@example.com', subject: 'Flackey bug: x', body: 'x' })
  vi.spyOn(api, 'revealBugReport').mockResolvedValue({ ok: true })
  render(<ReportBugDialog screen="download" onClose={() => {}} />)
  const go = screen.getByRole('button', { name: 'Email with Gmail' })
  expect(go).toBeDisabled()
  fireEvent.change(screen.getByLabelText('What went wrong?'), { target: { value: 'The QR code never shows' } })
  fireEvent.change(screen.getByLabelText(/What were you doing/), { target: { value: 'Setting up' } })
  fireEvent.click(go)
  await waitFor(() => expect(api.sendBugReport).toHaveBeenCalledWith(expect.objectContaining(
    { description: 'The QR code never shows', steps: 'Setting up', screen: 'download', via: 'gmail' })))
  expect(await screen.findByRole('heading', { name: 'Almost done' })).toBeInTheDocument()
  expect(screen.getByText('flackey-bug-report-1.zip')).toBeInTheDocument()
  expect(screen.getByText('dev@example.com')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Copy address' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Copy message' })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Show the file again' }))
  expect(api.revealBugReport).toHaveBeenCalled()
})

it('keeps what was typed and says so when the report could not be prepared', async () => {
  vi.spyOn(api, 'sendBugReport').mockRejectedValue(new ApiError(500, 'Could not save the report file. Try again.'))
  render(<ReportBugDialog screen="settings" onClose={() => {}} />)
  fireEvent.change(screen.getByLabelText('What went wrong?'), { target: { value: 'Broken' } })
  fireEvent.click(screen.getByRole('button', { name: 'Other email app' }))
  expect(await screen.findByText('Could not save the report file. Try again.')).toBeInTheDocument()
  expect(screen.getByLabelText('What went wrong?')).toHaveValue('Broken')
  expect(api.sendBugReport).toHaveBeenCalledWith(expect.objectContaining({ via: 'mail' }))
})

it('still lets the owner send when the details could not be gathered', async () => {
  vi.spyOn(api, 'bugPreview').mockRejectedValue(new Error('offline'))
  render(<ReportBugDialog screen="settings" onClose={() => {}} />)
  expect(await screen.findByText(/Couldn't gather the details/)).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('What went wrong?'), { target: { value: 'x' } })
  expect(screen.getByRole('button', { name: 'Email with Gmail' })).toBeEnabled()
})

it('keeps typed words on Escape; only the close button throws them away', () => {
  const onClose = vi.fn()
  render(<ReportBugDialog screen="download" onClose={onClose} />)
  fireEvent.change(screen.getByLabelText('What went wrong?'), { target: { value: 'half a sentence' } })
  fireEvent.keyDown(window, { key: 'Escape' })
  expect(onClose).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Close' }))
  expect(onClose).toHaveBeenCalledTimes(1)
})

it('closes on Escape and on the close button', () => {
  const onClose = vi.fn()
  render(<ReportBugDialog screen="download" onClose={onClose} />)
  fireEvent.keyDown(window, { key: 'Escape' })
  fireEvent.click(screen.getByRole('button', { name: 'Close' }))
  expect(onClose).toHaveBeenCalledTimes(2)
})
