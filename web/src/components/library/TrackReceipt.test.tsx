import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import TrackReceipt from './TrackReceipt'
import TrackTable from './TrackTable'
import { api } from '../../api'
import type { Receipt, Track } from '../../api'

vi.mock('../../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api')>()
  return { ...actual, api: { ...actual.api, receipt: vi.fn(), recheckTrack: vi.fn() } }
})

const receipt = (over: Partial<Receipt> = {}): Receipt => ({
  track_id: 7, asked_for: 'Astral Projection - Mahadeva', filed_at: '2026-11-01T10:00:00+00:00', replaced_at: null,
  format: 'AIFF 16-bit/44.1 kHz, from FLAC via Soulseek', lossless_source: true, kept_anyway: false, unverified: false,
  checks: [{ label: 'Same recording', value: '98.2% match', ok: true, note: null },
    { label: 'Quality check', value: 'passed', ok: true, note: null }],
  has_spectrogram: true, recheck: null, ...over,
})

it('shows where the file came from, its checks and its spectrogram', async () => {
  vi.mocked(api.receipt).mockResolvedValue(receipt())
  render(<TrackReceipt trackId={7} />)
  await waitFor(() => expect(screen.getByText('AIFF 16-bit/44.1 kHz, from FLAC via Soulseek')).toBeInTheDocument())
  expect(screen.getByText('Astral Projection - Mahadeva')).toBeInTheDocument()
  expect(screen.getByText('98.2% match')).toBeInTheDocument()
  expect(screen.getByAltText('Spectrogram of this file')).toHaveAttribute('src', '/api/library/7/spectrogram.png')
})

it('checks the file again and says what today’s check found', async () => {
  vi.mocked(api.receipt).mockResolvedValue(receipt())
  vi.mocked(api.recheckTrack).mockResolvedValue(receipt({ recheck: {
    checked_at: '2026-11-21T10:00:00+00:00', status: 'checked', tier: 'unverified', reason: 'one sign of a lossy source' } }))
  render(<TrackReceipt trackId={7} />)
  await waitFor(() => expect(screen.getByText('Check again')).toBeInTheDocument())
  fireEvent.click(screen.getByText('Check again'))
  await waitFor(() => expect(screen.getByText(/Unverified by today's check: one sign of a lossy source/)).toBeInTheDocument())
  expect(api.recheckTrack).toHaveBeenCalledWith(7)
})

it('marks a kept-anyway file in the table and opens its receipt on click', async () => {
  vi.mocked(api.receipt).mockResolvedValue(receipt({ kept_anyway: true }))
  const t = { id: 7, path: '/lib/A/A - T.aiff', fmt: 'aiff', bitrate_kbps: 1411, cutoff_hz: 16000, file_size: 1,
    artist: 'A', title: 'T', mix_name: 'Original Mix', duration_s: 300, isrc: null, catalog_track_id: null,
    request_id: 1, added_at: '', verified_at: '', spectrogram_path: null, catalog: null, source: 'soulseek', source_fmt: 'flac',
    evidence: [{ kind: 'quality_check', value: { status: 'failed', kept_by: 'owner' } }] } as unknown as Track
  render(<TrackTable tracks={[t]} onReveal={vi.fn()} />)
  expect(screen.getByText('Kept anyway')).toBeInTheDocument()
  fireEvent.click(screen.getByText('A – T'))
  await waitFor(() => expect(screen.getByRole('region', { name: 'Why we trust this file' })).toBeInTheDocument())
  fireEvent.click(screen.getByText('A – T'))
  expect(screen.queryByRole('region', { name: 'Why we trust this file' })).not.toBeInTheDocument()
})
