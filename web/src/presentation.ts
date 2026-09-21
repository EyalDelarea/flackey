import type { Bundle, Candidate, FetchProgress, Playlist, Request, RequestState } from './api'
import { spectrogramUrl } from './api'

export const STEPS = ['Search', 'Choose', 'Download', 'Verify', 'Done'] as const
/* A rung earns its place if the request can stop on it: Search ends in not_found, Choose waits on the owner
   indefinitely, Download ends in error, Verify ends in rejected. Filing ends in nothing -- a name clash with
   a track already there is a duplicate, which is a success -- so it got a rung the owner only ever watched
   flash past. The ladder's job is saying how far a track got when it stopped, and File never stopped one. */
/** Hover text for each rung. Here rather than in Stepper.tsx for the same reason the "step N of 6" counter
 *  went: two places describing one ladder disagreed the moment the map below changed. */
export const STEP_TIPS: Record<string, string> = {
  Search: 'Working out which track this is. Beatport gives the official title, remix name and release; Deezer says who has a copy.',
  Choose: 'More than one copy could be the right one and none of them won outright, so it is waiting on you. Most tracks skip this.',
  Download: "Pulling the file down. Soulseek first for a lossless copy, which can mean waiting in a stranger's queue; Deezer as the fallback. A Soulseek copy is also checked and fingerprinted here, before it moves on.",
  Verify: 'Reading the spectrogram to make sure the file is really lossless. An MP3 re-wrapped as FLAC has a hard cutoff around 16 kHz that real music never has.',
  Done: 'Tagged with artist, title, remix and artwork, and filed under the artist\'s folder. No BPM or key — Rekordbox works those out on import.',
}
export type Dot = 'done' | 'current' | 'pending'
/** One rung of the ladder: the name and the state are rendered together, never as two separate widgets. */
export interface StepView { name: string; state: Dot }
/** Evidence that the file is what it claims to be - the fingerprint match and the spectrogram cutoff. */
export interface CheckView { label: string; value: string; ok: boolean }
export type Tone = 'muted' | 'amber' | 'green' | 'red' | 'text'
export type Bucket = 'progress' | 'needs' | 'done' | 'failed'
export interface RowAction { label: string; kind: 'reveal' | 'retry' | 'why' | 'cancel' | 'remove'; path?: string }
export interface CandidateView { id: number; title: string; version: string; score: number | null; length: string; onBeatport: boolean; lengthNote: string; chosen: boolean }
export interface RejectionView { reason: string; cutoffKhz: number | null; caption: string; spectrogramUrl: string | null }
/** Why a row is sitting in a list called "Failed" and whether it can come back. Present on failed rows
 *  only -- the ones the owner is looking at when they ask that question. */
export interface OutcomeView { retryable: boolean; note: string }
export interface RowView {
  id: number; title: string; version: string | null; status: string; statusTone: Tone
  steps: StepView[] | null; tag: string | null; dimmed: boolean; washed: boolean
  action: RowAction | null; candidates: CandidateView[] | null; rejection: RejectionView | null
  artworkUrl: string | null; rejected: boolean; retryInSeconds: number | null; bucket: Bucket; removable: boolean
  formatLabel: string | null; checks: CheckView[]
  progress: ProgressView | null; fallback: FallbackView | null; outcome: OutcomeView | null
}
/** A live transfer's position. Every downloading row has one of these -- they all run at once. */
export interface ProgressView { pct: number | null; label: string }
/** This track is on the lossy Deezer copy and why, so a miss is visible rather than silent. */
export interface FallbackView { label: string; reason: string }
export interface GroupSummary { filed: number; total: number; needsChoice: number; rejected: number; inFlight: number; pct: number }
export interface GroupView { key: string; name: string; summary: GroupSummary; rows: RowView[]; beatportDown: { seconds: number; requestIds: number[] } | null }
export interface PresentOpts {
  libraryRoot: string; telegramAuthorized: boolean; now: Date; whyOpen: boolean
  soulseekConnected?: boolean
  /** One entry per track currently transferring; the row picks out its own by request id. */
  fetchProgress?: FetchProgress[] | null
}

export const mmss = (s: number | null | undefined) => s == null ? '?:??' : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
export const gb = (bytes: number) => bytes >= 1e9 ? `${(bytes / 1e9).toFixed(1)} GB` : `${Math.round(bytes / 1e6)} MB`
// `done` is the last index, not one past it: the old value indexed past STEPS, so the Done rung could never
// read as reached. `filing` rides on Verify rather than on Done -- it has no rung of its own now, and a Done
// rung pulsing amber on a track that is not filed yet claims the very thing the ladder exists to report.
// So on a Soulseek row -- which skips VERIFYING outright, having verified inside the attempt -- Verify is
// amber for the seconds the file is being tagged and moved. The row's own words say "Tagging and filing"
// while it is, and the rung behind it has been reached; the alternative reads as finished when it is not.
const STEP_OF: Partial<Record<RequestState, number>> = { queued: 0, identifying: 0, awaiting_review: 1, fetching: 2, verifying: 3, filing: 3, done: 4, duplicate: 4 }
const COMPLETE: RequestState[] = ['done', 'duplicate']
const SOURCE_LABEL: Record<string, string> = { soulseek: 'Soulseek', deezer: 'Deezer', deezer_bot: 'Deezer' }
export const sourceLabel = (s: string | null | undefined) => s ? SOURCE_LABEL[s] ?? s : null
export const stepIndex = (state: RequestState): number | null => STEP_OF[state] ?? null
const BUCKET_OF: Record<RequestState, Bucket> = {
  queued: 'progress', identifying: 'progress', fetching: 'progress', verifying: 'progress', filing: 'progress',
  awaiting_review: 'needs',
  done: 'done', duplicate: 'done',
  rejected: 'failed', not_found: 'failed', error: 'failed', cancelled: 'failed',
}
export const bucketOf = (state: RequestState): Bucket => BUCKET_OF[state]

/* Whether anything can still move a request out of the state it is in. A plain `Record`, not a Set of the
   interesting ones: a thirteenth state then fails the build here instead of quietly classifying itself as
   final and appearing in the Failed list with nothing to say about it.
   - `open`: the pipeline still has it, so the question does not arise.
   - `retryable`: stopped, but `Worker.retry` takes it back and re-queues it. Exactly `RETRYABLE_STATES` in
     src/flackey/models.py -- `test_failed_states_match_the_ui` fails if these two lists disagree, because a
     button offering a retry the worker refuses is the confusion this whole table exists to end.
   - `final`: nothing in the app moves it again. `done` and `duplicate` are final because they succeeded;
     `rejected` and `cancelled` because a verdict was reached, not because the app ran out of ideas. */
export type Finality = 'open' | 'retryable' | 'final'
const FINALITY_OF: Record<RequestState, Finality> = {
  queued: 'open', identifying: 'open', awaiting_review: 'open', fetching: 'open', verifying: 'open', filing: 'open',
  done: 'final', duplicate: 'final',
  error: 'retryable', not_found: 'retryable',
  rejected: 'final', cancelled: 'final',
}
/** The one answer to "can the owner press a button and have this tried again?". Every retry affordance in
 *  the UI asks this rather than carrying its own list of states, which is how the Failed badge and the
 *  "Retry all" button came to count different things. */
export const canRetry = (state: RequestState): boolean => FINALITY_OF[state] === 'retryable'

/* What the Failed list says about each way of failing. `tally` is the fragment the summary line above the
   list counts with ("20 you stopped"); `note` is the sentence on the row itself, and every one of them ends
   by saying whether the track can come back and what to do if it cannot. Retryability is deliberately not
   repeated here -- it is read from FINALITY_OF above -- so the words and the buttons cannot disagree.
   Keyed by every state BUCKET_OF sends to the `failed` bucket; the test walks BUCKET_OF and fails if a new
   failure state lands in the list mute. */
const FAILED_COPY: Partial<Record<RequestState, { tally: string; note: string }>> = {
  error: {
    tally: 'gave up after several tries',
    note: 'Flackey tried this several times and stopped. Try again starts the search over from the beginning, Soulseek included.',
  },
  not_found: {
    tally: 'found no copy anywhere',
    note: 'Nothing to download turned up last time. Try again searches again from scratch — Beatport listings and the people sharing on Soulseek both change.',
  },
  rejected: {
    tally: 'failed the quality check',
    note: 'The copy on offer was checked, failed and deleted, so Flackey will not fetch it again. Paste the link again to start a fresh search.',
  },
  cancelled: {
    tally: 'you stopped',
    note: 'You stopped this one — nothing went wrong with it. Flackey will not pick it back up on its own; paste the link again to start over.',
  },
}

const versionOf = (c: Candidate) => c.mix_name || 'Original Mix'

/* Why the lossless upgrade did not happen, in the owner's words rather than the attempt table's. Every
   value of ATTEMPT_OUTCOMES has an entry: a miss with no explanation is the thing this exists to stop. */
const MISS_REASON: Record<string, string> = {
  no_pick: 'nothing on Soulseek matched this track closely enough',
  transfer_failed: 'the people who had it would not send it',
  first_byte_timeout: 'the people who had it never started sending',
  queued: 'the people who had it are busy and their queue has not reached us yet',
  transfer_timeout: 'the people who had it stopped sending part-way through',
  verify_failed: 'the copies offered were not really lossless',
  fingerprint_failed: 'the copies offered were a different recording',
  fingerprint_unavailable: 'the recording could not be checked acoustically',
  convert_failed: 'the file could not be converted',
  unavailable: 'Soulseek was not reachable at the time',
  interrupted: 'it was interrupted',
}

function fallbackOf(b: Bundle): FallbackView | null {
  const t = b.track
  // source_fmt is set only when a lossless provider supplied the file, so its absence is exactly
  // "this is the Deezer copy" -- and it names no provider, which keeps this true for the next one.
  if (!t || t.source_fmt) return null
  const head = t.fmt === 'mp3' && t.bitrate_kbps ? `MP3 ${t.bitrate_kbps} kbps` : t.fmt.toUpperCase()
  const outcome = b.attempt?.outcome
  const reason = outcome ? MISS_REASON[outcome] ?? `the lossless attempt ended in ${outcome}`
    : 'no lossless search ran for this one'
  return { label: `${head} — lossless unavailable`, reason: `Kept the matched MP3 copy because ${reason}.` }
}

const kbps = (bps: number) => bps >= 1e6 ? `${(bps / 1e6).toFixed(1)} MB/s` : `${Math.round(bps / 1e3)} kB/s`
const mb = (bytes: number) => `${(bytes / 1e6).toFixed(1)} MB`

/* What the worker is doing in the seconds between the last byte landing and the row leaving FETCHING.
   Verify, fingerprint and convert all run inside the Soulseek attempt -- the state never reaches VERIFYING,
   because FETCHING is the one the owner may still stop -- so without these the platter sits at 100% and the
   label says nothing while they run. The worker publishes the key; the words live here with the rest of them. */
const PHASE_TEXT: Record<string, string> = {
  verifying: 'Checking the audio is really lossless',
  fingerprinting: 'Checking it is the same recording',
  converting: 'Converting it for your library',
}

function progressOf(b: Bundle, all: FetchProgress[] | null | undefined): ProgressView | null {
  const p = all?.find(x => x.request_id === b.request.id)
  if (!p) return null
  // No bytes are moving in these, so the platter sweeps instead of filling -- the same signal as a queue wait.
  if (p.phase && PHASE_TEXT[p.phase]) return { pct: null, label: PHASE_TEXT[p.phase] }
  // Queued at the peer means no bytes are moving yet; a 0% bar there would read as a stall rather than
  // as a wait, so it says so in words and shows an indeterminate bar instead.
  if (p.state.startsWith('Queued')) return { pct: null, label: `Waiting in ${p.peer}'s queue` }
  if (!p.size) return { pct: null, label: `Downloading from ${p.peer}` }
  return { pct: Math.min(100, p.pct),
    label: `${mb(p.bytes)} of ${mb(p.size)} from ${p.peer}${p.speed_bps > 0 ? ` · ${kbps(p.speed_bps)}` : ''}` }
}
const IN_FLIGHT: RequestState[] = ['identifying', 'fetching', 'verifying', 'filing']
/** "Stop", not "Cancel": the button beside a running download, where Cancel reads as "leave this dialog". */
const STOP: RowAction = { label: 'Stop', kind: 'cancel' }
// No "step N of 6" here any more: the stepper itself says which rung we are on, and two places saying it
// disagreed the moment the map changed. Each line describes what is happening, nothing else.
const PROGRESS_TEXT: Partial<Record<RequestState, string>> = {
  identifying: 'Looking it up on Beatport and Deezer', fetching: 'Downloading the file',
  verifying: 'Checking the audio is genuine and that it is the right recording', filing: 'Tagging and filing',
}

/** Choose is the one rung most tracks never touch: it exists for the minority that stop and wait on a
 *  person. Drawing it green on the rest claims a step that never happened, which is the same false
 *  completion the all-green duplicate row used to tell. `reviewed` is the durable answer -- it is written
 *  the first time the request is parked and never cleared, so the rung neither appears out of nowhere nor
 *  vanishes from under the owner the moment they pick. The `awaiting_review` clause covers the instant
 *  before the flag reaches the browser, and old rows written before the column existed. */
const skipsChoice = (r: Request) => !r.reviewed && r.state !== 'awaiting_review'

function stepsFor(r: Request): StepView[] | null {
  const state = r.state
  const i = stepIndex(state)
  if (i == null) return null
  // Built against the full ladder, then thinned: the indices in STEP_OF are positions in STEPS, and a rung
  // dropped first would shift every one after it.
  const dot = (n: number): Dot =>
    COMPLETE.includes(state) ? 'done' : state === 'queued' ? 'pending' : n < i ? 'done' : n === i ? 'current' : 'pending'
  const rungs = STEPS.map((name, n) => ({ name, state: dot(n) }))
  return skipsChoice(r) ? rungs.filter(s => s.name !== 'Choose') : rungs
}

/** What proves the file is the real thing: the Chromaprint match against Deezer, and the spectrogram cutoff.
 *  Both already ride along in the bundle - the UI simply never showed them. */
function checksFor(b: Bundle): CheckView[] {
  const out: CheckView[] = []
  const fp = b.attempt?.fingerprint
  if (fp && fp.score != null) out.push({ label: 'Same recording', value: `${(fp.score * 100).toFixed(1)}% match`, ok: fp.status !== 'failed' })
  const hz = b.track?.cutoff_hz ?? b.rejection?.cutoff_hz ?? null
  // "Verified to 22.1 kHz", not "Audio to": the number is the highest frequency carrying real content, and
  // 22.1 kHz means no encoder lowpass was found anywhere, which is the point the line is making. A file
  // that failed is not verified to anything, so it says what it is instead.
  if (hz) out.push({ label: b.track != null ? 'Verified to' : 'Audio only to',
                     value: `${(hz / 1000).toFixed(1)} kHz`, ok: b.track != null })
  return out
}

/** `AIFF 16-bit/44.1 kHz, from FLAC via Soulseek` - the API already builds this; older payloads get a fallback. */
function formatLabelOf(b: Bundle): string | null {
  const t = b.track
  if (!t) return null
  if (t.format?.label) return t.format.label
  const head = t.fmt === 'mp3' ? `MP3 ${t.bitrate_kbps} kbps` : t.fmt.toUpperCase()
  return `${head} via ${sourceLabel(t.source) ?? 'Deezer'}`
}

function lengthNote(c: Candidate, videoS: number | null): string {
  if (c.duration_s == null || videoS == null) return ''
  const d = c.duration_s - videoS
  if (Math.abs(d) <= 3) return 'same length as the video'
  return `${mmss(Math.abs(d))} ${d < 0 ? 'shorter' : 'longer'} than the video`
}

function titleOf(b: Bundle): { title: string; version: string | null } {
  const r = b.request
  if (b.catalog) return { title: `${b.catalog.artist} – ${b.catalog.title}`, version: b.catalog.mix_name }
  if (r.query_artist && r.query_title) return { title: `${r.query_artist} – ${r.query_title}`, version: r.query_version }
  return { title: r.raw_text, version: null }
}

function outcomeOf(state: RequestState): OutcomeView | null {
  const copy = FAILED_COPY[state]
  return copy ? { retryable: canRetry(state), note: copy.note } : null
}

/** The sentence beside "Retry all N" that reconciles it with the "Failed N" badge above it. The two numbers
 *  differ whenever a failure is final, and a tab showing both without a word about it is exactly what sent
 *  the owner asking whether failed is a final state. Null when there is nothing to reconcile: every failure
 *  on screen is retryable, the numbers already match, and a line saying "0 of these" would be noise.
 *  Counted from the same FINALITY_OF the buttons are drawn from, and worded from the same FAILED_COPY the
 *  rows are, so this line cannot claim a breakdown the list below it does not show. */
export function failedSummary(bundles: Bundle[]): string | null {
  const failed = bundles.filter(b => bucketOf(b.request.state) === 'failed')
  const finals = failed.filter(b => !canRetry(b.request.state))
  if (finals.length === 0) return null
  // Fixed key order, not first-seen order: the line must read the same on every refresh, and the map is
  // rebuilt from scratch each time the list changes.
  const parts = (Object.keys(FAILED_COPY) as RequestState[])
    .map(s => ({ n: finals.filter(b => b.request.state === s).length, tally: FAILED_COPY[s]!.tally }))
    .filter(p => p.n > 0)
    .map(p => `${p.n} ${p.tally}`)
  // No count in the head when the whole tab is final: "None of these 5" needs the reader to check the
  // number against the badge, where "Nothing here" is the answer they came for, and it also keeps the
  // sentence honest on a tab holding exactly one row.
  const head = failed.length === finals.length
    ? 'Nothing here can be tried again'
    : `${finals.length} of these ${failed.length} cannot be tried again`
  return `${head} — ${parts.join(', ')}. ${finals.length === 1 ? 'That row says' : 'Each row says'} what to do instead.`
}

export function presentRow(b: Bundle, opts: PresentOpts): RowView {
  const r = b.request
  const { title, version } = titleOf(b)
  const bucket = bucketOf(r.state)
  const v: RowView = {
    id: r.id, title, version, status: '', statusTone: 'muted', steps: stepsFor(r),
    tag: null, dimmed: false, washed: false, action: null, candidates: null, rejection: null,
    artworkUrl: b.catalog?.artwork_url ?? null, rejected: false, retryInSeconds: null,
    bucket, removable: bucket === 'done' || bucket === 'failed', formatLabel: formatLabelOf(b), checks: checksFor(b),
    progress: progressOf(b, opts.fetchProgress), fallback: fallbackOf(b),
    outcome: outcomeOf(r.state),
  }
  const step = stepIndex(r.state)
  const hasAnySource = opts.telegramAuthorized || opts.soulseekConnected
  const sourceUnavailable = r.state === 'fetching' && r.fetch_source === 'soulseek'
    ? !opts.soulseekConnected
    : !hasAnySource
  if (sourceUnavailable && (IN_FLIGHT.includes(r.state) || r.state === 'queued')) {
    v.status = r.state === 'queued' ? 'Paused — connect a source to start'
                                    : r.fetch_source === 'soulseek'
                                      ? 'Paused — Soulseek is not connected'
                                      : 'Paused — connect a source to continue'
    // The rung, not a count of them: "step 3 of 6" outlived the six-rung ladder, and the ladder beside
    // this tag already shows how far along it is. What the tag adds is the name of the rung it stopped on.
    // `stepIndex` counts positions in the full STEPS vocabulary, not in this row's drawn ladder, which may
    // have had Choose thinned out of it. That is right for naming a rung and wrong for counting one: drop a
    // second conditional rung one day and this still names correctly, but do not turn it back into "of N".
    v.tag = r.state === 'queued' ? 'queued' : `paused at ${STEPS[step ?? 0]}`
    v.dimmed = true
    if (r.state === 'identifying' || r.state === 'fetching') v.action = STOP
    return v
  }
  switch (r.state) {
    case 'queued':
      if (r.retry_after) {
        const secs = Math.max(0, Math.round((new Date(r.retry_after).getTime() - opts.now.getTime()) / 1000))
        v.retryInSeconds = secs
        const reason = (r.flag_reason || 'could not complete')
          .replace(/, will retry$/, '')
          .replace(/^no way to fetch this track:\s*/i, '')
          .replace(/nothing on Soulseek matched this track closely enough, the source is unavailable for the lossy fallback/i,
            'No Soulseek match; alternate source unavailable')
        v.status = `Previous attempt: ${reason}`
        v.statusTone = 'amber'
      } else {
        // There is no line any more: every queued track is picked up on the worker's next pass, so this
        // is a second or two, not a position. The old text counted the tracks ahead because the wait was
        // real and permanent; it is neither, now, and a queue length nobody waits in would be theatre.
        v.status = 'Starting…'
        v.tag = 'queued'; v.dimmed = true
        v.action = STOP
      }
      break
    case 'identifying': case 'fetching': case 'verifying': case 'filing': {
      const from = r.state === 'fetching' ? sourceLabel(r.fetch_source) : null
      v.status = PROGRESS_TEXT[r.state]! + (from ? ` from ${from}` : '')
      v.statusTone = 'amber'
      // Verify and file move the finished file into the library; there is no safe moment to stop those,
      // and they are over in seconds. Everything before them can be stopped, which is the point: a
      // download crawling at 100 kB/s from one peer is exactly what an owner wants to be rid of.
      if (r.state === 'identifying' || r.state === 'fetching') v.action = STOP
      break
    }
    case 'awaiting_review': {
      v.status = `Needs your choice — ${r.flag_reason || 'more than one version matches'}. Pick the version you want.`
      v.statusTone = 'amber'; v.washed = true
      const sorted = [...b.candidates].sort((a, c) => (c.score ?? 0) - (a.score ?? 0) || a.rank - c.rank).slice(0, 5)
      v.candidates = sorted.map(c => ({
        id: c.id, title: `${c.artist} – ${c.title}`, version: versionOf(c), score: c.score, length: mmss(c.duration_s),
        onBeatport: c.catalog_track_id != null, lengthNote: lengthNote(c, r.query_duration_s), chosen: c.id === r.chosen_candidate_id,
      }))
      v.action = { label: 'Skip this track', kind: 'cancel' }
      break
    }
    case 'done':
      if (b.track) {
        // No status line: it used to carry the library-relative path, which is `Artist/Artist - Title`
        // -- the same sentence as the title directly above it, ellipsized before it ever reached the
        // filename. Show in Finder is right there for anyone who wants the location.
        v.action = { label: 'Show in Finder', kind: 'reveal', path: b.track.path }
        if (!v.version) v.version = b.track.mix_name
      } else { v.status = 'Filed earlier — the file is no longer in your library folder'; v.statusTone = 'muted' }
      break
    case 'duplicate':
      v.status = 'Already in your library — skipped, nothing downloaded twice'
      if (b.track) v.action = { label: 'Show in Finder', kind: 'reveal', path: b.track.path }
      break
    case 'rejected': {
      const reason = b.rejection?.reason || r.error_message || 'Failed the quality check'
      v.status = `${reason.replace(/\.$/, '')}. Deleted, not added to your library.`
      v.statusTone = 'red'; v.rejected = true
      const khz = b.rejection?.cutoff_hz ? Math.round(b.rejection.cutoff_hz / 1000) : null
      v.rejection = {
        reason, cutoffKhz: khz,
        caption: khz != null ? `A real 320 kbps file has sound up to 20 kHz. This one stops at ${khz} kHz — it was blown up from a smaller file.`
          : 'The file did not pass the quality check.',
        spectrogramUrl: b.rejection?.spectrogram_path ? spectrogramUrl(b.rejection.id) : null,
      }
      v.action = { label: opts.whyOpen ? 'Hide why' : 'See why', kind: 'why' }
      break
    }
    // `cancel` leaves `flag_reason` as it found it, so a row that had backed off before it was stopped is
    // still carrying "…, will retry". Nothing here reads it: printing a retry promise on a row this very
    // list calls final is worse than printing nothing, and the outcome note below says what happened.
    case 'cancelled': v.status = 'Stopped by you'; v.dimmed = true; break
    case 'not_found':
      v.status = `No downloadable match found${r.error_message ? ' — ' + r.error_message : ''}`
      break
    case 'error':
      v.status = `Failed — ${r.error_message || 'unknown error'}`; v.statusTone = 'red'
      break
  }
  // One place decides which rows get a retry button, and it is the same `canRetry` the Failed tab counts
  // with and the worker is pinned to. It used to be spelled out in the two cases above, which is how a
  // third list of "failed" states could be written elsewhere and disagree with it.
  if (canRetry(r.state)) v.action = { label: 'Try again', kind: 'retry' }
  return v
}

function summaryOf(list: Bundle[]): GroupSummary {
  const total = list.length
  const filed = list.filter(b => COMPLETE.includes(b.request.state)).length
  return {
    filed, total,
    needsChoice: list.filter(b => b.request.state === 'awaiting_review').length,
    rejected: list.filter(b => b.request.state === 'rejected').length,
    inFlight: list.filter(b => bucketOf(b.request.state) === 'progress').length,
    pct: total ? Math.round((filed / total) * 100) : 0,
  }
}

const BEATPORT_DOWN = /^Beatport unreachable/
export function groupRows(bundles: Bundle[], playlists: Playlist[], opts: PresentOpts, filter: Bucket | 'all' = 'all'): GroupView[] {
  const byPlaylist = new Map<number | null, Bundle[]>()
  for (const b of bundles) {
    const k = b.request.playlist_id
    if (!byPlaylist.has(k)) byPlaylist.set(k, [])
    byPlaylist.get(k)!.push(b)
  }
  const names = new Map(playlists.map(p => [p.id, p.name]))
  const groups: GroupView[] = []
  const keys = [...byPlaylist.keys()].filter((k): k is number => k != null)
    .sort((a, c) => Math.max(...byPlaylist.get(c)!.map(b => b.request.id)) - Math.max(...byPlaylist.get(a)!.map(b => b.request.id)))
  const build = (key: string, name: string, list: Bundle[]): GroupView => {
    const sorted = [...list].sort((a, c) =>
      filter === 'all'
        ? c.request.created_at.localeCompare(a.request.created_at) || c.request.id - a.request.id
        : a.request.id - c.request.id
    )
    const rows = sorted.map(b => presentRow(b, opts)).filter(r => filter === 'all' || r.bucket === filter)
    const down = sorted.filter(b => b.request.state === 'queued' && b.request.retry_after && BEATPORT_DOWN.test(b.request.flag_reason || ''))
    const seconds = down.length ? Math.max(0, ...down.map(b => Math.round((new Date(b.request.retry_after!).getTime() - opts.now.getTime()) / 1000))) : 0
    return {
      key, name, rows,
      summary: summaryOf(sorted),
      beatportDown: down.length ? { seconds, requestIds: down.map(b => b.request.id) } : null,
    }
  }
  for (const k of keys) groups.push(build(`pl-${k}`, names.get(k) ?? 'Playlist', byPlaylist.get(k)!))
  if (byPlaylist.has(null)) groups.push(build('single', 'Single tracks', byPlaylist.get(null)!))
  return groups.filter(g => g.rows.length > 0)
}

export function bucketCounts(bundles: Bundle[]): Record<Bucket | 'all', number> {
  const counts: Record<Bucket | 'all', number> = { all: bundles.length, progress: 0, needs: 0, done: 0, failed: 0 }
  for (const b of bundles) counts[bucketOf(b.request.state)]++
  return counts
}
