import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import ReadyStep from './ReadyStep'
import { api } from '../../api'

beforeEach(() => { vi.clearAllMocks() })

it('shows Start digging when every tool is present', async () => {
  vi.spyOn(api, 'tools').mockResolvedValue({ ffmpeg: true, ffprobe: true, yt_dlp: true })
  render(<ReadyStep libraryRoot="/tmp/lib" onStart={vi.fn()} telegram="connected" soulseek="skipped" />)
  await waitFor(() => expect(screen.getByText('Start digging')).toBeInTheDocument())
})

it('names ffmpeg when it is missing', async () => {
  vi.spyOn(api, 'tools').mockResolvedValue({ ffmpeg: false, ffprobe: true, yt_dlp: true })
  render(<ReadyStep libraryRoot="/tmp/lib" onStart={vi.fn()} telegram="connected" soulseek="skipped" />)
  await waitFor(() => expect(screen.getByText(/brew install ffmpeg/)).toBeInTheDocument())
})

it('shows the setup error passed down from App when starting fails', async () => {
  vi.spyOn(api, 'tools').mockResolvedValue({ ffmpeg: true, ffprobe: true, yt_dlp: true })
  render(<ReadyStep libraryRoot="/tmp/lib" onStart={vi.fn()} telegram="connected" soulseek="skipped" error="Couldn't finish setup. Try again." />)
  await waitFor(() => expect(screen.getByText("Couldn't finish setup. Try again.")).toBeInTheDocument())
})

it('shows a check-failed message when the request is rejected, and Try again retries', async () => {
  const toolsSpy = vi.spyOn(api, 'tools')
  toolsSpy.mockRejectedValueOnce(new Error('network down'))
  render(<ReadyStep libraryRoot="/tmp/lib" onStart={vi.fn()} telegram="connected" soulseek="skipped" />)
  await waitFor(() => expect(screen.getByText(/Couldn't check/)).toBeInTheDocument())
  toolsSpy.mockResolvedValueOnce({ ffmpeg: true, ffprobe: true, yt_dlp: true })
  fireEvent.click(screen.getByText('Try again'))
  await waitFor(() => expect(toolsSpy).toHaveBeenCalledTimes(2))
  await waitFor(() => expect(screen.getByText('Start digging')).toBeInTheDocument())
})

it('says what is connected', async () => {
  vi.spyOn(api, 'tools').mockResolvedValue({ ffmpeg: true, ffprobe: true, yt_dlp: true })
  render(<ReadyStep libraryRoot="/tmp/lib" onStart={vi.fn()} telegram="connected" soulseek="pending" />)
  await waitFor(() => expect(screen.getByText(/Telegram is connected/)).toBeInTheDocument())
  expect(screen.getByText(/next time you open Flackey/)).toBeInTheDocument()
})

it('offers Connect a source, not Start digging, when both sources were skipped', async () => {
  vi.spyOn(api, 'tools').mockResolvedValue({ ffmpeg: true, ffprobe: true, yt_dlp: true })
  const onConnectSource = vi.fn(); const onStart = vi.fn().mockResolvedValue(undefined)
  render(<ReadyStep libraryRoot="/tmp/lib" onStart={onStart} onConnectSource={onConnectSource} telegram="skipped" soulseek="skipped" />)
  await waitFor(() => expect(screen.getByText(/nowhere to fetch from yet/)).toBeInTheDocument())
  expect(screen.queryByText('Start digging')).not.toBeInTheDocument()

  fireEvent.click(screen.getByText('Connect a source'))
  expect(onConnectSource).toHaveBeenCalled()
  expect(onStart).not.toHaveBeenCalled()

  fireEvent.click(screen.getByText('Explore the app'))   // still allowed to finish without a source
  expect(onStart).toHaveBeenCalled()
})

it('names the saved Soulseek account rather than claiming sources in general', async () => {
  vi.spyOn(api, 'tools').mockResolvedValue({ ffmpeg: true, ffprobe: true, yt_dlp: true })
  render(<ReadyStep libraryRoot="/tmp/lib" onStart={vi.fn()} telegram="skipped" soulseek="pending" />)
  await waitFor(() => expect(screen.getByText(/Soulseek is saved and your music will be filed into/)).toBeInTheDocument())
  expect(screen.queryByText(/Your sources are saved/)).not.toBeInTheDocument()
  expect(screen.queryByText(/Telegram is connected/)).not.toBeInTheDocument()
})
