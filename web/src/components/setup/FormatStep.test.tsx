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
