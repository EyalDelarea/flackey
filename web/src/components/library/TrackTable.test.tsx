import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import TrackTable from './TrackTable'
import type { Track } from '../../api'

const t: Track = { id: 1, path: '/lib/Astral Projection/x.mp3', fmt: 'mp3', bitrate_kbps: 320, cutoff_hz: 19800, file_size: 1, artist: 'Astral Projection', title: 'Into the Void',
  mix_name: 'Original Mix', duration_s: 442, isrc: null, catalog_track_id: 7, request_id: null, added_at: '', verified_at: '2026-01-01T00:00:00Z', spectrogram_path: null, source: 'soulseek', source_fmt: 'flac', bit_depth: null, sample_rate: null,
  catalog: { id: 7, artist: 'Astral Projection', title: 'Into the Void', mix_name: 'Original Mix', label: 'TIP Records', genre: 'Psy-Trance', isrc: null, sub_genre: null, catalog_number: null, release_name: null, release_date: '2002-05-01', bpm: null, key: null, duration_ms: null, artwork_url: null } }

it('renders a track row from catalog data', () => {
  const onReveal = vi.fn()
  const { container } = render(<TrackTable tracks={[t]} onReveal={onReveal} />)
  expect(screen.getByText('Astral Projection – Into the Void')).toBeInTheDocument()
  expect(screen.getByText('Original Mix')).toBeInTheDocument()
  expect(screen.getByText('Psy-Trance')).toBeInTheDocument()
  expect(screen.getByText('TIP Records')).toBeInTheDocument()
  expect(screen.getByText('2002')).toBeInTheDocument()
  expect(screen.getByText('320')).toBeInTheDocument()
  expect(container.querySelector('.kbps svg')).not.toBeNull()
  fireEvent.click(screen.getByText('Show in Finder'))
  expect(onReveal).toHaveBeenCalledWith('/lib/Astral Projection/x.mp3')
})

const lossy: Track = { ...t, source: 'deezer_bot', source_fmt: null }

it('names the format each file is filed as', () => {
  // The library mixes formats -- a WAV filed from Soulseek beside an MP3 that came from the bot -- and
  // Kbps alone does not separate them: 1411 is WAV and AIFF alike.
  render(<TrackTable tracks={[t, { ...t, id: 2, fmt: 'aiff', title: 'Second' }]} onReveal={() => {}} />)
  expect(screen.getByText('Format')).toBeInTheDocument()
  expect(screen.getByText('MP3')).toBeInTheDocument()
  expect(screen.getByText('AIFF')).toBeInTheDocument()
})

describe('a button the pointer clicked', () => {
  // The buttons sit in a cell held at opacity 0 and revealed by :hover, :focus-within or selection, so a
  // button that keeps focus holds its row revealed after the pointer has moved on -- which is what left
  // four rows showing "Show in Finder" at once.
  it('is let go of, so the row stops being revealed', () => {
    render(<TrackTable tracks={[t]} onReveal={() => {}} />)
    const btn = screen.getByText('Show in Finder')
    btn.focus()
    fireEvent.click(btn, { detail: 1 })
    expect(document.activeElement).toBe(document.body)
  })

  it('keeps its focus when the click came from the keyboard', () => {
    // Enter on a focused button reports detail 0. Blurring that would drop a keyboard caller out of the
    // table entirely, and :focus-within is the only thing showing them the buttons in the first place.
    render(<TrackTable tracks={[t]} onReveal={() => {}} />)
    const btn = screen.getByText('Show in Finder')
    btn.focus()
    fireEvent.click(btn, { detail: 0 })
    expect(document.activeElement).toBe(btn)
  })
})

it('falls back to the track tags when there is no catalog record', () => {
  render(<TrackTable tracks={[{ ...t, catalog: null }]} onReveal={() => {}} />)
  expect(screen.getAllByText('Unknown').length).toBeGreaterThanOrEqual(2)
})

it('shows a bare kbps number with no checkmark when the track is not verified', () => {
  const { container } = render(<TrackTable tracks={[{ ...t, verified_at: null }]} onReveal={() => {}} />)
  expect(screen.getByText('320')).toBeInTheDocument()
  expect(container.querySelector('.kbps svg')).toBeNull()
})

it('selects a row on click and reveals it on double-click', () => {
  const onReveal = vi.fn()
  render(<TrackTable tracks={[t, { ...t, id: 2, title: 'Second' }]} onReveal={onReveal} />)
  const row = screen.getByText('Astral Projection – Into the Void').closest('.trow')!
  fireEvent.click(row)
  expect(row).toHaveClass('selected')
  fireEvent.click(screen.getByText('Astral Projection – Second').closest('.trow')!)
  expect(row).not.toHaveClass('selected')
  fireEvent.doubleClick(row)
  expect(onReveal).toHaveBeenCalledWith('/lib/Astral Projection/x.mp3')
})

describe('a track still on the lossy copy', () => {
  it('is marked and offers to search again, where a lossless track offers neither', () => {
    const { container, rerender } = render(<TrackTable tracks={[lossy]} onReveal={() => {}} onUpgrade={async () => {}} />)
    expect(container.querySelector('.kbps.lossy')).not.toBeNull()
    expect(container.querySelector('.kbps svg')).toBeNull()      // no green check: it is not the wanted copy
    expect(screen.getByText('Find lossless')).toBeInTheDocument()

    rerender(<TrackTable tracks={[t]} onReveal={() => {}} onUpgrade={async () => {}} />)
    expect(container.querySelector('.kbps.lossy')).toBeNull()
    expect(container.querySelector('.kbps svg')).not.toBeNull()
    expect(screen.queryByText('Find lossless')).not.toBeInTheDocument()
  })

  it('calls onUpgrade with the track and does not select or reveal the row', () => {
    const onUpgrade = vi.fn().mockResolvedValue(undefined)
    const onReveal = vi.fn()
    render(<TrackTable tracks={[lossy]} onReveal={onReveal} onUpgrade={onUpgrade} />)
    fireEvent.click(screen.getByText('Find lossless'))
    expect(onUpgrade).toHaveBeenCalledWith(lossy)
    expect(onReveal).not.toHaveBeenCalled()
  })

  it('shows the search running and blocks a second click until it finishes', async () => {
    let release!: () => void
    const onUpgrade = vi.fn().mockReturnValue(new Promise<void>(r => { release = r }))
    render(<TrackTable tracks={[lossy, { ...lossy, id: 2, title: 'Second' }]} onReveal={() => {}} onUpgrade={onUpgrade} />)
    fireEvent.click(screen.getAllByText('Find lossless')[0])
    expect(await screen.findByText('Searching…')).toBeInTheDocument()
    // one search at a time: the worker serializes lossless downloads, so a second is queued behind it anyway
    fireEvent.click(screen.getByText('Find lossless'))
    expect(onUpgrade).toHaveBeenCalledTimes(1)
    release()
    await waitFor(() => expect(screen.queryByText('Searching…')).not.toBeInTheDocument())
  })

  it('offers no button at all when the page passes no handler', () => {
    render(<TrackTable tracks={[lossy]} onReveal={() => {}} />)
    expect(screen.queryByText('Find lossless')).not.toBeInTheDocument()
  })
})
