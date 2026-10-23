import { asSentence, bucketCounts, cutoffKhz, bucketOf, bulkRemoveOf, canRetry, failedSummary, gb, groupRows, matchesFilter, mmss, plural, presentRow, removeConfirmText, stageOf, stepIndex, sweptByRetryAll } from './presentation'
import type { Bundle, Playlist, Request } from './api'

const base: Request = { id: 1, created_at: '', updated_at: '', raw_text: 'Ace Ventura - Rezonate', kind: 'yt_track', state: 'queued',
  playlist_id: null, playlist_position: null, source_url: null, query_artist: 'Ace Ventura', query_title: 'Rezonate', query_version: null,
  query_duration_s: 521, chosen_candidate_id: null, catalog_track_id: null, fetch_source: null, confidence: null, flag_reason: null, error_message: null,
  attempts: 0, retry_after: null, track_id: null, failed_stage: null }
const opts = { libraryRoot: '/Users/me/Music/DJ Library', telegramAuthorized: true, now: new Date('2026-09-06T10:00:00Z'), whyOpen: false }
const bundle = (r: Partial<Request>, rest: Partial<Bundle> = {}): Bundle =>
  ({ request: { ...base, ...r }, candidates: [], catalog: null, track: null, rejection: null, ...rest })

describe('formatting', () => {
  it('cutoffKhz rounds the same way everywhere, on the 50 Hz steps cutoffs come in', () => {
    expect(cutoffKhz(15450)).toBe(15.5); expect(cutoffKhz(19950)).toBe(20); expect(cutoffKhz(16000)).toBe(16)
    for (const hz of [15450, 19950]) {
      const rejection = { id: 7, request_id: 1, reason: `cutoff at ${hz} Hz, below 18000: consistent with a lower-bitrate source`,
        bitrate_kbps: 320, cutoff_hz: hz, spectrogram_path: '/x.png', created_at: '', kind: 'quality' }
      const v = presentRow(bundle({ state: 'rejected' }, { rejection }), opts)
      const shown = String(cutoffKhz(hz))
      expect(v.status.startsWith(`Nothing above ${shown} kHz,`), v.status).toBe(true)
      expect(v.rejection!.caption.startsWith(`Nothing above ${shown} kHz.`), v.rejection!.caption).toBe(true)
      expect(v.checks).toContainEqual({ label: 'Audio only to', value: `${cutoffKhz(hz).toFixed(1)} kHz`, ok: false })
    }
  })
  it('asSentence', () => {
    expect(asSentence('cutoff at 15500 Hz, below 18000: consistent with a lower-bitrate source'))
      .toBe('Nothing above 15.5 kHz, below the 18 kHz Flackey asks of an MP3')
    expect(asSentence('the kept copy no longer passes the quality check: cutoff at 15500 Hz, below 18000: consistent with a lower-bitrate source'))
      .toBe('The kept copy no longer passes the quality check: nothing above 15.5 kHz, below the 18 kHz Flackey asks of an MP3')
    expect(asSentence('deadmau5 - Strobe is already the lossless copy')).toBe('deadmau5 - Strobe is already the lossless copy')
    expect(asSentence('lossless container but cutoff at 16000 Hz: consistent with a lossy source'))
      .toBe('Nothing above 16 kHz in a lossless file, which points to a lossy source')
    expect(asSentence('this copy is also a different recording (best score 0.64 below 0.80), so it was not filed'))
      .toBe('This copy is also a different recording (64% match), so it was not filed')
    expect(asSentence('unsupported format ogg')).toBe('Unsupported format ogg')
    expect(asSentence('Already a sentence.')).toBe('Already a sentence.')
  })
  it('plural', () => {
    expect(plural(1, 'track')).toBe('1 track'); expect(plural(0, 'track')).toBe('0 tracks')
    expect(plural(3, 'copy', 'copies')).toBe('3 copies'); expect(plural(1200, 'line')).toBe('1,200 lines')
  })
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
    // The same button in the other platforms' words; the mac wording above is the default.
    expect(presentRow(bundle({ state: 'done', track_id: 9 }, { track }), { ...opts, platform: 'windows' }).action?.label).toBe('Show in File Explorer')
    expect(presentRow(bundle({ state: 'done', track_id: 9 }, { track }), { ...opts, platform: 'linux' }).action?.label).toBe('Show in folder')
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
      { label: 'Spectrum', value: 'no edge found', ok: true }])
  })
  // 22050 is what the check answers when it found no edge -- silence answers it too -- so it is never shown
  // as "22.1 kHz", which claimed content up there that nobody had seen (issue #97).
  it('a measured edge on a filed track is evidence, not verification', () => {
    const track = { id: 9, path: '/l/a.mp3', fmt: 'mp3', bitrate_kbps: 320, cutoff_hz: 19800, file_size: 1, artist: 'A',
      title: 'T', mix_name: 'Original Mix', duration_s: 1, isrc: null, catalog_track_id: null, request_id: 1, added_at: '',
      verified_at: null, spectrogram_path: null, source: 'deezer_bot', source_fmt: null, bit_depth: null, sample_rate: 44100,
      catalog: null }
    expect(presentRow(bundle({ state: 'done', track_id: 9 }, { track }), opts).checks)
      .toEqual([{ label: 'Content to', value: '19.8 kHz', ok: true }])
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
      { id: 5, title: 'Vini Vici – The Tribe', version: 'Extended Mix', score: 92, length: '8:42', onBeatport: true, lengthNote: 'same length as the video', chosen: true, sample: null },
      { id: 6, title: 'Vini Vici – The Tribe', version: 'Original Mix', score: 74, length: '6:12', onBeatport: false, lengthNote: '2:29 shorter than the video', chosen: false, sample: null },
    ])
    expect(v.action).toEqual({ label: 'Skip this track', kind: 'cancel' })
  })
  it('rejected row explains and toggles why', () => {
    const rejection = { id: 2, request_id: 1, reason: 'Sounds like a 128 kbps upscale', bitrate_kbps: 320, cutoff_hz: 16000, spectrogram_path: '/x.png', created_at: '' }
    const v = presentRow(bundle({ state: 'rejected' }, { rejection }), opts)
    expect(v.status).toBe('Sounds like a 128 kbps upscale. Deleted, not added to your library.'); expect(v.statusTone).toBe('red')
    expect(v.rejected).toBe(true); expect(v.steps).toBeNull(); expect(v.action).toEqual({ label: 'See why', kind: 'why' })
    expect(v.rejection).toEqual({ reason: 'Sounds like a 128 kbps upscale', cutoffKhz: 16, spectrogramUrl: '/api/rejections/2/spectrogram.png',
      caption: 'Nothing above 16 kHz. That is consistent with a copy blown up from a smaller file — a real 320 kbps MP3 usually reaches about 20 kHz.' })
    expect(presentRow(bundle({ state: 'rejected' }, { rejection }), { ...opts, whyOpen: true }).action?.label).toBe('Hide why')
  })
  // Issue #61 added a second reason a file can be refused: it is genuine 320 kbps audio of the *wrong
  // recording*. The upscale caption is about the spectral check and quotes its cutoff, so saying it here
  // would tell the owner a healthy file is a fake and print a frequency that is perfectly fine as the proof.
  it('a different-recording rejection explains the real reason and quotes no cutoff', () => {
    const rejection = { id: 7, request_id: 1, reason: 'a different recording: best score 0.61 below 0.79',
      bitrate_kbps: 320, cutoff_hz: null, spectrogram_path: '/x.png', created_at: '', kind: 'different_recording' }
    const v = presentRow(bundle({ state: 'rejected' }, { rejection }), opts)
    expect(v.status).toBe('A different recording: 61% match. Deleted, not added to your library.')
    expect(v.rejection).toEqual({ reason: 'a different recording: best score 0.61 below 0.79', cutoffKhz: null,
      spectrogramUrl: '/api/rejections/7/spectrogram.png',
      caption: 'The audio itself is genuine — it is just not the recording that was asked for, so it was not kept.' })
    // Nothing failed a cutoff, so no cutoff is claimed either way round.
    expect(v.checks).toEqual([])
  })
  // Issue #92: the owner's answer to "is this really a different take?" is their ears, so the refused copy
  // is kept and played against the reference. Everything the row says about it has to agree with the fact
  // that the file is still there and a button files it.
  it('a different-recording row whose copy was kept offers both samples and does not claim a deletion', () => {
    const rejection = { id: 7, request_id: 1, reason: 'a different recording: best score 0.77 below 0.79',
      bitrate_kbps: 320, cutoff_hz: null, spectrogram_path: null, created_at: '', kind: 'different_recording',
      audio_path: '/data/rejected/req1-1.mp3' }
    const reference = { kind: 'deezer' as const, ref: '1109731', excerpt_start_s: 95 }
    const v = presentRow(bundle({ state: 'rejected' }, { rejection, reference }), opts)
    expect(v.status).toBe('A different recording: 77% match. Kept aside for you to hear, not added to your library.')
    expect(v.rejection?.caption).toMatch(/still here, so you can hear it/)
    expect(v.outcome).toEqual({ retryable: false, note: expect.stringContaining('Keep it anyway files this very copy') })
    expect(v.samples).toEqual({
      found: { key: 'found:7', label: 'the copy Flackey found', url: '/api/rejections/7/audio' },
      reference: { key: 'ref:1', label: 'the recording you asked for', url: '/api/requests/1/reference/audio' },
      video: null, kind: 'compare' })
  })
  it('a YouTube reference is a link into the owner\'s own video, at the second the excerpt came from', () => {
    // Nothing local holds that audio, so there is nothing to serve -- but the passage the fingerprint
    // compared is knowable, and sending them to it beats sending them to 0:00 of a ten-minute set.
    const rejection = { id: 7, request_id: 1, reason: 'a different recording', bitrate_kbps: 320, cutoff_hz: null,
      spectrogram_path: null, created_at: '', kind: 'different_recording', audio_path: '/data/rejected/req1-1.mp3' }
    const v = presentRow(bundle({ state: 'rejected' },
      { rejection, reference: { kind: 'youtube' as const, ref: 'dQw4w9WgXcQ', excerpt_start_s: 95 } }), opts)
    expect(v.samples?.reference).toBeNull()
    expect(v.samples?.video).toEqual({ url: 'https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=95', at: '1:35' })
  })
  it('a quality rejection whose copy was kept offers that copy alone, and says what the check is worth', () => {
    // Issue #97: a sharp edge is evidence of a lossy source, not proof, so the owner may overrule it like a
    // different recording (#92). There is nothing to compare with -- the reference is a lossy preview -- so
    // the block plays the found copy alone, and the reference is not offered even when there is one.
    const rejection = { id: 7, request_id: 1, reason: 'cutoff at 16000 Hz, below 18000: consistent with a lower-bitrate source',
      bitrate_kbps: 320, cutoff_hz: 16000, spectrogram_path: '/x.png', created_at: '', kind: 'quality',
      audio_path: '/data/rejected/req1-1.mp3' }
    const v = presentRow(bundle({ state: 'rejected' }, { rejection, reference: { kind: 'deezer' as const, ref: '1', excerpt_start_s: 0 } }), opts)
    expect(v.samples).toEqual({ found: { key: 'found:7', label: 'the copy Flackey found', url: '/api/rejections/7/audio' },
      reference: null, video: null, kind: 'listen' })
    expect(v.status).toBe('Nothing above 16 kHz, below the 18 kHz Flackey asks of an MP3. Kept aside for you to hear, not added to your library.')
    // The stored reason is the worker's, untouched: only what is drawn is reworded.
    expect(v.rejection?.reason).toBe(rejection.reason)
    // And the caption under the picture quotes the same figure as the status line, not a rounder one.
    const halfway = presentRow(bundle({ state: 'rejected' }, { rejection: { ...rejection, cutoff_hz: 15500,
      reason: 'cutoff at 15500 Hz, below 18000: consistent with a lower-bitrate source' } }), opts)
    expect(halfway.status).toMatch(/^Nothing above 15\.5 kHz,/)
    expect(halfway.rejection?.cutoffKhz).toBe(15.5)
    expect(halfway.rejection?.caption).toMatch(/^Nothing above 15\.5 kHz\./)
    expect(v.rejection?.caption).toMatch(/^Nothing above 16 kHz\. .* still here, so you can hear it and decide\.$/)
    expect(v.outcome).toEqual({ retryable: false, note: expect.stringContaining('evidence, not proof') })
  })
  it('a bitrate rejection is a measured fact: no player, no evidence-not-proof note, and the plain wording', () => {
    // Refused on bitrate, so it never reached the spectrum and carries no cutoff. Even a row that claims a
    // kept copy is not offered one: the worker would refuse to file it.
    const rejection = { id: 7, request_id: 1, reason: 'bitrate 128 kbps below 320', bitrate_kbps: 128, cutoff_hz: null,
      spectrogram_path: '/x.png', created_at: '', kind: 'quality', audio_path: '/data/rejected/req1-1.mp3' }
    const v = presentRow(bundle({ state: 'rejected' }, { rejection }), opts)
    expect(v.samples).toBeNull()
    expect(v.status).toBe('Only 128 kbps, below the 320 kbps Flackey keeps. Deleted, not added to your library.')
    expect(v.outcome?.note).not.toMatch(/evidence, not proof/)
    expect(v.outcome?.note).toMatch(/checked, failed and deleted/)
    expect(v.rejection?.caption).toBe('The file did not pass the quality check.')
  })
  it('a quality rejection with nothing kept has nothing to listen to', () => {
    // A row written before copies were kept, or a format Flackey could not file: no player, no promise.
    const rejection = { id: 7, request_id: 1, reason: 'Sounds like a 128 kbps upscale', bitrate_kbps: 320,
      cutoff_hz: 16000, spectrogram_path: '/x.png', created_at: '' }
    const v = presentRow(bundle({ state: 'rejected' }, { rejection, reference: { kind: 'deezer' as const, ref: '1', excerpt_start_s: 0 } }), opts)
    expect(v.samples).toBeNull()
    expect(v.status).toMatch(/Deleted, not added/)
  })
  it('a track kept after failing the quality check says so instead of passing', () => {
    const track = { id: 9, path: '/l/a.mp3', fmt: 'mp3', bitrate_kbps: 320, cutoff_hz: 16000, file_size: 1, artist: 'A',
      title: 'T', mix_name: 'Original Mix', duration_s: 1, isrc: null, catalog_track_id: null, request_id: 1, added_at: '',
      verified_at: null, spectrogram_path: null, source: 'deezer_bot', source_fmt: null, bit_depth: null, sample_rate: 44100,
      catalog: null, evidence: [{ kind: 'quality_check', value: { status: 'failed', kept_by: 'owner' } }] }
    expect(presentRow(bundle({ state: 'done', track_id: 9 }, { track }), opts).checks).toEqual([
      { label: 'Quality check', value: 'kept anyway', ok: false },
      { label: 'Audio only to', value: '16.0 kHz', ok: false }])
  })
  it('a track whose version nothing confirmed says so, and a confirmed one says nothing extra', () => {
    // Issue #172: the fingerprint proves the recording, not the remaster, so an unconfirmed one is flagged.
    const track = (status: string) => ({ id: 9, path: '/l/a.aiff', fmt: 'aiff', bitrate_kbps: 1411, cutoff_hz: 22050,
      file_size: 1, artist: 'A', title: 'T', mix_name: 'Remastered 2024', duration_s: 1, isrc: null, catalog_track_id: null,
      request_id: 1, added_at: '', verified_at: null, spectrogram_path: null, source: 'slskd', source_fmt: 'flac',
      bit_depth: 16, sample_rate: 44100, catalog: null, evidence: [{ kind: 'version_check', value: { status, reason: 'r' } }] })
    expect(presentRow(bundle({ state: 'done', track_id: 9 }, { track: track('unconfirmed') }), opts).checks).toEqual([
      { label: 'Version', value: 'unconfirmed', ok: false },
      { label: 'Spectrum', value: 'no edge found', ok: true }])
    expect(presentRow(bundle({ state: 'done', track_id: 9 }, { track: track('confirmed') }), opts).checks).toEqual([
      { label: 'Spectrum', value: 'no edge found', ok: true }])
  })
  it('an extended mix the owner asked for is named on the row, and a miss says what came instead', () => {
    // Issue #187: the record says radio edit, the file is the extended mix, and the row names the file
    const track = (mix_name: string, evidence: { kind: string; value: Record<string, unknown> }[]) => ({ id: 9, path: '/l/a.aiff', fmt: 'aiff', bitrate_kbps: 1411,
      cutoff_hz: 22050, file_size: 1, artist: 'Calvin Harris', title: 'Feel So Close', mix_name, duration_s: 330, isrc: null,
      catalog_track_id: 3, request_id: 1, added_at: '', verified_at: null, spectrogram_path: null, source: 'soulseek',
      source_fmt: 'flac', bit_depth: 16, sample_rate: 44100, catalog: null, evidence })
    const catalog = { id: 3, isrc: null, artist: 'Calvin Harris', title: 'Feel So Close', mix_name: 'Original Mix', label: 'Ultra',
      genre: 'Dance', sub_genre: null, catalog_number: null, release_name: null, release_date: null, bpm: null, key: null,
      duration_ms: 206000, artwork_url: null }
    const got = presentRow(bundle({ state: 'done', track_id: 9 }, { catalog, track: track('Extended Mix',
      [{ kind: 'extended', value: { sought: true, found: true, mix_name: 'Extended Mix' } }]) }), opts)
    expect(got.version).toBe('Extended Mix'); expect(got.extendedNote).toBeNull()
    expect(got.checks).toContainEqual({ label: 'Extended mix', value: 'found', ok: true })
    const missed = presentRow(bundle({ state: 'done', track_id: 9 }, { catalog, track: track('Original Mix',
      [{ kind: 'extended', value: { sought: true, found: false, mix_name: 'Original Mix' } }]) }), opts)
    expect(missed.version).toBe('Original Mix')
    expect(missed.extendedNote).toBe('No extended mix found, so this is the Original Mix.')
    expect(missed.checks.map(c => c.label)).not.toContain('Extended mix')
    // the setting off: no evidence, nothing extra on the row
    const off = presentRow(bundle({ state: 'done', track_id: 9 }, { catalog, track: track('Original Mix', []) }), opts)
    expect(off.extendedNote).toBeNull(); expect(off.checks.map(c => c.label)).not.toContain('Extended mix')
  })
  it('a longer version found after filing is offered on the Done row, and named once it is swapped in', () => {
    const track = (evidence: { kind: string; value: Record<string, unknown> }[]) => ({ id: 9, path: '/l/a.aiff', fmt: 'aiff',
      bitrate_kbps: 1411, cutoff_hz: 22050, file_size: 1, artist: 'Calvin Harris', title: 'Feel So Close', mix_name: 'Radio Edit',
      duration_s: 206, isrc: null, catalog_track_id: null, request_id: 1, added_at: '', verified_at: null, spectrogram_path: null,
      source: 'soulseek', source_fmt: 'flac', bit_depth: 16, sample_rate: 44100, catalog: null, evidence })
    const offer = (value: Record<string, unknown>, state = 'done') =>
      presentRow(bundle({ state: state as never, track_id: 9 }, { track: track([{ kind: 'extended_offer', value }]) }), opts).extendedOffer
    expect(offer({ status: 'available', length_s: 400, mix_name: 'Extended Mix' })).toEqual({ state: 'offer',
      label: 'Extended mix available (6:40)',
      tip: 'We found a longer extended mix of this track — easier to mix in and out. Get it instead of this version?' })
    expect(offer({ status: 'available', length_s: 444, mix_name: 'Club Mix' })?.label).toBe('Club mix available (7:24)')
    expect(offer({ status: 'available', length_s: 444, mix_name: 'Club Mix' })?.tip).toMatch(/^We found a longer club mix/)
    expect(offer({ status: 'available', length_s: 444, mix_name: 'PvD E-Werk Club Mix' })?.label).toBe('PvD E-Werk Club Mix available (7:24)')
    expect(offer({ status: 'swapped', length_s: 400, mix_name: 'Extended Mix', was: 'Radio Edit' })).toEqual({ state: 'swapped',
      label: 'Swapped in for the Radio Edit.', tip: '' })
    // still being looked for, nothing found, or a no: nothing on the row
    for (const status of ['pending', 'none', 'kept']) expect(offer({ status })).toBeNull()
    expect(offer({ status: 'available', length_s: 400 }, 'duplicate')).toBeNull()
  })
  it('a copy that is gone leaves the reference playable on its own, rather than hiding the whole block', () => {
    const rejection = { id: 7, request_id: 1, reason: 'a different recording', bitrate_kbps: 320, cutoff_hz: null,
      spectrogram_path: null, created_at: '', kind: 'different_recording', audio_path: null }
    const v = presentRow(bundle({ state: 'rejected' },
      { rejection, reference: { kind: 'deezer' as const, ref: '1109731', excerpt_start_s: null } }), opts)
    expect(v.samples?.found).toBeNull()
    expect(v.samples?.reference?.url).toBe('/api/requests/1/reference/audio')
    // And with no copy there is nothing to keep, so the row falls back to the plain refusal wording.
    expect(v.status).toMatch(/Deleted, not added/)
  })
  it('a different-recording row with neither side to play draws no block at all', () => {
    const rejection = { id: 7, request_id: 1, reason: 'a different recording', bitrate_kbps: 320, cutoff_hz: null,
      spectrogram_path: null, created_at: '', kind: 'different_recording', audio_path: null }
    expect(presentRow(bundle({ state: 'rejected' }, { rejection }), opts).samples).toBeNull()
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
      .toEqual([{ label: 'Content to', value: '20.5 kHz', ok: true }])
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
  it('offers a failure report on the failures the pipeline reached, not on a track the owner stopped', () => {
    const reportable = (state: Request['state']) => presentRow(bundle({ state }), opts).reportable
    expect(['rejected', 'not_found', 'error'].every(s => reportable(s as Request['state']))).toBe(true)
    expect(['cancelled', 'done', 'duplicate', 'queued', 'fetching', 'awaiting_review']
      .some(s => reportable(s as Request['state']))).toBe(false)
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
    // Row 6 is queued with a retry_after -- parked on a backoff, doing nothing. It used to be counted as
    // in progress, which is the claim the segmented bar exists to correct, so it is `waiting` now.
    expect(g[0].summary).toEqual({ filed: 1, total: 4, needsChoice: 1, rejected: 1, inFlight: 0, waiting: 1,
      failed: 1, pct: 25, failedStages: { search: 0, choose: 0, download: 0, verify: 1, waiting: 0, stopped: 0, unknown: 0 } })
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
    expect(failedOnly[0].summary).toEqual({ filed: 1, total: 3, needsChoice: 1, rejected: 1, inFlight: 0, waiting: 0,
      failed: 1, pct: 33, failedStages: { search: 0, choose: 0, download: 0, verify: 1, waiting: 0, stopped: 0, unknown: 0 } })  // summary unaffected by the filter

    const progressOnly = groupRows(bundles, playlists, opts, 'progress')
    expect(progressOnly.map(g => g.name)).toEqual(['Single tracks'])
    expect(progressOnly[0].rows.map(r => r.id)).toEqual([2])

    expect(groupRows(bundles, playlists, opts, 'all')).toHaveLength(2)
  })

  it('counts the whole batch in the summary even when the tab is showing a slice of it', () => {
    // The Downloads tab drops every finished row before it gets here, so a summary built from the rows
    // reads "0 filed" on a playlist that is in fact half filed. The bar is about the batch, not the tab.
    const all = [
      bundle({ id: 1, playlist_id: 7, state: 'done' }), bundle({ id: 2, playlist_id: 7, state: 'done' }),
      bundle({ id: 3, playlist_id: 7, state: 'rejected' }), bundle({ id: 4, playlist_id: 7, state: 'fetching' }),
      bundle({ id: 5, playlist_id: 7, state: 'queued', retry_after: '2026-09-06T10:15:00+00:00' }),
    ]
    const active = all.filter(b => b.request.state === 'fetching' || b.request.state === 'queued')
    const g = groupRows(active, playlists, opts, 'all', all)
    expect(g[0].rows.map(r => r.id)).toEqual([5, 4])
    expect(g[0].summary).toMatchObject({ filed: 2, total: 5, inFlight: 1, waiting: 1, failed: 1, pct: 40 })
    // Every segment of the bar accounted for: nothing falls into an unexplained gap at the end.
    const s = g[0].summary
    expect(s.filed + s.inFlight + s.needsChoice + s.waiting + s.failed).toBe(s.total)
  })

  it('gives each batch its own totals, not the whole app\'s', () => {
    /* The summary reads the unfiltered list, and the obvious way to wire that up -- hand `summaryOf` the
       whole of `all` -- is indistinguishable from the right one until a second playlist exists. Then every
       bar on screen draws the same shape and each one is wrong. Two batches here, deliberately different
       sizes, so that mistake cannot pass. */
    const all = [
      bundle({ id: 1, playlist_id: 7, state: 'done' }), bundle({ id: 2, playlist_id: 7, state: 'done' }),
      bundle({ id: 3, playlist_id: 7, state: 'fetching' }),
      bundle({ id: 4, playlist_id: 8, state: 'done' }), bundle({ id: 5, playlist_id: 8, state: 'error' }),
      bundle({ id: 6, playlist_id: 8, state: 'error' }), bundle({ id: 7, playlist_id: 8, state: 'fetching' }),
    ]
    const two = [...playlists, { id: 8, source_url: 'u', name: 'Goa Trance Classics', created_at: '', updated_at: '', track_ids: [], file: '' }]
    const active = all.filter(b => b.request.state === 'fetching')
    const g = groupRows(active, two, opts, 'all', all)
    const by = new Map(g.map(x => [x.name, x.summary]))
    expect(by.get('Progressive Psy Set 2026')).toMatchObject({ filed: 2, total: 3, failed: 0 })
    expect(by.get('Goa Trance Classics')).toMatchObject({ filed: 1, total: 4, failed: 2 })
  })

  it('filters on the stage axis as readily as the bucket axis, still grouping by playlist only', () => {
    const bundles = [
      bundle({ id: 1, playlist_id: 7, state: 'identifying' }),
      bundle({ id: 2, playlist_id: 7, state: 'queued', retry_after: '2026-09-06T10:15:00+00:00' }),
      bundle({ id: 3, playlist_id: 7, state: 'error', failed_stage: 'download' }),
      bundle({ id: 4, state: 'queued', retry_after: '2026-09-06T10:15:00+00:00' }),
    ]
    expect(groupRows(bundles, playlists, opts, 'waiting').map(g => [g.name, g.rows.map(r => r.id)]))
      .toEqual([['Progressive Psy Set 2026', [2]], ['Single tracks', [4]]])
    expect(groupRows(bundles, playlists, opts, 'download')[0].rows.map(r => r.id)).toEqual([3])
    expect(groupRows(bundles, playlists, opts, 'search')[0].rows.map(r => r.id)).toEqual([1])
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
    expect(FAILED_STATES.filter(canRetry)).toEqual(['not_found', 'error', 'cancelled'])
  })

  it('separates the rows Retry all sweeps from the ones only their own button moves', () => {
    // A track the owner stopped has a Try again and is still left out of the sweep (issue #92). Two
    // different questions, so two different predicates, and the row carries the narrower answer.
    for (const s of FAILED_STATES) {
      expect(presentRow(bundle({ state: s }), opts).sweepable, s).toBe(sweptByRetryAll(s))
    }
    expect(FAILED_STATES.filter(sweptByRetryAll)).toEqual(['not_found', 'error'])
    expect(sweptByRetryAll('cancelled')).toBe(false)
    expect(canRetry('cancelled')).toBe(true)
  })

  it('counts the tries an errored row had instead of claiming several', () => {
    expect(presentRow(bundle({ state: 'error', attempts: 1 }), opts).outcome!.note).toMatch(/^Flackey tried this once and stopped\. Try again/)
    expect(presentRow(bundle({ state: 'error', attempts: 0 }), opts).outcome!.note).toMatch(/^Flackey tried this once and stopped\./)
    expect(presentRow(bundle({ state: 'error', attempts: 3 }), opts).outcome!.note).toMatch(/^Flackey tried this 3 times and stopped\./)
    expect(presentRow(bundle({ state: 'error', attempts: 1 }), opts).outcome!.note).not.toMatch(/several/)
  })

  it('says Try again and the count of looks once each when the message already counts them', () => {
    const v = presentRow(bundle({ state: 'error', attempts: 15, error_message: 'the people who had it are busy and '
      + 'their queue has not reached us yet; looked 12 times over 72 h. Press Try again to search once more' }), opts)
    expect(v.status).toBe('Failed — the people who had it are busy and their queue has not reached us yet; looked 12 times over 72 h')
    expect(v.outcome!.note).toBe('Flackey stopped trying. Try again starts the search over from the beginning, Soulseek included.')
    expect((v.status + v.outcome!.note).match(/Try again/g)).toHaveLength(1)
    expect(v.outcome!.note).not.toMatch(/15/)
    // Advice that says *when* is not a repeat, and stays.
    const unchecked = presentRow(bundle({ state: 'error', attempts: 1,
      error_message: 'the recording could not be checked acoustically (no reference). Try again once it can be checked' }), opts)
    expect(unchecked.status).toMatch(/Try again once it can be checked$/)
  })

  it('points a final row at the one thing that does work: submitting the link again', () => {
    expect(presentRow(bundle({ state: 'rejected' }), opts).outcome!.note).toMatch(/paste the link again/i)
  })

  it('tells a stopped row its Try again works and that Retry all will not press it', () => {
    const note = presentRow(bundle({ state: 'cancelled' }), opts).outcome!.note
    expect(note).toMatch(/Try again/)
    expect(note).toMatch(/Retry all leaves stopped tracks alone/)
    expect(note).not.toMatch(/paste the link again/i)
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
      .toBe('1 of these 5 cannot be tried again — 1 failed the quality check. That row says what to do '
            + 'instead. 2 of these you stopped yourself, so Retry all leaves them out — press Try again '
            + 'on a row to start that one again.')
  })

  it('explains a Retry all of nothing even when no row is final', () => {
    // The worst version of the gap: seventeen live Try again buttons under a disabled "Retry all 0".
    // Nothing here is final, so the old summary returned null and the owner was left with two numbers.
    expect(failedSummary([failed(1, 'error'), failed(2, 'cancelled'), failed(3, 'cancelled')]))
      .toBe('2 of these you stopped yourself, so Retry all leaves them out — press Try again on a row '
            + 'to start that one again.')
    expect(failedSummary([failed(1, 'cancelled')]))
      .toBe('One of these you stopped yourself, so Retry all leaves it out — press Try again on the row '
            + 'to start it again.')
  })

  it('says so plainly when the button is disabled because nothing here can move', () => {
    expect(failedSummary([failed(1, 'rejected'), failed(2, 'rejected')]))
      .toBe('Nothing here can be tried again — 2 failed the quality check. Each row says what to do instead.')
  })

  it('counts the two refusals apart, because only one of them is about quality', () => {
    // One `rejected` state, two entirely different things to have happened. Saying "3 failed the quality
    // check" over a row that offers to file the file would be the summary contradicting the row (#92).
    const different = bundle({ id: 3, state: 'rejected' }, { rejection: { id: 3, request_id: 3,
      reason: 'a different recording', bitrate_kbps: 320, cutoff_hz: null, spectrogram_path: null,
      created_at: '', kind: 'different_recording' } })
    expect(failedSummary([failed(1, 'rejected'), failed(2, 'rejected'), different]))
      .toBe('Nothing here can be tried again — 2 failed the quality check, 1 turned out to be a different '
            + 'recording. Each row says what to do instead.')
  })

  it('stays silent when there is no gap to explain', () => {
    // Badge and button already agree; a line saying "0 of these" is noise above a list that is fine.
    expect(failedSummary([failed(1, 'error'), failed(2, 'not_found')])).toBeNull()
    expect(failedSummary([])).toBeNull()
  })

  it('counts only failures, so a History tab full of finished tracks does not inflate it', () => {
    expect(failedSummary([failed(1, 'done'), failed(2, 'duplicate'), failed(3, 'rejected')]))
      .toBe('Nothing here can be tried again — 1 failed the quality check. That row says what to do instead.')
  })
})

describe('bucketCounts', () => {
  it('counts bundles per bucket plus a running total', () => {
    const counts = bucketCounts([
      bundle({ id: 1, state: 'queued' }), bundle({ id: 2, state: 'fetching' }), bundle({ id: 3, state: 'awaiting_review' }),
      bundle({ id: 4, state: 'done' }), bundle({ id: 5, state: 'duplicate' }), bundle({ id: 6, state: 'rejected' }),
    ])
    expect(counts).toEqual({ all: 6, progress: 2, needs: 1, done: 2, failed: 1,
      search: 1, choose: 1, download: 1, verify: 1, waiting: 0, stopped: 0, unknown: 0 })
  })
})

describe('presentRow bucket/removable', () => {
  it('marks done, failed and stoppable rows removable; rows being checked or filed are not', () => {
    expect(presentRow(bundle({ state: 'done' }), opts)).toMatchObject({ bucket: 'done', removable: true })
    expect(presentRow(bundle({ state: 'rejected' }), opts)).toMatchObject({ bucket: 'failed', removable: true })
    // Live rows the worker can stop: Remove stops them and forgets them in one press.
    for (const state of ['queued', 'identifying', 'fetching'] as const) {
      expect(presentRow(bundle({ state }), opts)).toMatchObject({ bucket: 'progress', removable: true })
    }
    expect(presentRow(bundle({ state: 'awaiting_review' }), opts)).toMatchObject({ bucket: 'needs', removable: true })
    for (const state of ['verifying', 'filing'] as const) {
      expect(presentRow(bundle({ state }), opts)).toMatchObject({ bucket: 'progress', removable: false })
    }
  })
})

describe('Remove these', () => {
  it('takes the removable rows the filter shows, and counts the live ones it will stop', () => {
    const bundles = [
      bundle({ id: 1, retry_after: '2026-09-06T16:00:00Z', flag_reason: 'waiting for Soulseek: x' }),
      bundle({ id: 2, retry_after: '2026-09-06T16:00:00Z', flag_reason: 'waiting for Soulseek: x' }),
      bundle({ id: 3, state: 'fetching' }),
      bundle({ id: 4, state: 'verifying' }),
    ]
    expect(bulkRemoveOf(groupRows(bundles, [], opts, 'waiting'))).toEqual({ ids: [1, 2], live: 2 })
    // Verifying is on screen under All but is not taken: its file is moving into the library.
    expect(bulkRemoveOf(groupRows(bundles, [], opts, 'all'))).toEqual({ ids: [3, 2, 1], live: 3 })
    expect(bulkRemoveOf(groupRows([bundle({ id: 5, state: 'error' }), bundle({ id: 6, state: 'done' })], [], opts))).toEqual({ ids: [6, 5], live: 0 })
  })

  it('says how many it removes, which it stops, and that library files stay', () => {
    expect(removeConfirmText({ ids: [1, 2, 3, 4], live: 4 }))
      .toBe('Remove these 4 tracks from the list? They will be stopped first. Files already in your library stay where they are.')
    expect(removeConfirmText({ ids: [1, 2, 3], live: 1 }))
      .toBe('Remove these 3 tracks from the list? 1 still in progress will be stopped first. Files already in your library stay where they are.')
    expect(removeConfirmText({ ids: [1], live: 0 }))
      .toBe('Remove this track from the list? Files already in your library stay where they are.')
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

  it('says Soulseek found no copies when the search came back empty, and nothing matched when it did not', () => {
    expect(presentRow(filed({}, { outcome: 'no_pick', no_results: true }), opts).fallback?.reason)
      .toBe('Kept the matched MP3 copy because Soulseek found no copies of this track.')
    expect(presentRow(filed({}, { outcome: 'no_pick', no_results: false }), opts).fallback?.reason)
      .toBe('Kept the matched MP3 copy because nothing on Soulseek matched this track closely enough.')
    // An older server never says: the old words, which were the only ones then.
    expect(presentRow(filed({}, { outcome: 'no_pick' }), opts).fallback?.reason)
      .toBe('Kept the matched MP3 copy because nothing on Soulseek matched this track closely enough.')
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

  it('keeps its countdown when it is on a retry backoff, and can be stopped there', () => {
    const v = presentRow(bundle({ id: 4, retry_after: '2026-09-06T10:01:00Z',
      flag_reason: 'Beatport unreachable, will retry' }), opts)
    expect(v.status).toBe('Previous attempt: Beatport unreachable')
    expect(v.retryInSeconds).toBe(60)
    expect(v.action).toEqual({ label: 'Stop', kind: 'cancel' })
    expect(v.removable).toBe(true)
  })

  it('shortens the verbose Soulseek fallback warning without losing its meaning', () => {
    const v = presentRow(bundle({ retry_after: '2026-09-06T10:00:25+00:00',
      flag_reason: 'no way to fetch this track: nothing on Soulseek matched this track closely enough, Deezer offered nothing to fall back on' }), opts)
    expect(v.status).toBe('Previous attempt: No Soulseek match; nothing on Deezer to fall back on')
    expect(v.retryInSeconds).toBe(25)
    // The worker joins the two clauses with a semicolon now; rows written before that keep the comma
    const joined = presentRow(bundle({ retry_after: '2026-09-06T10:00:25+00:00',
      flag_reason: 'no way to fetch this track: nothing on Soulseek matched this track closely enough; Deezer offered nothing to fall back on, will retry' }), opts)
    expect(joined.status).toBe('Previous attempt: No Soulseek match; nothing on Deezer to fall back on')
    // An empty search is its own sentence, shortened the same way.
    const empty = presentRow(bundle({ retry_after: '2026-09-06T10:00:25+00:00',
      flag_reason: 'no way to fetch this track: Soulseek found no copies of this track; Deezer offered nothing to fall back on' }), opts)
    expect(empty.status).toBe('Previous attempt: No copies on Soulseek; nothing on Deezer to fall back on')
  })

  it('shows the wait for Soulseek as itself rather than as a previous attempt', () => {
    // The worker words this one for the owner already (issue #74): what is being waited for and how many
    // looks are left. "Previous attempt" would be wrong twice over -- the search really does run again.
    const v = presentRow(bundle({ retry_after: '2026-09-06T16:00:00Z',
      flag_reason: 'waiting for Soulseek: nothing on Soulseek matched this track closely enough; 11 more looks, one every 6 h' }), opts)
    expect(v.status).toBe('Waiting for Soulseek: nothing on Soulseek matched this track closely enough; 11 more looks, one every 6 h')
    expect(v.statusTone).toBe('amber')
    expect(v.retryInSeconds).toBe(21600)
    // A user had four of these, six hours each, with no Stop and no Remove anywhere on the row.
    expect(v.action).toEqual({ label: 'Stop', kind: 'cancel' })
    expect(v.removable).toBe(true)
  })

  it('offers Stop on a waiting row even while its source is disconnected', () => {
    const v = presentRow(bundle({ retry_after: '2026-09-06T16:00:00Z', flag_reason: 'waiting for Soulseek: x' }),
      { ...opts, telegramAuthorized: false, soulseekConnected: false })
    expect(v.status).toBe('Paused — connect a source to start')
    expect(v.action).toEqual({ label: 'Stop', kind: 'cancel' })
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

/* Issue #60: one vocabulary for "where is it" and "where did it stop". The chips, the segmented bar and
   the stage tag on a failed row all read from `stageOf`, so it is pinned here per state rather than at
   each of the three places that draw it. */
describe('stageOf', () => {
  const req = (over: Partial<Request>) => bundle(over).request

  it('names the rung a live request is standing on', () => {
    expect(stageOf(req({ state: 'queued' }))).toBe('search')
    expect(stageOf(req({ state: 'identifying' }))).toBe('search')
    expect(stageOf(req({ state: 'awaiting_review' }))).toBe('choose')
    expect(stageOf(req({ state: 'fetching' }))).toBe('download')
    expect(stageOf(req({ state: 'verifying' }))).toBe('verify')
    expect(stageOf(req({ state: 'filing' }))).toBe('verify')
  })

  it('calls a re-queued request parked, not searching', () => {
    // The 41 rows the issue was opened about: `queued` with a `retry_after`, which the chips counted as
    // in progress while they sat on a Soulseek backoff doing nothing.
    expect(stageOf(req({ state: 'queued', retry_after: '2026-09-06T10:15:00+00:00' }))).toBe('waiting')
  })

  it('reads where a failed run stopped off the column, and admits it when there is none', () => {
    expect(stageOf(req({ state: 'error', failed_stage: 'download' }))).toBe('download')
    expect(stageOf(req({ state: 'error', failed_stage: 'verify' }))).toBe('verify')
    // Rows written before the column existed, and anything the backend might one day write that this
    // vocabulary does not know: an honest gap, never a chip that counts nothing.
    expect(stageOf(req({ state: 'error', failed_stage: null }))).toBe('unknown')
    expect(stageOf(req({ state: 'error' }))).toBe('unknown')
    expect(stageOf(req({ state: 'error', failed_stage: 'teleporting' }))).toBe('unknown')
  })

  it('says where the three self-describing failures stopped without needing a column', () => {
    expect(stageOf(req({ state: 'not_found' }))).toBe('search')
    expect(stageOf(req({ state: 'rejected' }))).toBe('verify')
    expect(stageOf(req({ state: 'cancelled' }))).toBe('stopped')
  })

  it('has nothing to say about a request that finished', () => {
    expect(stageOf(req({ state: 'done' }))).toBeNull()
    expect(stageOf(req({ state: 'duplicate' }))).toBeNull()
  })
})

describe('matchesFilter', () => {
  const req = (over: Partial<Request>) => bundle(over).request

  it('matches on either axis, because the two unions share no value', () => {
    const parked = req({ state: 'queued', retry_after: '2026-09-06T10:15:00+00:00' })
    expect(matchesFilter(parked, 'all')).toBe(true)
    expect(matchesFilter(parked, 'progress')).toBe(true)     // bucket axis
    expect(matchesFilter(parked, 'waiting')).toBe(true)      // stage axis
    expect(matchesFilter(parked, 'search')).toBe(false)      // parked is not searching
    expect(matchesFilter(req({ state: 'identifying' }), 'search')).toBe(true)
    expect(matchesFilter(req({ state: 'error', failed_stage: 'download' }), 'download')).toBe(true)
    expect(matchesFilter(req({ state: 'error', failed_stage: 'download' }), 'failed')).toBe(true)
    expect(matchesFilter(req({ state: 'error', failed_stage: 'download' }), 'verify')).toBe(false)
  })
})

describe('bucketCounts with stages', () => {
  it('counts buckets and stages in one walk, and drops the null stage of a finished row', () => {
    const counts = bucketCounts([
      bundle({ id: 1, state: 'identifying' }), bundle({ id: 2, state: 'fetching' }),
      bundle({ id: 3, state: 'awaiting_review' }), bundle({ id: 4, state: 'done' }),
      bundle({ id: 5, state: 'queued', retry_after: '2026-09-06T10:15:00+00:00' }),
      bundle({ id: 6, state: 'error', failed_stage: 'download' }), bundle({ id: 7, state: 'error' }),
      bundle({ id: 8, state: 'cancelled' }),
    ])
    expect(counts).toEqual({
      all: 8, progress: 3, needs: 1, done: 1, failed: 3,
      search: 1, choose: 1, download: 2, verify: 0, waiting: 1, stopped: 1, unknown: 1,
    })
  })
})

describe('a parked row says where it picks up', () => {
  it('rings the rung a re-queued request resumes at, where a fresh one is all pending', () => {
    const parked = presentRow(bundle({ state: 'queued', retry_after: '2026-09-06T10:15:00+00:00', attempts: 2 }), opts)
    expect(parked.steps!.map(s => s.state)).toEqual(['paused', 'pending', 'pending', 'pending'])
    expect(presentRow(bundle({ state: 'queued' }), opts).steps!.map(s => s.state))
      .toEqual(['pending', 'pending', 'pending', 'pending'])
  })
})
