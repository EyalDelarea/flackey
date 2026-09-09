import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import FolderStep from './FolderStep'
import { api } from '../../api'

const mockOnDone = vi.fn()

beforeEach(() => {
  mockOnDone.mockClear()
  vi.clearAllMocks()
  vi.spyOn(api, 'pickFolderAvailable').mockResolvedValue({ available: true })
})

it('shows the Choose… button when picker is available', async () => {
  vi.spyOn(api, 'saveSettings').mockResolvedValue({ library_root: '/tmp', data_dir: '/d', version: '0.1.0', telegram_configured: false, log_path: '/d/flackey.log' })
  render(<FolderStep initial="" onDone={mockOnDone} />)
  await screen.findByText('Choose…')
})

it('hides the Choose… button when picker is unavailable', async () => {
  vi.spyOn(api, 'pickFolderAvailable').mockResolvedValue({ available: false })
  vi.spyOn(api, 'saveSettings').mockResolvedValue({ library_root: '/tmp', data_dir: '/d', version: '0.1.0', telegram_configured: false, log_path: '/d/flackey.log' })
  render(<FolderStep initial="" onDone={mockOnDone} />)
  await waitFor(() => expect(screen.queryByText('Choose…')).not.toBeInTheDocument())
})

it('fills the input when Choose… returns a path', async () => {
  vi.spyOn(api, 'pickFolder').mockResolvedValue({ path: '/Users/me/Music/Crates' })
  vi.spyOn(api, 'saveSettings').mockResolvedValue({ library_root: '/Users/me/Music/Crates', data_dir: '/d', version: '0.1.0', telegram_configured: false, log_path: '/d/flackey.log' })
  render(<FolderStep initial="" onDone={mockOnDone} />)
  await screen.findByText('Choose…')
  fireEvent.click(screen.getByText('Choose…'))
  await waitFor(() => expect(screen.getByDisplayValue('/Users/me/Music/Crates')).toBeInTheDocument())
})
