import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import FormatStep from './FormatStep'
import { api } from '../../api'

const mockOnDone = vi.fn()

beforeEach(() => {
  mockOnDone.mockClear()
  vi.clearAllMocks()
})

it('defaults to AIFF and saves the selected format', async () => {
  vi.spyOn(api, 'saveSettings').mockResolvedValue({ library_root: '/lib', data_dir: '/d', version: '0.1.0',
    telegram_configured: true, log_path: '/d/flackey.log', lossless_filing_format: 'wav' })
  render(<FormatStep libraryRoot="/lib" initial="aiff" formats={['aiff', 'wav', 'flac']} onDone={mockOnDone} />)
  expect(screen.getByRole('button', { name: /AIFF/ })).toHaveAttribute('aria-pressed', 'true')
  fireEvent.click(screen.getByRole('button', { name: /WAV/ }))
  fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
  await waitFor(() => expect(api.saveSettings).toHaveBeenCalledWith('/lib', { lossless_filing_format: 'wav' }))
  expect(mockOnDone).toHaveBeenCalledWith('wav')
})

it('on Windows marks FLAC as the default and starts on it', () => {
  // The server's default there is FLAC: the Windows window (Chromium) cannot play AIFF.
  render(<FormatStep libraryRoot="/lib" initial="" formats={['aiff', 'wav', 'flac']} onDone={mockOnDone} defaultFormat="flac" />)
  const flac = screen.getByRole('button', { name: /FLAC/ })
  expect(flac).toHaveAttribute('aria-pressed', 'true')
  expect(flac).toHaveTextContent('Default')
  const aiff = screen.getByRole('button', { name: /AIFF/ })
  expect(aiff).not.toHaveTextContent('Default')
  // "Recommended" goes with the default, so AIFF does not claim it beside a FLAC marked Default.
  expect(aiff).not.toHaveTextContent('Recommended')
})
