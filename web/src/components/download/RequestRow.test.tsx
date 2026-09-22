import { act, render, screen, fireEvent } from '@testing-library/react'
import RequestRow from './RequestRow'
import { STEPS } from '../../presentation'
import type { RowView } from '../../presentation'

const base: RowView = { id: 1, title: 'Ace Ventura – Rezonate', version: null, status: 'Starting…', statusTone: 'muted',
  steps: STEPS.map(name => ({ name, state: 'pending' as const })),
  tag: 'queued', dimmed: true, washed: false, action: null,
  candidates: null, rejection: null, artworkUrl: null, rejected: false, retryInSeconds: null, bucket: 'progress', stage: 'search', removable: false,
  formatLabel: null, checks: [], progress: null, fallback: null, outcome: null }

it('spells out whether a failed row can come back, and dresses the two answers differently', () => {
  // The note is the only thing on a final row that says what is left to do -- there is no button beside
  // it -- so it must render, and it must not read the same as the one that has a Try again next to it.
  const { rerender } = render(<RequestRow view={{ ...base, bucket: 'failed', status: 'Stopped by you',
    outcome: { retryable: false, note: 'You stopped this one; paste the link again to start over.' } }}
    onAction={() => {}} onChoose={() => {}} />)
  expect(screen.getByText(/paste the link again/)).toHaveClass('outcome', 'final')
  rerender(<RequestRow view={{ ...base, bucket: 'failed', status: 'Failed — boom',
    action: { label: 'Try again', kind: 'retry' },
    outcome: { retryable: true, note: 'Try again starts the search over.' } }}
    onAction={() => {}} onChoose={() => {}} />)
  expect(screen.getByText('Try again starts the search over.')).not.toHaveClass('final')
  expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
})

it('renders title, status and tag', () => {
  render(<RequestRow view={base} onAction={() => {}} onChoose={() => {}} />)
  expect(screen.getByText('Ace Ventura – Rezonate')).toBeInTheDocument()
  expect(screen.getByText('Starting…')).toBeInTheDocument()
  expect(screen.getByText('queued')).toBeInTheDocument()
})

it('shows the retry timer separately and counts it down', () => {
  vi.useFakeTimers()
  try {
    const view: RowView = { ...base, status: 'Previous attempt: No Soulseek match; alternate source unavailable',
      statusTone: 'amber', retryInSeconds: 61 }
    render(<RequestRow view={view} onAction={() => {}} onChoose={() => {}} />)
    expect(screen.getByText('Previous attempt: No Soulseek match; alternate source unavailable')).toBeInTheDocument()
    expect(screen.getByText('Waiting · 1m 1s')).toBeInTheDocument()
    act(() => { vi.advanceTimersByTime(1000) })
    expect(screen.getByText('Waiting · 1m 0s')).toBeInTheDocument()
  } finally {
    vi.useRealTimers()
  }
})

it('says a six-hour wait in hours, and counts it down once a minute rather than once a second', () => {
  // The wait for Soulseek to turn over is 6 h (issue #74). Counted in minutes it reads as a stuck row, and
  // ticking a label that changes once a minute 21,600 times is 21,600 renders of the same five words.
  vi.useFakeTimers()
  try {
    render(<RequestRow view={{ ...base, status: 'Waiting for Soulseek: nothing on Soulseek matched this track closely enough; 11 more looks, one every 6 h',
      statusTone: 'amber', retryInSeconds: 21600 }} onAction={() => {}} onChoose={() => {}} />)
    expect(screen.getByText('Waiting · 6h 0m')).toBeInTheDocument()
    act(() => { vi.advanceTimersByTime(1000) })
    expect(screen.getByText('Waiting · 6h 0m')).toBeInTheDocument()   // a second is not a tick up here
    act(() => { vi.advanceTimersByTime(59000) })
    expect(screen.getByText('Waiting · 5h 59m')).toBeInTheDocument()
  } finally {
    vi.useRealTimers()
  }
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
  expect(screen.getByLabelText('progress').querySelectorAll('.step')).toHaveLength(STEPS.length)
})

it('a row being checked after the transfer is not announced as waiting in a queue', () => {
  // `pct: null` used to mean one thing -- queued at the peer. The phases after the last byte land use it
  // too now, so the platter cannot keep announcing every one of them as a wait.
  const view: RowView = { ...base, progress: { pct: null, label: 'Checking it is the same recording' } }
  const { container } = render(<RequestRow view={view} onAction={() => {}} onChoose={() => {}} />)
  expect(container.querySelector('.platter')!.getAttribute('aria-label')).toBe('Checking it is the same recording')
})

it('every rung explains itself, so the ladder needs no legend beside it', () => {
  // The explanation used to be a `title` attribute, which the native window renders as nothing at all.
  // Each rung now carries it where both a pointer and a screen reader can reach it; Stepper.test.tsx
  // covers the hover popover itself.
  const view: RowView = { ...base, steps: STEPS.map(name => ({ name, state: 'pending' as const })) }
  render(<RequestRow view={view} onAction={() => {}} onChoose={() => {}} />)
  const rungs = [...screen.getByLabelText('progress').querySelectorAll('.step-tip-target')]
  expect(rungs).toHaveLength(STEPS.length)
  for (const rung of rungs) expect(rung.getAttribute('aria-label')).toMatch(/\w.*\. /)
})

it('renders every step by name with the current one marked, as one ladder', () => {
  const view: RowView = { ...base, tag: null, status: 'Downloading the file',
    steps: [{ name: 'Search', state: 'done' }, { name: 'Choose', state: 'done' }, { name: 'Download', state: 'current' },
            { name: 'Verify', state: 'pending' }, { name: 'Done', state: 'pending' }] }
  render(<RequestRow view={view} onAction={() => {}} onChoose={() => {}} />)
  const track = screen.getByLabelText('progress')
  expect(track.querySelectorAll('.step')).toHaveLength(5)
  for (const name of ['Search', 'Choose', 'Download', 'Verify', 'Done']) expect(screen.getByText(name)).toBeInTheDocument()
  expect(track.querySelector('[aria-current="step"]')!.textContent).toContain('Download')
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
  const view: RowView = { ...base, status: '', dimmed: false, tag: null,
    formatLabel: 'AIFF 16-bit/44.1 kHz, from FLAC via Soulseek',
    action: { label: 'Show in Finder', kind: 'reveal', path: '/a.mp3' }, bucket: 'done', removable: true }
  render(<RequestRow view={view} onAction={() => {}} onChoose={() => {}} />)
  expect(screen.getByText('AIFF 16-bit/44.1 kHz, from FLAC via Soulseek')).toBeInTheDocument()
  // The path line is gone: it repeated the title above it word for word.
  expect(document.querySelector('.status')).toBeNull()
})

it('puts Stop beside a running download, where there are no choices to skip past', () => {
  // The cancel action used to render only under a list of candidates, so a downloading row -- the one
  // place the owner actually wants a way out -- had a Stop that appeared nowhere.
  const onAction = vi.fn()
  const view: RowView = { ...base, status: 'Downloading the file from Soulseek', statusTone: 'amber',
    tag: null, dimmed: false, action: { label: 'Stop', kind: 'cancel' },
    progress: { pct: 5, label: '2.0 MB of 43.1 MB from starvetodeath · 100 kB/s' } }
  render(<RequestRow view={view} onAction={onAction} onChoose={() => {}} />)
  expect(screen.getByText('2.0 MB of 43.1 MB from starvetodeath · 100 kB/s')).toBeInTheDocument()
  // The percentage is the platter's job now, and it is the one place the number appears.
  expect(screen.getByLabelText('5% transferred').querySelector('text')).toHaveTextContent('5')
  fireEvent.click(screen.getByText('Stop'))
  expect(onAction).toHaveBeenCalledWith('cancel', 1, undefined)
})

it('a queued transfer spins without a number: nothing has arrived, so there is no proportion to draw', () => {
  const view: RowView = { ...base, status: 'Waiting for the peer', statusTone: 'amber', tag: null, dimmed: false,
    progress: { pct: null, label: "Waiting in someone's queue" } }
  render(<RequestRow view={view} onAction={() => {}} onChoose={() => {}} />)
  const platter = screen.getByLabelText("Waiting in someone's queue")   // the row's own words, which name the peer
  expect(platter).toHaveClass('waiting')
  expect(platter.querySelector('text')).toBeNull()
})

it('sweeps without claiming a queue when the transfer has started but the size is not known yet', () => {
  // `progressOf` returns a null pct for two different states; only the label tells them apart.
  const view: RowView = { ...base, status: 'Downloading', statusTone: 'amber', tag: null, dimmed: false,
    progress: { pct: null, label: 'Downloading from someone' } }
  render(<RequestRow view={view} onAction={() => {}} onChoose={() => {}} />)
  expect(screen.getByLabelText('Downloading from someone')).toHaveClass('waiting')
})

/* Issue #60: a parked row must not look like one that never started. */
it('reads a parked row as waiting -- a pill on the rail and a wash, not four grey rungs', () => {
  const { container } = render(<RequestRow view={{ ...base, stage: 'waiting', retryInSeconds: 870, dimmed: false,
    status: 'No Soulseek match; alternate source unavailable', statusTone: 'amber' }} onAction={() => {}} onChoose={() => {}} />)
  expect(container.querySelector('.row')).toHaveClass('parked')
  const pill = screen.getByText('Waiting · 14m 30s')
  expect(pill).toHaveClass('waiting-pill')
  // On the right rail, where the row's other standing facts are -- not on a line of its own under the status.
  expect(pill.closest('.row-right')).not.toBeNull()
})

it('tags a failed row with where it stopped, and leaves a running row untagged', () => {
  const { rerender, container } = render(<RequestRow view={{ ...base, bucket: 'failed', stage: 'download', steps: null,
    status: 'Failed — every peer refused the transfer', statusTone: 'red' }} onAction={() => {}} onChoose={() => {}} />)
  expect(container.querySelector('.stage-tag')).toHaveTextContent('download')
  expect(container.querySelector('.stage-tag')).toHaveClass('download')
  rerender(<RequestRow view={{ ...base, bucket: 'progress', stage: 'download' }} onAction={() => {}} onChoose={() => {}} />)
  // A running row already says where it is -- the ladder is right there. The tag is for the list where
  // the ladder is gone and "where did this stop" is the question being asked.
  expect(container.querySelector('.stage-tag')).toBeNull()
})
