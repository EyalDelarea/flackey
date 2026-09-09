import { act, render, screen, fireEvent, waitFor } from '@testing-library/react'
import SoulseekStep from './SoulseekStep'
import { api } from '../../api'
import type { SlskdProgress, SoulseekConnect } from '../../api'

const POLL_MS = 1000   // must match SoulseekStep's own interval

beforeEach(() => {
  vi.clearAllMocks()
  vi.spyOn(api, 'soulseekSetup').mockResolvedValue({ configured: false, username: null })
  // Already installed by default: the tests below that care about the download say so themselves, and
  // every other test would otherwise start a real one.
  vi.spyOn(api, 'slskdSetup').mockResolvedValue({ installed: true, running: false, version: '0.26.0' })
  // No live link by default, so Save behaves as it did before this build gained one; the tests that
  // exercise the sign-in turn `connecting` on themselves.
  vi.spyOn(api, 'soulseekConnectStatus').mockResolvedValue(connect())
})

const connect = (over: Partial<SoulseekConnect> = {}): SoulseekConnect =>
  ({ state: 'idle', username: null, error: null, ...over })

afterEach(() => { vi.useRealTimers() })

const progress = (over: Partial<SlskdProgress> = {}): SlskdProgress =>
  ({ state: 'downloading', done: 0, total: 0, error: null, ...over })

it('disables Save and continue until both fields are filled', async () => {
  render(<SoulseekStep onDone={vi.fn()} onSkip={vi.fn()} />)
  await waitFor(() => expect(api.soulseekSetup).toHaveBeenCalled())
  const username = screen.getByLabelText('Soulseek username')
  const password = screen.getByLabelText('Soulseek password')
  expect(password).toHaveAttribute('type', 'password')
  const save = screen.getByText('Create account')
  expect(save).toBeDisabled()
  fireEvent.change(username, { target: { value: 'digger' } })
  expect(save).toBeDisabled()
  fireEvent.change(password, { target: { value: 'not-a-real-password' } })
  expect(save).not.toBeDisabled()
})

it('saves and calls onDone with the typed values', async () => {
  const saveSoulseek = vi.spyOn(api, 'saveSoulseek').mockResolvedValue({ ok: true, restart_required: true, connecting: false })
  const onDone = vi.fn()
  render(<SoulseekStep onDone={onDone} onSkip={vi.fn()} />)
  await waitFor(() => expect(api.soulseekSetup).toHaveBeenCalled())
  fireEvent.change(screen.getByLabelText('Soulseek username'), { target: { value: 'digger' } })
  fireEvent.change(screen.getByLabelText('Soulseek password'), { target: { value: 'not-a-real-password' } })
  fireEvent.click(screen.getByText('Create account'))
  await waitFor(() => expect(onDone).toHaveBeenCalled())
  expect(saveSoulseek).toHaveBeenCalledWith('digger', 'not-a-real-password')
})

it('Skip for now calls onSkip without saving', async () => {
  const saveSoulseek = vi.spyOn(api, 'saveSoulseek')
  const onSkip = vi.fn()
  render(<SoulseekStep onDone={vi.fn()} onSkip={onSkip} />)
  await waitFor(() => expect(api.soulseekSetup).toHaveBeenCalled())
  fireEvent.click(screen.getByText('Skip for now'))
  expect(onSkip).toHaveBeenCalled()
  expect(saveSoulseek).not.toHaveBeenCalled()
})

it('shows the error and stays on the step when the save is rejected', async () => {
  const { ApiError } = await import('../../api')
  vi.spyOn(api, 'saveSoulseek').mockRejectedValue(new ApiError(400, 'That username is taken'))
  const onDone = vi.fn()
  render(<SoulseekStep onDone={onDone} onSkip={vi.fn()} />)
  await waitFor(() => expect(api.soulseekSetup).toHaveBeenCalled())
  fireEvent.change(screen.getByLabelText('Soulseek username'), { target: { value: 'digger' } })
  fireEvent.change(screen.getByLabelText('Soulseek password'), { target: { value: 'not-a-real-password' } })
  fireEvent.click(screen.getByText('Create account'))
  await waitFor(() => expect(screen.getByText('That username is taken')).toBeInTheDocument())
  expect(onDone).not.toHaveBeenCalled()
})

it('prefills the username when an account is already configured', async () => {
  vi.spyOn(api, 'soulseekSetup').mockResolvedValue({ configured: true, username: 'digger' })
  render(<SoulseekStep onDone={vi.fn()} onSkip={vi.fn()} />)
  await waitFor(() => expect(screen.getByLabelText('Soulseek username')).toHaveValue('digger'))
  expect(screen.getByText('A Soulseek account is already saved.')).toBeInTheDocument()
})

it('renders no error when the mount check is rejected', async () => {
  const spy = vi.spyOn(api, 'soulseekSetup').mockRejectedValue(new Error('network down'))
  const { container } = render(<SoulseekStep onDone={vi.fn()} onSkip={vi.fn()} />)
  await waitFor(() => expect(spy).toHaveBeenCalled())
  expect(container.querySelector('.err')).toBeNull()
  expect(screen.getByLabelText('Soulseek username')).toHaveValue('')
})

describe('fetching the sidecar while the user types', () => {
  it('starts the install when nothing is installed and reports progress without naming slskd', async () => {
    vi.spyOn(api, 'slskdSetup').mockResolvedValue({ installed: false, running: false, version: '0.26.0' })
    const install = vi.spyOn(api, 'installSlskd').mockResolvedValue({ state: 'downloading' })
    vi.spyOn(api, 'slskdProgress').mockResolvedValue(progress({ done: 40, total: 100 }))
    const { container } = render(<SoulseekStep onDone={vi.fn()} onSkip={vi.fn()} />)
    await waitFor(() => expect(install).toHaveBeenCalled())
    await waitFor(() => expect(screen.getByText('Getting things ready… 40%')).toBeInTheDocument())
    // "They never see slskd": not the name, not the version, anywhere on the step.
    expect(container.textContent).not.toMatch(/slskd/i)
    expect(container.textContent).not.toContain('0.26.0')
  })

  it('does not install when the binary is already there', async () => {
    const install = vi.spyOn(api, 'installSlskd')
    render(<SoulseekStep onDone={vi.fn()} onSkip={vi.fn()} />)
    await waitFor(() => expect(api.slskdSetup).toHaveBeenCalled())
    expect(install).not.toHaveBeenCalled()
    expect(screen.queryByText(/Getting things ready/)).not.toBeInTheDocument()
  })

  // Fake timers go up BEFORE render: an interval created against the real clock is not one
  // advanceTimersByTime can fire, so installing them afterwards makes these assertions vacuous.
  // Both were checked by mutation -- dropping either stopPolling() call fails one of them.
  const renderWithFakeTimers = () => {
    vi.useFakeTimers()
    vi.spyOn(api, 'slskdSetup').mockResolvedValue({ installed: false, running: false, version: '0.26.0' })
    vi.spyOn(api, 'installSlskd').mockResolvedValue({ state: 'downloading' })
    return render(<SoulseekStep onDone={vi.fn()} onSkip={vi.fn()} />)
  }
  const settle = (ms = 0) => act(async () => { await vi.advanceTimersByTimeAsync(ms) })

  it('stops polling once the install reports done', async () => {
    const prog = vi.spyOn(api, 'slskdProgress').mockResolvedValue(progress({ state: 'done', done: 1, total: 1 }))
    renderWithFakeTimers()
    await settle()
    expect(prog).toHaveBeenCalledTimes(1)
    await settle(10 * POLL_MS)
    expect(prog).toHaveBeenCalledTimes(1)   // a live interval would have polled ten more times
  })

  it('keeps polling while the download is still running, and stops on unmount', async () => {
    const prog = vi.spyOn(api, 'slskdProgress').mockResolvedValue(progress({ done: 10, total: 100 }))
    const { unmount } = renderWithFakeTimers()
    await settle()
    expect(prog).toHaveBeenCalledTimes(1)
    await settle(3 * POLL_MS)
    expect(prog).toHaveBeenCalledTimes(4)   // the interval is genuinely running
    unmount()
    await settle(10 * POLL_MS)
    expect(prog).toHaveBeenCalledTimes(4)   // ...and the unmount cleanup stopped it
  })

  it('a failed install does not block saving, and Try again restarts it', async () => {
    vi.spyOn(api, 'slskdSetup').mockResolvedValue({ installed: false, running: false, version: '0.26.0' })
    const install = vi.spyOn(api, 'installSlskd').mockRejectedValueOnce(new Error('offline'))
    vi.spyOn(api, 'slskdProgress').mockResolvedValue(progress({ done: 5, total: 100 }))
    render(<SoulseekStep onDone={vi.fn()} onSkip={vi.fn()} />)
    await waitFor(() => expect(screen.getByText(/Couldn't finish getting Soulseek ready/)).toBeInTheDocument())
    fireEvent.change(screen.getByLabelText('Soulseek username'), { target: { value: 'digger' } })
    fireEvent.change(screen.getByLabelText('Soulseek password'), { target: { value: 'not-a-real-password' } })
    expect(screen.getByText('Create account')).not.toBeDisabled()
    install.mockResolvedValueOnce({ state: 'downloading' })
    fireEvent.click(screen.getByText('Try again'))
    await waitFor(() => expect(install).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(screen.getByText('Getting things ready… 5%')).toBeInTheDocument())
  })

  it('reports a server-side install failure the same way', async () => {
    vi.spyOn(api, 'slskdSetup').mockResolvedValue({ installed: false, running: false, version: '0.26.0' })
    vi.spyOn(api, 'installSlskd').mockResolvedValue({ state: 'downloading' })
    vi.spyOn(api, 'slskdProgress').mockResolvedValue(progress({ state: 'error', error: 'sha256 mismatch' }))
    render(<SoulseekStep onDone={vi.fn()} onSkip={vi.fn()} />)
    await waitFor(() => expect(screen.getByText(/Couldn't finish getting Soulseek ready/)).toBeInTheDocument())
  })
})

describe('signing in, which on Soulseek is also how the account gets created', () => {
  // Fake timers must be installed before render: an interval created against the real clock cannot be
  // fired by advanceTimersByTime, and every assertion below would pass whether or not the polling works.
  const renderWithFakeTimers = () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    return render(<SoulseekStep onDone={vi.fn()} onSkip={vi.fn()} />)
  }
  const settle = async (ms: number) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms) }) }

  const fillAndSave = async () => {
    fireEvent.change(screen.getByLabelText('Soulseek username'), { target: { value: 'digger' } })
    fireEvent.change(screen.getByLabelText('Soulseek password'), { target: { value: 'not-a-real-password' } })
    fireEvent.click(screen.getByText('Create account'))
    await settle(0)
  }

  it('polls until the server confirms, then offers Continue rather than auto-advancing', async () => {
    vi.spyOn(api, 'saveSoulseek').mockResolvedValue({ ok: true, restart_required: false, connecting: true })
    vi.spyOn(api, 'soulseekConnectStatus')
      .mockResolvedValueOnce(connect({ state: 'connecting' }))
      .mockResolvedValue(connect({ state: 'connected', username: 'digger' }))
    renderWithFakeTimers()
    await settle(0)
    await fillAndSave()
    expect(screen.getByText('Signing in to Soulseek…')).toBeInTheDocument()
    await settle(POLL_MS)
    expect(screen.getByText('Signed in as digger. The account is yours.')).toBeInTheDocument()
    expect(screen.getByText('Continue')).not.toBeDisabled()
  })

  it('stops polling once the answer arrives', async () => {
    vi.spyOn(api, 'saveSoulseek').mockResolvedValue({ ok: true, restart_required: false, connecting: true })
    const status = vi.spyOn(api, 'soulseekConnectStatus')
      .mockResolvedValue(connect({ state: 'connected', username: 'digger' }))
    renderWithFakeTimers()
    await settle(0)
    await fillAndSave()
    const after = status.mock.calls.length
    await settle(POLL_MS * 5)
    expect(status.mock.calls.length).toBe(after)
  })

  it('shows the server\'s own reason when the sign-in fails, and lets the user try another name', async () => {
    vi.spyOn(api, 'saveSoulseek').mockResolvedValue({ ok: true, restart_required: false, connecting: true })
    vi.spyOn(api, 'soulseekConnectStatus')
      .mockResolvedValue(connect({ state: 'failed', error: 'Somebody already uses that name.' }))
    renderWithFakeTimers()
    await settle(0)
    await fillAndSave()
    await settle(POLL_MS)
    expect(screen.getByText('Somebody already uses that name.')).toBeInTheDocument()
    expect(screen.getByLabelText('Soulseek username')).not.toBeDisabled()
  })

  it('does not leave polling running after the step unmounts', async () => {
    vi.spyOn(api, 'saveSoulseek').mockResolvedValue({ ok: true, restart_required: false, connecting: true })
    const status = vi.spyOn(api, 'soulseekConnectStatus').mockResolvedValue(connect({ state: 'connecting' }))
    const { unmount } = renderWithFakeTimers()
    await settle(0)
    await fillAndSave()
    const after = status.mock.calls.length
    unmount()
    await settle(POLL_MS * 5)
    expect(status.mock.calls.length).toBe(after)
  })
})
