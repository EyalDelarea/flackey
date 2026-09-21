import { bucketCounts, bucketOf, canRetry, failedSummary, gb, groupRows, mmss, presentRow, stepIndex } from './presentation'
import type { Bundle, Playlist, Request } from './api'

const base: Request = { id: 1, created_at: '', updated_at: '', raw_text: 'Ace Ventura - Rezonate', kind: 'yt_track', state: 'queued',
  playlist_id: null, playlist_position: null, source_url: null, query_artist: 'Ace Ventura', query_title: 'Rezonate', query_version: null,
  query_duration_s: 521, chosen_candidate_id: null, catalog_track_id: null, fetch_source: null, confidence: null, flag_reason: null, error_message: null,
  attempts: 0, retry_after: null, track_id: null }
const opts = { libraryRoot: '/Users/me/Music/DJ Library', telegramAuthorized: true, now: new Date('2026-09-06T10:00:00Z'), whyOpen: false }
const bundle = (r: Partial<Request>, rest: Partial<Bundle> = {}): Bundle =>
  ({ request: { ...base, ...r }, candidates: [], catalog: null, track: null, rejection: null, ...rest })

describe('formatting', () => {
  it('mmss', () => { expect(mmss(442)).toBe('7:22'); expect(mmss(5)).toBe('0:05'); expect(mmss(null)).toBe('?:??') })
  it('gb', () => { expect(gb(3.2e9)).toBe('3.2 GB'); expect(gb(512e6)).toBe('512 MB') })
  it('stepIndex', () => { expect(stepIndex('fetching')).toBe(2); expect(stepIndex('done')).toBe(4); expect(stepIndex('rejected')).toBeNull() })
})

describe('presentRow', () => {
  it('queued row is dimmed and tagged, and can be stopped before it starts', () => {
    const v = presentRow(bundle({}), opts)
    expect(v.title).toBe('Ace Ventura – Rezonate')
    expect(v.status).toBe('Starting\u2026'); expect(v.tag).toBe('queued'); expect(v.dimmed).toBe(true)
    expect(v.action).toEqual({ label: 'Stop', kind: 'cancel' })
  })
  it('in-progress row carries one ladder: each step names itself and holds its own state', () => {
    // Reviewed, so the whole five-rung ladder is on show and every rung's state can be read at once.
    const v = presentRow(bundle({ state: 'fetching', reviewed: 1 }), opts)
    expect(v.status).toBe('Downloading the file'); expect(v.statusTone).toBe('amber')
    expect(v.steps).toEqual([
      { name: 'Search', state: 'done' }, { name: 'Choose', state: 'done' }, { name: 'Download', state: 'current' },
      { name: 'Verify', state: 'pending' }, { name: 'Done', state: 'pending' }])
  })
  it('has no File rung: filing is a moment, not a phase you wait in', () => {
    // Dropping File leaves `filing` without a rung of its own. It rides on Verify rather than on Done,
    // because a Done rung pulsing amber on a track that is not filed yet is the same lie the all-green
    // duplicate row tells.
    const v = presentRow(bundle({ state: 'filing', reviewed: 1 }), opts)
    expect(v.steps).toEqual([
      { name: 'Search', state: 'done' }, { name: 'Choose', state: 'done' }, { name: 'Download', state: 'done' },
      { name: 'Verify', state: 'current' }, { name: 'Done', state: 'pending' }])
  })
  it('leaves Choose off a track that was never stopped for one', () => {
    // Most tracks are matched outright and never wait on anybody. A green Choose rung on those claims a
    // step that never happened -- the same false completion the all-green duplicate row used to tell.
    expect(presentRow(bundle({ state: 'fetching' }), opts).steps!.map(s => s.name))
      .toEqual(['Search', 'Download', 'Verify', 'Done'])
    expect(presentRow(bundle({ state: 'done', track_id: 7 }), opts).steps!.map(s => s.name))
      .toEqual(['Search', 'Download', 'Verify', 'Done'])
  })
  it('keeps Choose on a track that waited for one, after the owner has answered', () => {
    // The rung is not "you are choosing now", it is "this track needed you". That stays true once they
    // have picked, so the ladder must not shrink under them the moment they click Use this.
    const v = presentRow(bundle({ state: 'fetching', reviewed: 1 }), opts)
    expect(v.steps).toEqual([
      { name: 'Search', state: 'done' }, { name: 'Choose', state: 'done' }, { name: 'Download', state: 'current' },
      { name: 'Verify', state: 'pending' }, { name: 'Done', state: 'pending' }])
  })
  it('shows Choose while the choice is being asked for, before anything durable is written back', () => {
    const v = presentRow(bundle({ state: 'awaiting_review', flag_reason: 'the video is longer' }), opts)
    expect(v.steps!.map(s => s.name)).toEqual(['Search', 'Choose', 'Download', 'Verify', 'Done'])
    expect(v.steps![1].state).toBe('current')
  })
  it('a fetching row says which network the bytes are coming from', () => {
    expect(presentRow(bundle({ state: 'fetching', fetch_source: 'soulseek' }), { ...opts, soulseekConnected: true }).status).toBe('Downloading the file from Soulseek')
    expect(presentRow(bundle({ state: 'fetching', fetch_source: 'deezer' }), opts).status).toBe('Downloading the file from Deezer')
  })
  it('a soulseek row that loses its connection mid-fetch is paused even while Telegram stays authorized', () => {
    const v = presentRow(bundle({ state: 'fetching', fetch_source: 'soulseek' }), { ...opts, soulseekConnected: false })
    expect(v.status).toBe('Paused — Soulseek is not connected'); expect(v.dimmed).toBe(true)
  })
  it('the Done rung reads as reached when the request is done (it indexed past the array before)', () => {
    expect(presentRow(bundle({ state: 'done', reviewed: 1 }), opts).steps).toEqual(
      ['Search', 'Choose', 'Download', 'Verify', 'Done'].map(name => ({ name, state: 'done' })))
    expect(presentRow(bundle({ state: 'queued' }), opts).steps!.every(x => x.state === 'pending')).toBe(true)
  })
  it('filed row shows the relative path in mono, a verified badge and Show in Finder', () => {
    const track = { id: 9, path: '/Users/me/Music/DJ Library/Ace Ventura/Ace Ventura - Rezonate.mp3', fmt: 'mp3', bitrate_kbps: 320, cutoff_hz: 19800,
      file_size: 1, artist: 'Ace Ventura', title: 'Rezonate', mix_name: 'Original Mix', duration_s: 521, isrc: null, catalog_track_id: null, fetch_source: null,
      request_id: 1, added_at: '', verified_at: null, spectrogram_path: null, source: 'deezer_bot', source_fmt: null, bit_depth: null, sample_rate: null, catalog: null }
    const v = presentRow(bundle({ state: 'done', track_id: 9 }, { track }), opts)
    // No status line on a filed row: it used to repeat the title as a library-relative path.
    expect(v.status).toBe('')
    expect(v.formatLabel).toBe('MP3 320 kbps via Deezer')
    expect(v.version).toBe('Original Mix')
    // Four rungs, not five: nobody was ever asked to choose this one. See the Choose tests above.
    expect(v.steps).toEqual(['Search', 'Download', 'Verify', 'Done'].map(name => ({ name, state: 'done' })))
    expect(v.action).toEqual({ label: 'Show in Finder', kind: 'reveal', path: track.path })
  })
  it('a lossless row names the delivered format and shows the evidence it is the right recording', () => {
    // Both already ride in the bundle: /api/queue builds `track.format.label`, and the attempt row carries the
    // Chromaprint score. The old row showed neither, so a filed AIFF read as a bare "1411 kbps".
    const track = { id: 9, path: '/Users/me/Music/DJ Library/Hallucinogen/Hallucinogen - Orphic Thrench.aiff', fmt: 'aiff',
      bitrate_kbps: 1411, cutoff_hz: 22050, file_size: 1, artist: 'Hallucinogen', title: 'Orphic Thrench', mix_name: 'Original Mix',
      duration_s: 442, isrc: null, catalog_track_id: null, request_id: 1, added_at: '', verified_at: null, spectrogram_path: null,
      source: 'soulseek', source_fmt: 'flac', bit_depth: 16, sample_rate: 44100, catalog: null,
      format: { fmt: 'aiff', bit_depth: 16, sample_rate: 44100, source: 'soulseek', source_fmt: 'flac',
                label: 'AIFF 16-bit/44.1 kHz, from FLAC via Soulseek' } }
    const attempt = { id: 4, request_id: 1, provider: 'soulseek', created_at: '', query: 'q', outcome: 'filed',
      fingerprint: { status: 'ok', score: 0.985, offset_s: 0, reason: null },
      spectrogram_path: null, first_byte_ms: 2002, total_ms: 26990 }
    const v = presentRow(bundle({ state: 'done', track_id: 9 }, { track, attempt }), opts)
    expect(v.formatLabel).toBe('AIFF 16-bit/44.1 kHz, from FLAC via Soulseek')
    expect(v.checks).toEqual([
      { label: 'Same recording', value: '98.5% match', ok: true },
      { label: 'Verified to', value: '22.1 kHz', ok: true }])
  })
  it('a failed fingerprint reads as a failed check, and a rejection still reports its cutoff', () => {
    const attempt = { id: 5, request_id: 1, provider: 'soulseek', created_at: '', query: 'q', outcome: 'fingerprint_failed',
      fingerprint: { status: 'failed', score: 0.41, offset_s: null, reason: 'below 0.9' }, spectrogram_path: null,
      first_byte_ms: null, total_ms: null }
    expect(presentRow(bundle({ state: 'fetching' }, { attempt }), opts).checks)
      .toEqual([{ label: 'Same recording', value: '41.0% match', ok: false }])
    const rejection = { id: 2, request_id: 1, reason: 'upscale', bitrate_kbps: 320, cutoff_hz: 16000, spectrogram_path: null, created_at: '' }
    expect(presentRow(bundle({ state: 'rejected' }, { rejection }), opts).checks)
      .toEqual([{ label: 'Audio only to', value: '16.0 kHz', ok: false }])
  })
  it('filed row without track shows a muted message when file was removed from library', () => {
    const v = presentRow(bundle({ state: 'done' }), opts)
    expect(v.status).toBe('Filed earlier — the file is no longer in your library folder')
    expect(v.statusTone).toBe('muted'); expect(v.action).toBeNull()
  })
  it('review row lists candidates with length notes and marks the chosen one', () => {
    const cands = [
      { id: 5, request_id: 1, source: 's', source_ref: 'a', artist: 'Vini Vici', title: 'The Tribe', mix_name: 'Extended Mix', duration_s: 522, deezer_id: null, isrc: null, rank: 1, score: 92, catalog_track_id: 3 },
      { id: 6, request_id: 1, source: 's', source_ref: 'b', artist: 'Vini Vici', title: 'The Tribe', mix_name: null, duration_s: 372, deezer_id: null, isrc: null, rank: 2, score: 74, catalog_track_id: null, fetch_source: null },
    ]
    const v = presentRow(bundle({ state: 'awaiting_review', flag_reason: 'best match is a Extended Mix; no version was requested', chosen_candidate_id: 5 }, { candidates: cands }), opts)
    expect(v.status).toBe('Needs your choice — best match is a Extended Mix; no version was requested. Pick the version you want.')
    expect(v.washed).toBe(true); expect(v.steps?.[1]).toEqual({ name: 'Choose', state: 'current' })
    expect(v.candidates).toEqual([
      { id: 5, title: 'Vini Vici – The Tribe', version: 'Extended Mix', score: 92, length: '8:42', onBeatport: true, lengthNote: 'same length as the video', chosen: true },
      { id: 6, title: 'Vini Vici – The Tribe', version: 'Original Mix', score: 74, length: '6:12', onBeatport: false, lengthNote: '2:29 shorter than the video', chosen: false },
    ])
    expect(v.action).toEqual({ label: 'Skip this track', kind: 'cancel' })
  })
  it('rejected row explains and toggles why', () => {
    const rejection = { id: 2, request_id: 1, reason: 'Sounds like a 128 kbps upscale', bitrate_kbps: 320, cutoff_hz: 16000, spectrogram_path: '/x.png', created_at: '' }
    const v = presentRow(bundle({ state: 'rejected' }, { rejection }), opts)
    expect(v.status).toBe('Sounds like a 128 kbps upscale. Deleted, not added to your library.'); expect(v.statusTone).toBe('red')
    expect(v.rejected).toBe(true); expect(v.steps).toBeNull(); expect(v.action).toEqual({ label: 'See why', kind: 'why' })
    expect(v.rejection).toEqual({ reason: 'Sounds like a 128 kbps upscale', cutoffKhz: 16, spectrogramUrl: '/api/rejections/2/spectrogram.png',
      caption: 'A real 320 kbps file has sound up to 20 kHz. This one stops at 16 kHz — it was blown up from a smaller file.' })
    expect(presentRow(bundle({ state: 'rejected' }, { rejection }), { ...opts, whyOpen: true }).action?.label).toBe('Hide why')
  })
  // Issue #61 added a second reason a file can be refused: it is genuine 320 kbps audio of the *wrong
  // recording*. The upscale caption is about the spectral check and quotes its cutoff, so saying it here
  // would tell the owner a healthy file is a fake and print a frequency that is perfectly fine as the proof.
  it('a different-recording rejection explains the real reason and quotes no cutoff', () => {
    const rejection = { id: 7, request_id: 1, reason: 'a different recording: best score 0.61 below 0.79',
      bitrate_kbps: 320, cutoff_hz: null, spectrogram_path: '/x.png', created_at: '', kind: 'different_recording' }
    const v = presentRow(bundle({ state: 'rejected' }, { rejection }), opts)
    expect(v.status).toBe('a different recording: best score 0.61 below 0.79. Deleted, not added to your library.')
    expect(v.rejection).toEqual({ reason: 'a different recording: best score 0.61 below 0.79', cutoffKhz: null,
      spectrogramUrl: '/api/rejections/7/spectrogram.png',
      caption: 'The audio itself is genuine — it is just not the recording that was asked for, so it was not kept.' })
    // Nothing failed a cutoff, so no cutoff is claimed either way round.
    expect(v.checks).toEqual([])
  })
  it('a rejected row does not borrow the Soulseek attempt\'s fingerprint: it refused a different file', () => {
    const attempt = { id: 8, request_id: 1, provider: 'soulseek', created_at: '', query: 'q', outcome: 'fingerprint_failed',
      fingerprint: { status: 'failed', score: 0.61, offset_s: null, reason: 'below 0.79' }, spectrogram_path: null,
      first_byte_ms: null, total_ms: null }
    const rejection = { id: 9, request_id: 1, reason: 'a different recording: best score 0.55 below 0.79',
      bitrate_kbps: 320, cutoff_hz: null, spectrogram_path: null, created_at: '', kind: 'different_recording' }
    expect(presentRow(bundle({ state: 'rejected' }, { attempt, rejection }), opts).checks).toEqual([])
  })
  // The peer's copy scored 0.61 and was thrown away; the Deezer copy that was actually filed scored 0.98,
  // and that score is nowhere in the bundle. Showing the attempt's number here described the wrong file.
  it('a track filed from Deezer does not show the discarded peer copy\'s match', () => {
    const track = { id: 11, path: '/l/A - T.mp3', fmt: 'mp3', bitrate_kbps: 320, cutoff_hz: 20500, file_size: 1,
      artist: 'A', title: 'T', mix_name: 'Original Mix', duration_s: 442, isrc: null, catalog_track_id: null,
      request_id: 1, added_at: '', verified_at: null, spectrogram_path: null, source: 'deezer_bot', source_fmt: null,
      bit_depth: null, sample_rate: 44100, catalog: null }
    const attempt = { id: 12, request_id: 1, provider: 'soulseek', created_at: '', query: 'q', outcome: 'fingerprint_failed',
      fingerprint: { status: 'failed', score: 0.61, offset_s: null, reason: 'below 0.79' }, spectrogram_path: null,
      first_byte_ms: null, total_ms: null }
    expect(presentRow(bundle({ state: 'done', track_id: 11 }, { track, attempt }), opts).checks)
      .toEqual([{ label: 'Verified to', value: '20.5 kHz', ok: true }])
  })
  it('duplicate, cancelled, not found, error', () => {
    expect(presentRow(bundle({ state: 'duplicate' }), opts).status).toBe('Already in your library — skipped, nothing downloaded twice')
    // "Skipped" said what happened without saying who did it, on a row sitting under a red "Failed" badge.
    expect(presentRow(bundle({ state: 'cancelled' }), opts).status).toBe('Stopped by you')
    expect(presentRow(bundle({ state: 'not_found', error_message: 'no candidates from source' }), opts)).toMatchObject({
      status: 'No downloadable match found — no candidates from source', action: { label: 'Try again', kind: 'retry' },
    })
    const e = presentRow(bundle({ state: 'error', error_message: 'boom' }), opts)
    expect(e.status).toBe('Failed — boom'); expect(e.statusTone).toBe('red'); expect(e.action).toEqual({ label: 'Try again', kind: 'retry' })
  })
  it('backoff row counts down and paused rows say so when Telegram is signed out', () => {
    const v = presentRow(bundle({ retry_after: '2026-09-06T10:00:25+00:00', flag_reason: 'Beatport unreachable, will retry', attempts: 1 }), opts)
    expect(v.status).toBe('Previous attempt: Beatport unreachable'); expect(v.retryInSeconds).toBe(25)
    const p = presentRow(bundle({ state: 'fetching' }), { ...opts, telegramAuthorized: false })
    // The tag counted rungs -- "step 3 of 6" -- against a ladder that has five, and the count was the
    // wrong thing to say anyway: the rung is right there beside it. It names where the row stopped.
    expect(p.status).toBe('Paused — connect a source to continue'); expect(p.tag).toBe('paused at Download'); expect(p.dimmed).toBe(true)
  })
})

describe('groupRows', () => {
  const playlists: Playlist[] = [{ id: 7, source_url: 'u', name: 'Progressive Psy Set 2026', created_at: '', updated_at: '', track_ids: [], file: '' }]
  it('groups by playlist with a summary, then single tracks', () => {
    const g = groupRows([
      bundle({ id: 3, created_at: '2026-09-06T08:00:00Z', playlist_id: 7, state: 'done' }), bundle({ id: 4, created_at: '2026-09-06T09:00:00Z', playlist_id: 7, state: 'awaiting_review' }),
      bundle({ id: 5, created_at: '2026-09-06T10:00:00Z', playlist_id: 7, state: 'rejected' }), bundle({ id: 6, created_at: '2026-09-06T11:00:00Z', playlist_id: 7, retry_after: '2026-09-06T10:00:30+00:00', flag_reason: 'Beatport unreachable, will retry' }),
      bundle({ id: 2, created_at: '2026-09-06T07:00:00Z' }),
    ], playlists, opts)
    expect(g.map(x => x.name)).toEqual(['Progressive Psy Set 2026', 'Single tracks'])
    expect(g[0].summary).toEqual({ filed: 1, total: 4, needsChoice: 1, rejected: 1, inFlight: 1, pct: 25 })
    expect(g[0].rows.map(r => r.id)).toEqual([6, 5, 4, 3])          // newest first with 'all' filter
    expect(g[0].beatportDown).toEqual({ seconds: 30, requestIds: [6] })
    expect(g[1].rows.map(r => r.id)).toEqual([2]); expect(g[1].beatportDown).toBeNull()
  })

  it('filters rows to the given bucket, keeps the unfiltered summary, and drops groups left empty', () => {
    const bundles = [
      bundle({ id: 3, playlist_id: 7, state: 'done' }), bundle({ id: 4, playlist_id: 7, state: 'awaiting_review' }),
      bundle({ id: 5, playlist_id: 7, state: 'rejected' }),
      bundle({ id: 2, state: 'queued' }),
    ]
    const failedOnly = groupRows(bundles, playlists, opts, 'failed')
    expect(failedOnly.map(g => g.name)).toEqual(['Progressive Psy Set 2026'])   // Single tracks group is empty, dropped
    expect(failedOnly[0].rows.map(r => r.id)).toEqual([5])
    expect(failedOnly[0].summary).toEqual({ filed: 1, total: 3, needsChoice: 1, rejected: 1, inFlight: 0, pct: 33 })  // summary unaffected by the filter

    const progressOnly = groupRows(bundles, playlists, opts, 'progress')
    expect(progressOnly.map(g => g.name)).toEqual(['Single tracks'])
    expect(progressOnly[0].rows.map(r => r.id)).toEqual([2])

    expect(groupRows(bundles, playlists, opts, 'all')).toHaveLength(2)
  })

  it("sorts newest-first when filter is 'all', oldest-first otherwise", () => {
    const bundles = [
      bundle({ id: 1, created_at: '2026-09-06T08:00:00Z', playlist_id: 7, state: 'done' }),
      bundle({ id: 2, created_at: '2026-09-06T10:00:00Z', playlist_id: 7, state: 'done' }),
      bundle({ id: 3, created_at: '2026-09-06T09:00:00Z', playlist_id: 7, state: 'done' }),
    ]
    const allFilter = groupRows(bundles, playlists, opts, 'all')
    expect(allFilter[0].rows.map(r => r.id)).toEqual([2, 3, 1])  // newest first

    const doneFilter = groupRows(bundles, playlists, opts, 'done')
    expect(doneFilter[0].rows.map(r => r.id)).toEqual([1, 2, 3])  // oldest first
  })
})

describe('bucketOf', () => {
  it('buckets in-progress, needs-you, done and failed states', () => {
    for (const s of ['queued', 'identifying', 'fetching', 'verifying', 'filing'] as const) expect(bucketOf(s)).toBe('progress')
    expect(bucketOf('awaiting_review')).toBe('needs')
    expect(bucketOf('done')).toBe('done'); expect(bucketOf('duplicate')).toBe('done')
    for (const s of ['rejected', 'not_found', 'error', 'cancelled'] as const) expect(bucketOf(s)).toBe('failed')
  })
})

const FAILED_STATES = ['rejected', 'not_found', 'error', 'cancelled'] as const

describe('failed states say why they are failed and whether they can come back', () => {
  it('gives every state in the failed bucket a note, and only those states', () => {
    // The list called "Failed" is where the owner goes to ask what happened. A state that lands there
    // with nothing to say is the bug this is here to stop, whichever state it turns out to be.
    for (const s of FAILED_STATES) {
      expect(bucketOf(s), `${s} should be a failure`).toBe('failed')
      expect(presentRow(bundle({ state: s }), opts).outcome, `${s} has no note`).not.toBeNull()
    }
    for (const s of ['queued', 'fetching', 'awaiting_review', 'done', 'duplicate'] as const) {
      expect(presentRow(bundle({ state: s }), opts).outcome, `${s} is not a failure`).toBeNull()
    }
  })

  it('agrees with the retry button on every one of them', () => {
    // `canRetry` draws the button and counts the batch; the note tells the owner what the button means.
    // If these two ever disagree a row says "paste the link again" beside a working Try again, or the
    // other way round -- which is the badge-versus-button confusion again, one row further down.
    for (const s of FAILED_STATES) {
      const v = presentRow(bundle({ state: s }), opts)
      expect(v.outcome!.retryable, s).toBe(canRetry(s))
      expect(v.action?.kind === 'retry', s).toBe(canRetry(s))
    }
    expect(FAILED_STATES.filter(canRetry)).toEqual(['not_found', 'error'])
  })

  it('points a final row at the one thing that does work: submitting the link again', () => {
    for (const s of ['rejected', 'cancelled'] as const) {
      expect(presentRow(bundle({ state: s }), opts).outcome!.note).toMatch(/paste the link again/i)
    }
  })

  it('does not repeat a stale retry promise on a row it has just called final', () => {
    // `Worker.cancel` leaves `flag_reason` alone, so a request that had backed off and was then stopped
    // still carries "will retry" in the column the queued row renders. The cancelled row must not.
    const v = presentRow(bundle({ state: 'cancelled', flag_reason: 'Beatport unreachable, will retry' }), opts)
    expect(v.status).toBe('Stopped by you')
    expect(v.status + v.outcome!.note).not.toMatch(/will retry/)
  })
})

describe('failedSummary', () => {
  const failed = (id: number, state: Request['state']) => bundle({ id, state })

  it('explains the gap between the Failed badge and the Retry all count', () => {
    expect(failedSummary([failed(1, 'error'), failed(2, 'not_found'), failed(3, 'rejected'),
                          failed(4, 'cancelled'), failed(5, 'cancelled')]))
      // Ordered by FAILED_COPY, not by what the list happens to hold first, so the sentence does not
      // reshuffle itself under the owner every time a row is retried or removed.
      .toBe('3 of these 5 cannot be tried again — 1 failed the quality check, 2 you stopped. '
            + 'Each row says what to do instead.')
  })

  it('says so plainly when the button is disabled because nothing here can move', () => {
    expect(failedSummary([failed(1, 'rejected'), failed(2, 'rejected')]))
      .toBe('Nothing here can be tried again — 2 failed the quality check. Each row says what to do instead.')
  })

  it('stays silent when there is no gap to explain', () => {
    // Badge and button already agree; a line saying "0 of these" is noise above a list that is fine.
    expect(failedSummary([failed(1, 'error'), failed(2, 'not_found')])).toBeNull()
    expect(failedSummary([])).toBeNull()
  })

  it('counts only failures, so a History tab full of finished tracks does not inflate it', () => {
    expect(failedSummary([failed(1, 'done'), failed(2, 'duplicate'), failed(3, 'cancelled')]))
      .toBe('Nothing here can be tried again — 1 you stopped. That row says what to do instead.')
  })
})

describe('bucketCounts', () => {
  it('counts bundles per bucket plus a running total', () => {
    const counts = bucketCounts([
      bundle({ id: 1, state: 'queued' }), bundle({ id: 2, state: 'fetching' }), bundle({ id: 3, state: 'awaiting_review' }),
      bundle({ id: 4, state: 'done' }), bundle({ id: 5, state: 'duplicate' }), bundle({ id: 6, state: 'rejected' }),
    ])
    expect(counts).toEqual({ all: 6, progress: 2, needs: 1, done: 2, failed: 1 })
  })
})

describe('presentRow bucket/removable', () => {
  it('marks done and failed rows removable; in-progress and needs-you rows are not', () => {
    expect(presentRow(bundle({ state: 'done' }), opts)).toMatchObject({ bucket: 'done', removable: true })
    expect(presentRow(bundle({ state: 'rejected' }), opts)).toMatchObject({ bucket: 'failed', removable: true })
    expect(presentRow(bundle({ state: 'queued' }), opts)).toMatchObject({ bucket: 'progress', removable: false })
    expect(presentRow(bundle({ state: 'awaiting_review' }), opts)).toMatchObject({ bucket: 'needs', removable: false })
  })
})

describe('the lossy fallback is visible rather than silent', () => {
  const filed = (over: Record<string, unknown>, attempt: Record<string, unknown> | null = null) =>
    bundle({ state: 'done' }, {
      track: { path: '/lib/a/x.mp3', fmt: 'mp3', bitrate_kbps: 320, source: 'deezer_bot',
        source_fmt: null, mix_name: null, cutoff_hz: 20000, ...over } as never,
      attempt: attempt as never,
    })

  it('names the format and why the lossless attempt missed', () => {
    const v = presentRow(filed({}, { outcome: 'transfer_failed' }), opts)
    expect(v.fallback).toEqual({
      label: 'MP3 320 kbps — lossless unavailable',
      reason: 'Kept the matched MP3 copy because the people who had it would not send it.',
    })
  })

  it('says so plainly when no lossless search ever ran', () => {
    expect(presentRow(filed({}, null), opts).fallback?.reason)
      .toBe('Kept the matched MP3 copy because no lossless search ran for this one.')
  })

  it('still explains an outcome it has no phrasing for, rather than saying nothing', () => {
    expect(presentRow(filed({}, { outcome: 'something_new' }), opts).fallback?.reason)
      .toContain('ended in something_new')
  })

  it('is absent once a lossless provider supplied the file', () => {
    // source_fmt is set only on a lossless hit, so its presence is the test -- not the provider's name,
    // which would go stale the moment there are two of them.
    const v = presentRow(filed({ fmt: 'wav', source: 'soulseek', source_fmt: 'flac', path: '/lib/a/x.wav' }), opts)
    expect(v.fallback).toBeNull()
  })
})

describe('a running transfer shows where it has got to', () => {
  const fetching = bundle({ state: 'fetching', fetch_source: 'soulseek' })
  const p = (over: Record<string, unknown> = {}) => ({ request_id: fetching.request.id, bytes: 21_000_000,
    size: 42_000_000, peer: 'someone', pct: 50, speed_bps: 1_400_000, pick: 1, state: 'InProgress', ...over })

  it('reports bytes, peer and speed, not just a percentage', () => {
    const v = presentRow(fetching, { ...opts, fetchProgress: [p()] as never })
    expect(v.progress).toEqual({ pct: 50, label: '21.0 MB of 42.0 MB from someone · 1.4 MB/s' })
  })

  it('shows a waiting bar with no percentage while queued at the peer', () => {
    // 0% would read as a stall; this is a queue, which is a different thing to say.
    const v = presentRow(fetching, { ...opts, fetchProgress: [p({ state: 'Queued, Remotely', bytes: 0, pct: 0 })] as never })
    expect(v.progress).toEqual({ pct: null, label: "Waiting in someone's queue" })
  })

  it('belongs only to the row it names, with several transfers running at once', () => {
    const v = presentRow(fetching, { ...opts, fetchProgress: [p({ request_id: 999 })] as never })
    expect(v.progress).toBeNull()
  })

  it('says what it is doing in the seconds after the bytes land, not sitting full and silent', () => {
    // verify, fingerprint and convert all run with the row still FETCHING, so without this the platter
    // reads 100% and the label says nothing for the seconds those take.
    const v = presentRow(fetching, { ...opts, fetchProgress: [p({ phase: 'fingerprinting', pct: 100 })] as never })
    expect(v.progress).toEqual({ pct: null, label: 'Checking it is the same recording' })
  })

  it('is absent when nothing is downloading', () => {
    expect(presentRow(fetching, { ...opts, fetchProgress: [] }).progress).toBeNull()
  })
})

describe('a queued track', () => {
  it('says it is starting rather than counting a line it does not wait in', () => {
    // Every queued track is picked up on the worker's next pass now, so there is no position to report.
    expect(presentRow(bundle({ id: 2 }), opts).status).toBe('Starting\u2026')
    expect(presentRow(bundle({ id: 2 }), opts).action).toEqual({ label: 'Stop', kind: 'cancel' })
  })

  it('keeps its countdown when it is on a retry backoff, and offers no Stop over it', () => {
    const v = presentRow(bundle({ id: 4, retry_after: '2026-09-06T10:01:00Z',
      flag_reason: 'Beatport unreachable, will retry' }), opts)
    expect(v.status).toBe('Previous attempt: Beatport unreachable')
    expect(v.retryInSeconds).toBe(60)
  })

  it('shortens the verbose Soulseek fallback warning without losing its meaning', () => {
    const v = presentRow(bundle({ retry_after: '2026-09-06T10:00:25+00:00',
      flag_reason: 'no way to fetch this track: nothing on Soulseek matched this track closely enough, Deezer offered nothing to fall back on' }), opts)
    expect(v.status).toBe('Previous attempt: No Soulseek match; nothing on Deezer to fall back on')
    expect(v.retryInSeconds).toBe(25)
  })

  it('shows the wait for Soulseek as itself rather than as a previous attempt', () => {
    // The worker words this one for the owner already (issue #74): what is being waited for and how many
    // looks are left. "Previous attempt" would be wrong twice over -- the search really does run again.
    const v = presentRow(bundle({ retry_after: '2026-09-06T16:00:00Z',
      flag_reason: 'waiting for Soulseek: nothing on Soulseek matched this track closely enough; 11 more looks, one every 6 h' }), opts)
    expect(v.status).toBe('Waiting for Soulseek: nothing on Soulseek matched this track closely enough; 11 more looks, one every 6 h')
    expect(v.statusTone).toBe('amber')
    expect(v.retryInSeconds).toBe(21600)
  })
})

describe('stopping a track that is already running', () => {
  it('offers Stop while it is being identified or downloaded', () => {
    for (const state of ['identifying', 'fetching'] as const) {
      expect(presentRow(bundle({ state }), opts).action).toEqual({ label: 'Stop', kind: 'cancel' })
    }
  })

  it('does not, once the file is being checked or filed', () => {
    // Those stages move the file into the library; there is no safe moment to stop them.
    for (const state of ['verifying', 'filing'] as const) {
      expect(presentRow(bundle({ state }), opts).action).toBeNull()
    }
  })
})
