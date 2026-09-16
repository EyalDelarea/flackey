import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import TelegramStep from './TelegramStep'
import { api, ApiError } from '../../api'

vi.mock('qrcode', () => ({ default: { toDataURL: vi.fn(async () => 'data:image/png;base64,AAA') } }))
beforeEach(() => { vi.spyOn(api, 'telegramStatus').mockResolvedValue({ authorized: false, configured: true, phone_masked: null }) })

it('shows the QR, then the password box when Telegram asks, then finishes', async () => {
  vi.spyOn(api, 'qrStart').mockResolvedValue({ id: 'q1', url: 'tg://login?token=x', expires_at: '2999-01-01T00:00:00+00:00' })
  const states = ['waiting', 'password_needed'] as const
  let i = 0
  vi.spyOn(api, 'qrState').mockImplementation(async () => ({ state: states[Math.min(i++, 1)] }))
  vi.spyOn(api, 'password').mockResolvedValue({ state: 'done' })
  const onDone = vi.fn()
  render(<TelegramStep onDone={onDone} onSkip={vi.fn()} pollMs={10} />)
  await waitFor(() => expect(screen.getByRole('img', { name: /qr/i })).toHaveAttribute('src', 'data:image/png;base64,AAA'))
  await waitFor(() => expect(screen.getByText('Your account has a two-step password')).toBeInTheDocument())
  fireEvent.change(screen.getByPlaceholderText('Two-step password'), { target: { value: 'secret' } })
  fireEvent.click(screen.getByText('Sign in'))
  await waitFor(() => expect(onDone).toHaveBeenCalled())
  expect(api.password).toHaveBeenCalledWith('secret')
})

it('switches to the phone flow', async () => {
  vi.spyOn(api, 'qrStart').mockResolvedValue({ id: 'q1', url: 'tg://x', expires_at: '2999-01-01T00:00:00+00:00' })
  vi.spyOn(api, 'qrState').mockResolvedValue({ state: 'waiting' })
  vi.spyOn(api, 'sendCode').mockResolvedValue({ ok: true })
  vi.spyOn(api, 'signIn').mockResolvedValue({ state: 'done' })
  const onDone = vi.fn()
  render(<TelegramStep onDone={onDone} onSkip={vi.fn()} pollMs={10} />)
  fireEvent.click(await screen.findByText('Use phone number instead'))
  fireEvent.change(screen.getByPlaceholderText('Phone number with country code'), { target: { value: '+31612345642' } })
  fireEvent.click(screen.getByText('Send code'))
  await waitFor(() => expect(screen.getByPlaceholderText('Code from Telegram')).toBeInTheDocument())
  fireEvent.change(screen.getByPlaceholderText('Code from Telegram'), { target: { value: '12345' } })
  fireEvent.click(screen.getByText('Sign in'))
  await waitFor(() => expect(onDone).toHaveBeenCalled())
  expect(api.signIn).toHaveBeenCalledWith('+31612345642', '12345')
})

it('skips the QR when the account is already signed in', async () => {
  vi.spyOn(api, 'telegramStatus').mockResolvedValue({ authorized: true, configured: true, phone_masked: '+31 •••• ••42' })
  const qrStart = vi.spyOn(api, 'qrStart').mockResolvedValue({ id: 'q1', url: 'tg://x', expires_at: '2999-01-01T00:00:00+00:00' })
  const onDone = vi.fn()
  render(<TelegramStep onDone={onDone} onSkip={vi.fn()} pollMs={10} />)
  await waitFor(() => expect(screen.getByText('Connected as +31 •••• ••42')).toBeInTheDocument())
  // Not merely present -- present outside the two-column QR grid. Its 220px first column is what broke the
  // heading across two lines and pinned this screen to the left while every other setup step is centred.
  expect(document.querySelector('.tg')).toBeNull()
  fireEvent.click(screen.getByText('Continue'))
  expect(onDone).toHaveBeenCalled()
  expect(qrStart).not.toHaveBeenCalled()
})

it('asks for the API keys when this copy has none, then goes on to the QR', async () => {
  vi.spyOn(api, 'telegramStatus').mockResolvedValue({ authorized: false, configured: false, phone_masked: null })
  vi.spyOn(api, 'telegramKeys').mockResolvedValue({ configured: true })
  const qrStart = vi.spyOn(api, 'qrStart').mockResolvedValue({ id: 'q1', url: 'tg://x', expires_at: '2999-01-01T00:00:00+00:00' })
  vi.spyOn(api, 'qrState').mockResolvedValue({ state: 'waiting' })
  render(<TelegramStep onDone={vi.fn()} onSkip={vi.fn()} pollMs={10} />)
  await waitFor(() => expect(screen.getByText(/needs Telegram API keys/)).toBeInTheDocument())
  expect(screen.getByText('Get your keys in about a minute')).toBeInTheDocument()
  expect(screen.getByText(/API development tools/)).toBeInTheDocument()
  expect(qrStart).not.toHaveBeenCalled()
  expect(screen.getByRole('link', { name: /my\.telegram\.org/ })).toHaveAttribute('href', 'https://my.telegram.org/apps')
  fireEvent.change(screen.getByLabelText('API id'), { target: { value: '4242' } })
  fireEvent.change(screen.getByLabelText('API hash'), { target: { value: 'b'.repeat(32) } })
  fireEvent.click(screen.getByText('Save keys'))
  await waitFor(() => expect(api.telegramKeys).toHaveBeenCalledWith('4242', 'b'.repeat(32)))
  await waitFor(() => expect(qrStart).toHaveBeenCalled())
})

it('offers Skip for now on every screen and calls onSkip', async () => {
  vi.spyOn(api, 'qrStart').mockResolvedValue({ id: 'q1', url: 'tg://x', expires_at: '2999-01-01T00:00:00+00:00' })
  vi.spyOn(api, 'qrState').mockResolvedValue({ state: 'waiting' })
  vi.spyOn(api, 'skipTelegram').mockResolvedValue({ source_enabled: false })
  const onSkip = vi.fn()
  render(<TelegramStep onDone={vi.fn()} onSkip={onSkip} pollMs={10} />)
  fireEvent.click(await screen.findByText('Skip for now'))
  await waitFor(() => expect(api.skipTelegram).toHaveBeenCalled())
  expect(onSkip).toHaveBeenCalled()
})

it('shows why a skip failed on the keys screen, where there is no other error line', async () => {
  vi.spyOn(api, 'telegramStatus').mockResolvedValue({ authorized: false, configured: false, phone_masked: null })
  vi.spyOn(api, 'skipTelegram').mockRejectedValue(new ApiError(500, 'Could not switch Telegram off.'))
  const onSkip = vi.fn()
  render(<TelegramStep onDone={vi.fn()} onSkip={onSkip} pollMs={10} />)
  fireEvent.click(await screen.findByText('Skip for now'))
  await waitFor(() => expect(screen.getByText('Could not switch Telegram off.')).toBeInTheDocument())
  expect(onSkip).not.toHaveBeenCalled()
})
