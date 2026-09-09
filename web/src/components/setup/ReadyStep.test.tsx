import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import ReadyStep from './ReadyStep'
import { api } from '../../api'

beforeEach(() => { vi.clearAllMocks() })

it('shows Start digging when every tool is present', async () => {
  vi.spyOn(api, 'tools').mockResolvedValue({ ffmpeg: true, ffprobe: true, yt_dlp: true })
  render(<ReadyStep libraryRoot="/tmp/lib" onStart={vi.fn()} />)
  await waitFor(() => expect(screen.getByText('Start digging')).toBeInTheDocument())
})

it('names ffmpeg when it is missing', async () => {
  vi.spyOn(api, 'tools').mockResolvedValue({ ffmpeg: false, ffprobe: true, yt_dlp: true })
  render(<ReadyStep libraryRoot="/tmp/lib" onStart={vi.fn()} />)
  await waitFor(() => expect(screen.getByText(/brew install ffmpeg/)).toBeInTheDocument())
})

it('shows the setup error passed down from App when starting fails', async () => {
  vi.spyOn(api, 'tools').mockResolvedValue({ ffmpeg: true, ffprobe: true, yt_dlp: true })
  render(<ReadyStep libraryRoot="/tmp/lib" onStart={vi.fn()} error="Couldn't finish setup. Try again." />)
  await waitFor(() => expect(screen.getByText("Couldn't finish setup. Try again.")).toBeInTheDocument())
})

it('shows a check-failed message when the request is rejected, and Try again retries', async () => {
  const toolsSpy = vi.spyOn(api, 'tools')
  toolsSpy.mockRejectedValueOnce(new Error('network down'))
  render(<ReadyStep libraryRoot="/tmp/lib" onStart={vi.fn()} />)
  await waitFor(() => expect(screen.getByText(/Couldn't check/)).toBeInTheDocument())
  toolsSpy.mockResolvedValueOnce({ ffmpeg: true, ffprobe: true, yt_dlp: true })
  fireEvent.click(screen.getByText('Try again'))
  await waitFor(() => expect(toolsSpy).toHaveBeenCalledTimes(2))
  await waitFor(() => expect(screen.getByText('Start digging')).toBeInTheDocument())
})

it('says Soulseek only starts on the next launch when an account was just saved', async () => {
  vi.spyOn(api, 'tools').mockResolvedValue({ ffmpeg: true, ffprobe: true, yt_dlp: true })
  render(<ReadyStep libraryRoot="/tmp/lib" onStart={vi.fn()} soulseekPending />)
  await waitFor(() => expect(screen.getByText(/next time you open flackey/)).toBeInTheDocument())
})

it('says nothing about Soulseek when the step was skipped', async () => {
  vi.spyOn(api, 'tools').mockResolvedValue({ ffmpeg: true, ffprobe: true, yt_dlp: true })
  render(<ReadyStep libraryRoot="/tmp/lib" onStart={vi.fn()} />)
  await waitFor(() => expect(screen.getByText('Start digging')).toBeInTheDocument())
  expect(screen.queryByText(/next time you open flackey/)).not.toBeInTheDocument()
})
