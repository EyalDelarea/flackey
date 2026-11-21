import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import ReadyStep from './ReadyStep'
import { api } from '../../api'
import { PlatformContext } from '../../platform'

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

it('tells a Windows owner to reinstall, since the helpers ship inside the app', async () => {
  vi.spyOn(api, 'tools').mockResolvedValue({ ffmpeg: false, ffprobe: true, yt_dlp: true })
  render(<PlatformContext.Provider value="windows"><ReadyStep libraryRoot="/tmp/lib" onStart={vi.fn()} telegram="connected" soulseek="skipped" /></PlatformContext.Provider>)
  await waitFor(() => expect(screen.getByText(/Reinstall Flackey to restore it/)).toBeInTheDocument())
  expect(screen.queryByText(/brew/)).not.toBeInTheDocument()
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

it('walks through a download on request, without sending anything or adding a row', async () => {
  vi.spyOn(api, 'tools').mockResolvedValue({ ffmpeg: true, ffprobe: true, yt_dlp: true })
  render(<ReadyStep libraryRoot="/tmp/lib" onStart={vi.fn()} telegram="connected" soulseek="connected" />)
  await waitFor(() => expect(screen.getByText('Start digging')).toBeInTheDocument())
  const fetchSpy = vi.spyOn(globalThis, 'fetch')
  const calls = (Object.keys(api) as (keyof typeof api)[])
    .filter(k => typeof api[k] === 'function' && k !== 'tools')
    .map(k => vi.spyOn(api, k as never))
  expect(screen.queryByText('Search')).not.toBeInTheDocument()   // short until asked
  fireEvent.click(screen.getByText('See how a download goes'))
  for (const name of ['Search', 'Download', 'Verify', 'Done']) expect(screen.getByText(name)).toBeInTheDocument()
  expect(screen.getByText(/filed under the artist's folder in \/tmp\/lib/)).toBeInTheDocument()
  expect(screen.getByText(/nothing was downloaded/)).toBeInTheDocument()
  expect(fetchSpy).not.toHaveBeenCalled()
  for (const c of calls) expect(c).not.toHaveBeenCalled()
})

it('offers no walkthrough when there is nowhere to fetch from yet', async () => {
  vi.spyOn(api, 'tools').mockResolvedValue({ ffmpeg: true, ffprobe: true, yt_dlp: true })
  render(<ReadyStep libraryRoot="/tmp/lib" onStart={vi.fn()} telegram="skipped" soulseek="skipped" />)
  await waitFor(() => expect(screen.getByText('Connect a source')).toBeInTheDocument())
  expect(screen.queryByText('See how a download goes')).not.toBeInTheDocument()
})

it('describes the download from the sources this owner actually set up', async () => {
  vi.spyOn(api, 'tools').mockResolvedValue({ ffmpeg: true, ffprobe: true, yt_dlp: true })
  const { unmount } = render(<ReadyStep libraryRoot="/tmp/lib" onStart={vi.fn()} telegram="connected" soulseek="skipped" />)
  fireEvent.click(await screen.findByText('See how a download goes'))
  expect(screen.getByText(/From Deezer through Telegram, as an MP3/)).toBeInTheDocument()
  expect(screen.queryByText(/Soulseek first/)).not.toBeInTheDocument()
  unmount()
  render(<ReadyStep libraryRoot="/tmp/lib" onStart={vi.fn()} telegram="skipped" soulseek="pending" />)
  fireEvent.click(await screen.findByText('See how a download goes'))
  expect(screen.getByText('From Soulseek, as a lossless copy.')).toBeInTheDocument()
})
