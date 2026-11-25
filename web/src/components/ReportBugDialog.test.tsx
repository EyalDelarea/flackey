import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import ReportBugDialog from './ReportBugDialog'
import { api, ApiError } from '../api'
import type { BugPreview } from '../api'
import { PlatformContext } from '../platform'

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

it('reports one failed track: asks for nothing, says what it found, and sends it by request', async () => {
  vi.spyOn(api, 'bugPreview').mockResolvedValue({ ...preview, log_lines: 12,
    diagnosis: 'Soulseek: 4 copies found, 3 tried, the transfers failed',
    files: [{ name: 'report.txt', text: 'Flackey failure report' }, { name: 'request-42.log', text: 'req#42 failed' }] })
  vi.spyOn(api, 'sendBugReport').mockResolvedValue({ file: 'flackey-bug-report-1.zip', to: 'dev@example.com', subject: 'x', body: 'x' })
  render(<ReportBugDialog screen="download" requestId={42} onClose={() => {}} />)
  expect(screen.getByRole('heading', { name: 'Report this failure' })).toBeInTheDocument()
  expect(api.bugPreview).toHaveBeenCalledWith(expect.objectContaining({ screen: 'download', request_id: 42 }))
  expect(await screen.findByText(/What happened to this track: Soulseek: 4 copies found, 3 tried, the transfers failed/)).toBeInTheDocument()
  expect(screen.getByText(/log lines about this track \(12 lines\)/)).toBeInTheDocument()
  expect(screen.queryByLabelText(/What were you doing/)).not.toBeInTheDocument()
  const go = screen.getByRole('button', { name: 'Email with Gmail' })
  expect(go).toBeEnabled()
  fireEvent.click(go)
  await waitFor(() => expect(api.sendBugReport).toHaveBeenCalledWith(expect.objectContaining(
    { request_id: 42, description: '', via: 'gmail' })))
  expect(await screen.findByRole('heading', { name: 'Almost done' })).toBeInTheDocument()
})

it('sends no request with a general report', () => {
  render(<ReportBugDialog screen="library" onClose={() => {}} />)
  expect(screen.getByRole('heading', { name: 'Report a problem' })).toBeInTheDocument()
  expect(vi.mocked(api.bugPreview).mock.calls[0][0]).not.toHaveProperty('request_id')
})

it('speaks Windows on Windows: File Explorer, no Mac, and the version loses its "(Windows app)"', async () => {
  vi.spyOn(api, 'bugPreview').mockResolvedValue({ ...preview, summary: [['Flackey', '0.1.9 (Windows app)'], ['System', 'Windows 11 (AMD64)']] })
  vi.spyOn(api, 'sendBugReport').mockResolvedValue({ file: 'flackey-bug-report-1.zip', to: 'dev@example.com', subject: 'x', body: 'x' })
  render(<PlatformContext.Provider value="windows"><ReportBugDialog screen="download" onClose={() => {}} /></PlatformContext.Provider>)
  expect(await screen.findByText('Flackey 0.1.9 on Windows 11 (AMD64)')).toBeInTheDocument()
  expect(screen.getByText(/passwords and user name are taken out/)).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('What went wrong?'), { target: { value: 'Broken' } })
  fireEvent.click(screen.getByRole('button', { name: 'Email with Gmail' }))
  expect(await screen.findByText(/from the File Explorer window into the email/)).toBeInTheDocument()
})

describe('straight to the Flackey server (issue #279)', () => {
  beforeEach(() => { vi.spyOn(api, 'bugPreview').mockResolvedValue({ ...preview, server: true }) })

  it('sends with one press and says it arrived, without opening any email', async () => {
    vi.spyOn(api, 'uploadBugReport').mockResolvedValue({ sent: true, file: 'flackey-bug-report-1.zip', reference: '0acbd8bf' })
    const email = vi.spyOn(api, 'sendBugReport')
    render(<ReportBugDialog screen="download" onClose={() => {}} />)
    const go = await screen.findByRole('button', { name: 'Send report' })
    expect(go).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'Email with Gmail' })).not.toBeInTheDocument()
    expect(screen.getByText(/kept for 90 days, then deleted/)).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('What went wrong?'), { target: { value: 'The QR code never shows' } })
    fireEvent.click(go)
    await waitFor(() => expect(api.uploadBugReport).toHaveBeenCalledWith(expect.objectContaining(
      { description: 'The QR code never shows', screen: 'download' })))
    expect(await screen.findByRole('heading', { name: 'Report sent' })).toBeInTheDocument()
    expect(screen.getByText('0acbd8bf')).toBeInTheDocument()
    expect(email).not.toHaveBeenCalled()
  })

  it('offers email, keeping the words, when the server did not take it', async () => {
    vi.spyOn(api, 'uploadBugReport').mockResolvedValue({ sent: false, file: 'flackey-bug-report-1.zip', error: "Couldn't reach the Flackey server" })
    vi.spyOn(api, 'sendBugReport').mockResolvedValue({ file: 'flackey-bug-report-1.zip', to: 'dev@example.com', subject: 's', body: 'b' })
    render(<ReportBugDialog screen="library" onClose={() => {}} />)
    fireEvent.change(screen.getByLabelText('What went wrong?'), { target: { value: 'Broken' } })
    fireEvent.click(await screen.findByRole('button', { name: 'Send report' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/Couldn't reach the Flackey server.*saved on this Mac.*by email/)
    expect(screen.getByLabelText('What went wrong?')).toHaveValue('Broken')
    expect(screen.queryByRole('button', { name: 'Send report' })).not.toBeInTheDocument()
    expect(api.uploadBugReport).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByRole('button', { name: 'Email with Gmail' }))
    await waitFor(() => expect(api.sendBugReport).toHaveBeenCalledWith(expect.objectContaining({ description: 'Broken', via: 'gmail' })))
  })

  it('lets the reporter pick email instead before sending', async () => {
    const upload = vi.spyOn(api, 'uploadBugReport')
    render(<ReportBugDialog screen="library" onClose={() => {}} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Use email instead' }))
    expect(screen.getByRole('button', { name: 'Email with Gmail' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Other email app' })).toBeInTheDocument()
    expect(upload).not.toHaveBeenCalled()
  })

  it('says so and keeps the words when the report could not even be saved', async () => {
    vi.spyOn(api, 'uploadBugReport').mockRejectedValue(new ApiError(500, 'Could not save the report file. Try again.'))
    render(<ReportBugDialog screen="settings" onClose={() => {}} />)
    fireEvent.change(screen.getByLabelText('What went wrong?'), { target: { value: 'Broken' } })
    fireEvent.click(await screen.findByRole('button', { name: 'Send report' }))
    expect(await screen.findByText('Could not save the report file. Try again.')).toBeInTheDocument()
    expect(screen.getByLabelText('What went wrong?')).toHaveValue('Broken')
  })
})
