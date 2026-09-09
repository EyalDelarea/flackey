import { render, screen, fireEvent } from '@testing-library/react'
import RequestRow from './RequestRow'
import type { RowView } from '../../presentation'

const base: RowView = { id: 1, title: 'Ace Ventura – Rezonate', version: null, status: 'Waiting its turn', statusTone: 'muted', statusMono: false,
  steps: ['Identify','Match','Fetch','Verify','File','Done'].map(name => ({ name, state: 'pending' as const })),
  tag: 'queued', dimmed: true, washed: false, action: null,
  candidates: null, rejection: null, artworkUrl: null, rejected: false, retryInSeconds: null, bucket: 'progress', removable: false,
  formatLabel: null, checks: [], progress: null, fallback: null }

it('renders title, status and tag', () => {
  render(<RequestRow view={base} onAction={() => {}} onChoose={() => {}} />)
  expect(screen.getByText('Ace Ventura – Rezonate')).toBeInTheDocument()
  expect(screen.getByText('Waiting its turn')).toBeInTheDocument()
  expect(screen.getByText('queued')).toBeInTheDocument()
})

it('fires the row action and candidate choice', () => {
  const onAction = vi.fn(); const onChoose = vi.fn()
  const view: RowView = { ...base, dimmed: false, washed: true, action: { label: 'Skip this track', kind: 'cancel' },
    candidates: [{ id: 5, title: 'Vini Vici – The Tribe', version: 'Extended Mix', score: 92, length: '8:42', onBeatport: true, lengthNote: 'same length as the video', chosen: true }] }
  render(<RequestRow view={view} onAction={onAction} onChoose={onChoose} />)
  fireEvent.click(screen.getByText('Use this'))
  expect(onChoose).toHaveBeenCalledWith(1, 5)
  fireEvent.click(screen.getByText('Skip this track'))
  expect(onAction).toHaveBeenCalledWith('cancel', 1, undefined)
  expect(screen.getByText('92% match')).toBeInTheDocument()
  expect(screen.getByText('8:42 · On Beatport · same length as the video')).toBeInTheDocument()
})

it('shows the spectrogram well when why is open', () => {
  const view: RowView = { ...base, rejected: true, statusTone: 'red', action: { label: 'Hide why', kind: 'why' },
    rejection: { reason: 'upscale', cutoffKhz: 16, caption: 'A real 320 kbps file has sound up to 20 kHz. This one stops at 16 kHz — it was blown up from a smaller file.', spectrogramUrl: '/api/rejections/2/spectrogram.png' } }
  render(<RequestRow view={view} whyOpen onAction={() => {}} onChoose={() => {}} />)
  expect(screen.getByText('nothing above 16 kHz')).toBeInTheDocument()
  expect(screen.getByRole('img', { name: /spectrogram/i })).toHaveAttribute('src', '/api/rejections/2/spectrogram.png')
})

it('shows a quiet Remove link next to an existing action on a removable row', () => {
  const onAction = vi.fn()
  const view: RowView = { ...base, dimmed: false, tag: null, status: 'Filed', statusTone: 'green',
    action: { label: 'Show in Finder', kind: 'reveal', path: '/a.mp3' }, bucket: 'done', removable: true }
  render(<RequestRow view={view} onAction={onAction} onChoose={() => {}} />)
  expect(screen.getByText('Show in Finder')).toBeInTheDocument()
  fireEvent.click(screen.getByText('Remove'))
  expect(onAction).toHaveBeenCalledWith('remove', 1, undefined)
})

it('does not show Remove on a row that is not removable', () => {
  render(<RequestRow view={base} onAction={() => {}} onChoose={() => {}} />)
  expect(screen.queryByText('Remove')).not.toBeInTheDocument()
})

it('a queued row still shows the whole road ahead beside its tag', () => {
  render(<RequestRow view={base} onAction={() => {}} onChoose={() => {}} />)
  expect(screen.getByText('queued')).toBeInTheDocument()
  expect(screen.getByLabelText('progress').querySelectorAll('.step')).toHaveLength(6)
})

it('renders every step by name with the current one marked, as one ladder', () => {
  const view: RowView = { ...base, tag: null, status: 'Downloading the file',
    steps: [{ name: 'Identify', state: 'done' }, { name: 'Match', state: 'done' }, { name: 'Fetch', state: 'current' },
            { name: 'Verify', state: 'pending' }, { name: 'File', state: 'pending' }, { name: 'Done', state: 'pending' }] }
  render(<RequestRow view={view} onAction={() => {}} onChoose={() => {}} />)
  const track = screen.getByLabelText('progress')
  expect(track.querySelectorAll('.step')).toHaveLength(6)
  for (const name of ['Identify', 'Match', 'Fetch', 'Verify', 'File', 'Done']) expect(screen.getByText(name)).toBeInTheDocument()
  expect(track.querySelector('[aria-current="step"]')!.textContent).toContain('Fetch')
  expect(screen.queryByText('queued')).not.toBeInTheDocument()
})

it('shows the checks that prove the file is the right recording', () => {
  const view: RowView = { ...base, tag: null, checks: [
    { label: 'Same recording', value: '98.5% match', ok: true }, { label: 'Audio to', value: '22.1 kHz', ok: true }] }
  render(<RequestRow view={view} onAction={() => {}} onChoose={() => {}} />)
  expect(screen.getByText('98.5% match')).toBeInTheDocument()
  expect(screen.getByText('22.1 kHz')).toBeInTheDocument()
})

it('names the delivered format on a filed row instead of a bare bitrate', () => {
  const view: RowView = { ...base, status: 'Ace Ventura / Ace Ventura - Rezonate.mp3', statusMono: true, dimmed: false, tag: null,
    formatLabel: 'AIFF 16-bit/44.1 kHz, from FLAC via Soulseek',
    action: { label: 'Show in Finder', kind: 'reveal', path: '/a.mp3' }, bucket: 'done', removable: true }
  render(<RequestRow view={view} onAction={() => {}} onChoose={() => {}} />)
  expect(screen.getByText('AIFF 16-bit/44.1 kHz, from FLAC via Soulseek')).toBeInTheDocument()
  expect(screen.getByText('Ace Ventura / Ace Ventura - Rezonate.mp3')).toHaveClass('mono')
})
