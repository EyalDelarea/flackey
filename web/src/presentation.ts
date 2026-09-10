import type { Bundle, Candidate, FetchProgress, Playlist, RequestState } from './api'
import { spectrogramUrl } from './api'

export const STEPS = ['Identify', 'Match', 'Fetch', 'Verify', 'File', 'Done'] as const
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
export interface RowView {
  id: number; title: string; version: string | null; status: string; statusTone: Tone; statusMono: boolean
  steps: StepView[] | null; tag: string | null; dimmed: boolean; washed: boolean
  action: RowAction | null; candidates: CandidateView[] | null; rejection: RejectionView | null
  artworkUrl: string | null; rejected: boolean; retryInSeconds: number | null; bucket: Bucket; removable: boolean
  formatLabel: string | null; checks: CheckView[]
  progress: ProgressView | null; fallback: FallbackView | null
}
/** A live transfer's position. Only ever set for the row actually downloading right now. */
export interface ProgressView { pct: number | null; label: string }
/** This track is on the lossy Deezer copy and why, so a miss is visible rather than silent. */
export interface FallbackView { label: string; reason: string }
export interface GroupSummary { filed: number; total: number; needsChoice: number; rejected: number; inFlight: number; pct: number }
export interface GroupView { key: string; name: string; summary: GroupSummary; rows: RowView[]; beatportDown: { seconds: number; requestIds: number[] } | null }
export interface PresentOpts {
  libraryRoot: string; telegramAuthorized: boolean; now: Date; whyOpen: boolean
  fetchProgress?: FetchProgress | null
  /** request id -> how many tracks the worker will take before this one. See `queueAhead`. */
  queueAhead?: Map<number, number>
}

export const mmss = (s: number | null | undefined) => s == null ? '?:??' : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
export function relPath(path: string, root: string): string {
  const rel = path.startsWith(root) ? path.slice(root.length).replace(/^\/+/, '') : path
  return rel.split('/').filter(Boolean).join(' / ')
}
export const gb = (bytes: number) => bytes >= 1e9 ? `${(bytes / 1e9).toFixed(1)} GB` : `${Math.round(bytes / 1e6)} MB`
// `done` is index 5, not 6: the old value indexed past STEPS, so the Done rung could never read as reached.
const STEP_OF: Partial<Record<RequestState, number>> = { queued: 0, identifying: 0, awaiting_review: 1, fetching: 2, verifying: 3, filing: 4, done: 5, duplicate: 5 }
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
const versionOf = (c: Candidate) => c.mix_name || 'Original Mix'

/** How many tracks the worker will take before each queued one -- the same order `Store.next_queued` uses:
    anything already in flight first, then queued rows by id. A row waiting on a `retry_after` backoff is not
    in line yet and is left out; its own countdown already says what it is waiting for.

    This exists because "why isn't it parallel?" is the question a bare "Waiting its turn" invites. The answer
    is that the slow stages cannot overlap: the Deezer source is one bot conversation whose replies would
    interleave, and two lossless downloads would race on the same derived path (see `_remove_stale_file`).
    Since the wait is real and permanent, the honest fix is to show its length rather than to hide it. */
export function queueAhead(bundles: Bundle[]): Map<number, number> {
  const out = new Map<number, number>()
  let ahead = bundles.filter(b => IN_FLIGHT.includes(b.request.state)).length
  const waiting = bundles.filter(b => b.request.state === 'queued' && !b.request.retry_after)
  for (const b of waiting.sort((a, c) => a.request.id - c.request.id)) out.set(b.request.id, ahead++)
  return out
}

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
  return { label: `${head} — no lossless copy`, reason: `Kept the Deezer copy because ${reason}.` }
}

const kbps = (bps: number) => bps >= 1e6 ? `${(bps / 1e6).toFixed(1)} MB/s` : `${Math.round(bps / 1e3)} kB/s`
const mb = (bytes: number) => `${(bytes / 1e6).toFixed(1)} MB`

function progressOf(b: Bundle, p: FetchProgress | null | undefined): ProgressView | null {
  if (!p || p.request_id !== b.request.id) return null
  // Queued at the peer means no bytes are moving yet; a 0% bar there would read as a stall rather than
  // as a wait, so it says so in words and shows an indeterminate bar instead.
  if (p.state.startsWith('Queued')) return { pct: null, label: `Waiting in ${p.peer}'s queue` }
  if (!p.size) return { pct: null, label: `Downloading from ${p.peer}` }
  return { pct: Math.min(100, p.pct),
    label: `${mb(p.bytes)} of ${mb(p.size)} from ${p.peer}${p.speed_bps > 0 ? ` · ${kbps(p.speed_bps)}` : ''}` }
}
const IN_FLIGHT: RequestState[] = ['identifying', 'fetching', 'verifying', 'filing']
// No "step N of 6" here any more: the stepper itself says which rung we are on, and two places saying it
// disagreed the moment the map changed. Each line describes what is happening, nothing else.
const PROGRESS_TEXT: Partial<Record<RequestState, string>> = {
  identifying: 'Looking it up on Beatport and Deezer', fetching: 'Downloading the file',
  verifying: 'Checking the audio is genuine and that it is the right recording', filing: 'Tagging and filing',
}

function stepsFor(state: RequestState): StepView[] | null {
  const i = stepIndex(state)
  if (i == null) return null
  if (COMPLETE.includes(state)) return STEPS.map(name => ({ name, state: 'done' as Dot }))
  if (state === 'queued') return STEPS.map(name => ({ name, state: 'pending' as Dot }))
  return STEPS.map((name, n) => ({ name, state: (n < i ? 'done' : n === i ? 'current' : 'pending') as Dot }))
}

/** What proves the file is the real thing: the Chromaprint match against Deezer, and the spectrogram cutoff.
 *  Both already ride along in the bundle - the UI simply never showed them. */
function checksFor(b: Bundle): CheckView[] {
  const out: CheckView[] = []
  const fp = b.attempt?.fingerprint
  if (fp && fp.score != null) out.push({ label: 'Same recording', value: `${(fp.score * 100).toFixed(1)}% match`, ok: fp.status !== 'failed' })
  const hz = b.track?.cutoff_hz ?? b.rejection?.cutoff_hz ?? null
  if (hz) out.push({ label: 'Audio to', value: `${(hz / 1000).toFixed(1)} kHz`, ok: b.track != null })
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

export function presentRow(b: Bundle, opts: PresentOpts): RowView {
  const r = b.request
  const { title, version } = titleOf(b)
  const bucket = bucketOf(r.state)
  const v: RowView = {
    id: r.id, title, version, status: '', statusTone: 'muted', statusMono: false, steps: stepsFor(r.state),
    tag: null, dimmed: false, washed: false, action: null, candidates: null, rejection: null,
    artworkUrl: b.catalog?.artwork_url ?? null, rejected: false, retryInSeconds: null,
    bucket, removable: bucket === 'done' || bucket === 'failed', formatLabel: formatLabelOf(b), checks: checksFor(b),
    progress: progressOf(b, opts.fetchProgress), fallback: fallbackOf(b),
  }
  const step = stepIndex(r.state)
  if (!opts.telegramAuthorized && (IN_FLIGHT.includes(r.state) || r.state === 'queued')) {
    v.status = r.state === 'queued' ? 'Waiting its turn' : 'Paused — will continue after you reconnect'
    v.tag = r.state === 'queued' ? 'queued' : `paused at step ${(step ?? 0) + 1} of 6`
    v.dimmed = true
    return v
  }
  switch (r.state) {
    case 'queued':
      if (r.retry_after) {
        const secs = Math.max(0, Math.round((new Date(r.retry_after).getTime() - opts.now.getTime()) / 1000))
        v.retryInSeconds = secs
        v.status = `${(r.flag_reason || 'Will retry').replace(/, will retry$/, '')} — trying again in ${secs} seconds`
        v.statusTone = 'amber'
      } else {
        const ahead = opts.queueAhead?.get(r.id)
        // The explanation rides on the "next up" row only. There is exactly one of those, so the reason
        // appears once, next to the wait it explains, instead of on every queued row as noise.
        v.status = ahead == null ? 'Waiting its turn'
          : ahead === 0 ? 'Starting now'
          : ahead === 1 ? 'Next up — tracks are fetched one at a time'
          : `${ahead} tracks ahead`
        v.tag = 'queued'; v.dimmed = true
      }
      break
    case 'identifying': case 'fetching': case 'verifying': case 'filing': {
      const from = r.state === 'fetching' ? sourceLabel(r.fetch_source) : null
      v.status = PROGRESS_TEXT[r.state]! + (from ? ` from ${from}` : '')
      v.statusTone = 'amber'
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
        v.status = relPath(b.track.path, opts.libraryRoot)
        v.statusTone = 'muted'; v.statusMono = true
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
    case 'cancelled': v.status = 'Skipped'; v.dimmed = true; break
    case 'not_found': v.status = `Not available on Deezer${r.error_message ? ' — ' + r.error_message : ''}`; break
    case 'error':
      v.status = `Failed — ${r.error_message || 'unknown error'}`; v.statusTone = 'red'
      v.action = { label: 'Try again', kind: 'retry' }
      break
  }
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
  // Computed over every bundle, not per group: the worker has one queue, so a track's place in line counts
  // the playlists ahead of it too.
  const withQueue: PresentOpts = { ...opts, queueAhead: opts.queueAhead ?? queueAhead(bundles) }
  const groups: GroupView[] = []
  const keys = [...byPlaylist.keys()].filter((k): k is number => k != null)
    .sort((a, c) => Math.max(...byPlaylist.get(c)!.map(b => b.request.id)) - Math.max(...byPlaylist.get(a)!.map(b => b.request.id)))
  const build = (key: string, name: string, list: Bundle[]): GroupView => {
    const sorted = [...list].sort((a, c) =>
      filter === 'all'
        ? c.request.created_at.localeCompare(a.request.created_at) || c.request.id - a.request.id
        : a.request.id - c.request.id
    )
    const rows = sorted.map(b => presentRow(b, withQueue)).filter(r => filter === 'all' || r.bucket === filter)
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
