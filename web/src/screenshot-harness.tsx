import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './theme.css'
import { api } from './api'
import type { AppSettings, Health, Stats, Track, Bundle, Candidate, Playlist, Request, RequestState } from './api'

/* Every EventSource this harness has handed out. StrictMode mounts twice, so `live` subscribes more than
   once: the 'stages' status frame has to reach all of them, while 'choose' only needs the newest, which is
   the one the mounted page is listening on. */
const sources: FakeEventSource[] = []
const lastEventSource = () => sources[sources.length - 1] ?? null
class FakeEventSource {
  onopen: (() => void) | null = null
  handlers: Record<string, (e: { data: string }) => void> = {}
  addEventListener(name: string, fn: (e: { data: string }) => void) { this.handlers[name] = fn }
  close() { /* no-op */ }
  constructor(_url: string) { sources.push(this) }
}
;(window as any).EventSource = FakeEventSource

const settings: AppSettings = {
  library_root: '/Users/dj/Music/Flackey', data_dir: '/data', version: '0.1.0',
  telegram_configured: true, log_path: '/data/flackey.log',
}
/* The Settings panel only draws its Soulseek rows for a copy that has an account, a format and a port
   table, so the settings scenarios need a fuller payload than the library ones -- with the thin one above
   the panel is half empty and a screenshot of it proves nothing about the layout. */
const settingsPanel: AppSettings = {
  ...settings, library_root: '/Users/dj/Music/DJ Library',
  data_dir: '/Users/dj/Library/Application Support/Flackey',
  log_path: '/Users/dj/Library/Application Support/Flackey/flackey.log',
  soulseek_enabled: true, lossless_filing_format: 'aiff', filing_formats: ['aiff', 'wav', 'flac'],
  ports: {
    app: { port: 8765, host: '127.0.0.1', public: false },
    sidecar: { port: 5030, host: '127.0.0.1', public: false },
    soulseek_listen: { port: 50300, host: '0.0.0.0', public: true },
  },
  ranking: { max_picks: 4, max_queue: null, fingerprint_min: 0.79 },
}
const stats: Stats = { tracks: 2, bytes: 84_000_000, playlists: 1, rejections: 0,
  library_root: '/Users/dj/Music/Flackey', playlist_dir: '/Users/dj/Music/Flackey/Playlists', requests_by_state: {} }
const health = (over: Partial<Health> = {}): Health => ({ ok: true, version: '0.1.0',
  telegram_authorized: true, worker_running: true, setup_done: true, source_enabled: true,
  telegram_configured: true,
  lossless: { enabled: true, provider: { name: 'slskd', status: 'ok', username: 'flackey-dj' }, fpcalc: true, attempts_24h: {}, raw_mb: 0 },
  ...over })

const track = (over: Partial<Track>): Track => ({
  id: 1, path: '/lib/Example Artist/Track.flac', fmt: 'flac', bitrate_kbps: 1411, cutoff_hz: 20000, file_size: 40_000_000,
  artist: 'Example Artist', title: 'Track Title', mix_name: 'Extended Mix', duration_s: 360,
  isrc: null, catalog_track_id: null, request_id: null, added_at: '2026-09-10T00:00:00Z', verified_at: '2026-09-10T00:00:01Z',
  spectrogram_path: null, source: 'soulseek', source_fmt: 'flac', bit_depth: 16, sample_rate: 44100,
  catalog: { id: 1, artist: 'Example Artist', title: 'Track Title', mix_name: 'Extended Mix', label: 'Anjunadeep',
    genre: 'Melodic House', isrc: null, sub_genre: null, catalog_number: null, release_name: 'Track Title EP',
    release_date: '2024-05-01', bpm: 122, key: '8A', duration_ms: 360000, artwork_url: null },
  ...over,
})

/* One failure of each kind, which is the only way to see the Failed tab answer its own question: two rows
   the worker will take back, two it will not, and a summary line reconciling the badge with the button. */
const failure = (id: number, state: RequestState, over: Partial<Request> = {}): Bundle => ({
  request: {
    id, created_at: '2026-09-16T20:00:00Z', updated_at: '2026-09-16T20:05:00Z', kind: 'yt_track', state,
    raw_text: 'https://youtu.be/x', playlist_id: null, playlist_position: null, source_url: null,
    query_artist: 'Astral Projection', query_title: 'Into the Void', query_version: null, query_duration_s: 442,
    chosen_candidate_id: null, catalog_track_id: null, confidence: null, flag_reason: null, error_message: null,
    attempts: 0, retry_after: null, track_id: null, fetch_source: null, failed_stage: null, ...over,
  },
  candidates: [], catalog: null, track: null, rejection: null,
})

const failures: Bundle[] = [
  failure(41, 'error', { query_artist: 'Ace Ventura', query_title: 'Presence', attempts: 3,
    error_message: 'the people who had it stopped sending part-way through' }),
  failure(42, 'not_found', { query_artist: 'Vibrasphere', query_title: 'Lime Twig',
    error_message: 'could not identify this track: no Deezer candidates and no Beatport match; no artist and title could be read from the request, so there is nothing to search for' }),
  failure(43, 'rejected', { query_artist: 'Symbolic', query_title: 'Gravity Waves' }),
  failure(44, 'cancelled', { query_artist: 'Human Element', query_title: 'The Answer' }),
  failure(45, 'cancelled', { query_artist: 'Atmos', query_title: 'Klein Aber Doctor',
    flag_reason: 'Beatport unreachable, will retry' }),
]

/* ---- issue #60: the download list split by pipeline stage --------------------------------------
   The scale is the point. This issue was opened against a 120-track import where "In progress 49" was one
   undifferentiated lump and 41 of those 49 were parked on a Soulseek backoff, doing nothing. A tidy
   three-row fixture would render the chips and the segmented bar perfectly and prove none of it, so this
   one is that import: 16 filed, 8 working (6 searching, 1 downloading, 1 verifying), 41 waiting and 55
   failed, which is where the design's numbers come from. Both tabs read from this one list -- the Failed
   tab needs its chip pressed, exactly as the `failed` scenario above does. */
const goaPlaylist: Playlist = { id: 7, source_url: 'https://open.spotify.com/playlist/goatrance', name: 'Goa Trance Classics',
  created_at: '', updated_at: '', track_ids: [], track_positions: [], file: '/lib/Playlists/Goa Trance Classics.m3u8' }

/* Filler for the rows below the fold. The named rows above them are the ones the screenshots are of; these
   only have to make the list as long as the counts claim it is. */
const GOA: [string, string][] = [
  ['Transwave', 'Hypnotic'], ['Cosmosis', 'Cannabis'], ['Electric Universe', 'Solar Energy'],
  ['X-Dream', 'Radio'], ['Koxbox', 'Forever After'], ['Doof', 'Let’s Turn On'],
  ['Green Nuns Of The Revolution', 'Klunk'], ['The Infinity Project', 'Mystical Experience'],
  ['Prana', 'Scarab'], ['MFG', 'Communication'], ['Chi-AD', 'Enlightenment'],
  ['Psysex', 'Dimensional Gate'], ['GMS', 'Juice'], ['Talamasca', 'Psychedelic Trance'],
  ['Yahel', 'Devotion'], ['Absolum', 'Kabalah'], ['Sandman', 'Witchcraft'],
  ['Astrix', 'Coolio'], ['Killerwatts', 'Psychedelic Jungle'], ['Space Tribe', 'Ultrasonic Heartbeat'],
  ['Deedrah', 'Reset'], ['Hux Flux', 'Cryogenics'], ['Logic Bomb', 'Headware'],
  ['Quirk', 'Bug Powder'], ['Slinky Wizard', 'Sheep'], ['Tandu', 'Alien Pump'],
  ['Union Jack', 'Two Full Moons And A Trout'], ['Wizzy Noise', 'Signals'], ['Ticon', 'Alpha Beta'],
  ['Shakta', 'Lepton Head'],
]

let goaAt = 0
const nextGoa = (): [string, string] => GOA[goaAt++ % GOA.length]

/* Every row carries the same `created_at` so the list's sort falls through to the id, which lets the named
   rows be put at the top of each tab simply by building them first. */
let stageId = 1000
const STAGE_AT = '2026-09-22T09:00:00Z'
const stageRow = (artist: string, title: string, state: RequestState, over: Partial<Request> = {}): Bundle => ({
  request: {
    id: stageId--, created_at: STAGE_AT, updated_at: STAGE_AT, kind: 'yt_track', state,
    raw_text: 'https://youtu.be/x', playlist_id: goaPlaylist.id, playlist_position: null, source_url: null,
    query_artist: artist, query_title: title, query_version: null, query_duration_s: 442,
    chosen_candidate_id: null, catalog_track_id: null, confidence: null, flag_reason: null, error_message: null,
    attempts: 0, retry_after: null, track_id: null, fetch_source: null, failed_stage: null, ...over,
  },
  candidates: [], catalog: null, track: null, rejection: null,
})

const inSeconds = (s: number) => new Date(Date.now() + s * 1000).toISOString()
const NO_MATCH = 'no way to fetch this track: nothing on Soulseek matched this track closely enough, Deezer offered nothing to fall back on, will retry'

function stageBundles(): Bundle[] {
  const rows: Bundle[] = []
  // Working: the one row actually moving bytes, the one being checked, and four looking themselves up.
  rows.push(stageRow('Raja Ram, Riktam, Space Cat', 'Snorkel Blaster', 'fetching', { fetch_source: 'soulseek' }))
  rows.push(stageRow('Astral Projection', 'Mahadeva', 'verifying'))
  rows.push(stageRow('Oforia', 'No Refund', 'identifying'))
  rows.push(stageRow('Etnica', 'The Italian EP [1995] Spirit Zone Recordings', 'identifying'))
  // Waiting: the category the issue exists to name. Two on the quick ladder, one on the 6 h Soulseek park.
  rows.push(stageRow('Hallucinogen', 'Jiggle Of The Sphinx', 'queued', { attempts: 2, retry_after: inSeconds(870), flag_reason: NO_MATCH }))
  rows.push(stageRow('California Sunshine', 'Rain', 'queued', { attempts: 2, retry_after: inSeconds(885), flag_reason: NO_MATCH }))
  rows.push(stageRow('Infected Mushroom', 'Bust A Move', 'queued', { attempts: 1, retry_after: inSeconds(20880),
    flag_reason: 'waiting for Soulseek — nobody sharing it came online; 3 of 4 looks left' }))
  // Failed, one of each way of stopping -- the rows the Failed tab's chips and stage tags are read from.
  rows.push(stageRow('Man With No Name', 'Teleport', 'not_found', {
    error_message: 'no copy turned up on Beatport, Deezer or Soulseek' }))
  rows.push(stageRow('Juno Reactor', 'Guardian Angel', 'error', { attempts: 7, failed_stage: 'download',
    error_message: 'gave up after 7 tries — every peer refused the transfer' }))
  rows.push(stageRow('Shpongle', 'Divine Moments Of Truth', 'error', { attempts: 4, failed_stage: 'download',
    error_message: 'looked 4 times over 24 h — nobody sharing it came online' }))
  rows.push(stageRow('Total Eclipse', 'Aliens', 'rejected'))
  rows.push(stageRow('Pleiadians', 'Maia', 'cancelled'))
  // The rest of the batch, to the counts above: 2 more searching, 38 more waiting, 16 filed, and the
  // remaining 50 failures split the way the design's Failed bar is (Search 12, Download 38, Verify 4).
  const fill = (n: number, make: (artist: string, title: string) => Bundle) => {
    for (let i = 0; i < n; i++) { const [a, t] = nextGoa(); rows.push(make(a, t)) }
  }
  fill(4, (a, t) => stageRow(a, t, 'queued'))
  fill(38, (a, t) => stageRow(a, t, 'queued', { attempts: 2, retry_after: inSeconds(600 + goaAt * 7), flag_reason: NO_MATCH }))
  fill(16, (a, t) => stageRow(a, t, 'done'))
  fill(11, (a, t) => stageRow(a, t, 'not_found', { error_message: 'no copy turned up on Beatport, Deezer or Soulseek' }))
  fill(36, (a, t) => stageRow(a, t, 'error', { attempts: 5, failed_stage: 'download',
    error_message: 'gave up after 5 tries — every peer refused the transfer' }))
  fill(3, (a, t) => stageRow(a, t, 'rejected'))
  return rows
}

/* The platter on the one downloading row. `live` only ever learns a transfer's position from the SSE
   `status` event, so the harness has to send one -- without it that row draws an empty platter and the
   screenshot shows a batch with nothing visibly moving in it. */
const stageProgress = (requestId: number) => ({
  request_id: requestId, bytes: 18_400_000, size: 42_100_000, peer: 'goadealer', pct: 43,
  speed_bps: 1_200_000, pick: 1, state: 'InProgress',
})

/* The Choose window in the only shape that makes it necessary: candidates that share a title and differ
   only in the version. No amount of text picks between an Original and an Extended Mix, which is exactly
   what the sample buttons are for. Two rows, because the page holds one player for all of them and
   starting a sample in one row stops the one running in the other -- a thing only a screenshot shows. */
const cand = (id: number, artist: string, title: string, mix: string, duration_s: number, score: number,
              onBeatport: boolean, hasPreview = true): Candidate => ({
  id, request_id: 0, source: 'deezer', source_ref: String(id), artist, title, mix_name: mix, duration_s,
  deezer_id: id, isrc: null, rank: id, score, catalog_track_id: onBeatport ? id : null, has_preview: hasPreview,
})
const choice = (id: number, over: Partial<Request>, candidates: Candidate[]): Bundle => ({
  request: {
    id, created_at: '2026-09-21T11:00:00Z', updated_at: '2026-09-21T11:02:00Z', kind: 'yt_track',
    state: 'awaiting_review', raw_text: 'https://youtu.be/x', playlist_id: null, playlist_position: null,
    source_url: null, query_version: null, chosen_candidate_id: null, catalog_track_id: null,
    confidence: null, error_message: null, attempts: 0, retry_after: null, track_id: null,
    fetch_source: null, failed_stage: null, reviewed: 1, query_artist: null, query_title: null, query_duration_s: null,
    flag_reason: null, ...over,
  },
  candidates: candidates.map(c => ({ ...c, request_id: id })), catalog: null, track: null, rejection: null,
})

const choices: Bundle[] = [
  choice(71, { created_at: '2026-09-21T11:00:00Z', query_artist: 'Vibrasphere', query_title: 'Landmark',
    query_duration_s: 489, flag_reason: 'three versions matched and none of them won outright' }, [
    cand(1, 'Vibrasphere', 'Landmark', 'Original Mix', 412, 91, true),
    cand(2, 'Vibrasphere', 'Landmark', 'Extended Mix', 487, 88, true),
    // No sample available for this one: `has_preview: false` means no control renders at all -- the
    // state the no-sample screenshot exists to prove.
    cand(3, 'Vibrasphere', 'Landmark', 'Ticon Remix', 454, 74, false, false),
  ]),
  /* Two candidates in the second row, not three: at the window's own 1100x720 both rows then stand in one
     frame, which is the only way a screenshot shows a sample starting here stopping the one above. */
  choice(72, { created_at: '2026-09-21T10:52:00Z', query_artist: 'Ace Ventura', query_title: 'Presence',
    query_duration_s: 401, flag_reason: 'the full version and a radio edit both matched' }, [
    cand(4, 'Ace Ventura', 'Presence', 'Original Mix', 398, 86, true),
    cand(5, 'Ace Ventura', 'Presence', 'Radio Edit', 228, 69, false),
  ]),
]

/* The queue a moment after Telegram revoked the session out from under a running app (issue #91). Every
   row is still `queued`, flagged `Telegram login required`, with `attempts` untouched at 0 -- which is the
   whole point: before the fix a revoked key surfaced as a plain ConnectionError and each of these was
   burned into `error` instead, one per pass, under a sidebar that still read "Connected". */
const pausedOnLogin: Bundle[] = [
  failure(32, 'queued', { query_artist: 'Astral Projection', query_title: 'Mahadeva', flag_reason: 'Telegram login required' }),
  failure(33, 'queued', { query_artist: 'Shpongle', query_title: 'Divine Moments of Truth', flag_reason: 'Telegram login required' }),
  failure(34, 'queued', { query_artist: 'Hallucinogen', query_title: 'LSD', flag_reason: 'Telegram login required' }),
]

/* The same three tracks as issue #91 reported them: a revoked key reached the worker as a bare
   ConnectionError, so each pass burned one more request into `error` while `telegram_authorized` was
   never touched and the footer stayed green. Kept beside the scenario above as the picture of what the
   fix must not produce again -- the two render from the same harness, so the only thing that differs
   between them is the state the backend put the queue in. */
const burnedOnRevoke: Bundle[] = [
  failure(32, 'error', { query_artist: 'Astral Projection', query_title: 'Mahadeva', attempts: 3,
    error_message: 'Cannot send requests while disconnected' }),
  failure(33, 'error', { query_artist: 'Shpongle', query_title: 'Divine Moments of Truth', attempts: 3,
    error_message: 'Cannot send requests while disconnected' }),
  failure(34, 'error', { query_artist: 'Hallucinogen', query_title: 'LSD', attempts: 3,
    error_message: 'Cannot send requests while disconnected' }),
]

const scenario = new URLSearchParams(window.location.search).get('scenario') ?? 'default'

function scenarioSetup() {
  vi_spy(api, 'settings', async () => settings)
  vi_spy(api, 'playlists', async () => scenario === 'library-with-playlist' ? [playlist] : scenario === 'stages' ? [goaPlaylist] : [])
  vi_spy(api, 'stats', async () => stats)
  vi_spy(api, 'queue', async () => scenario === 'failed' ? failures
    : scenario === 'stages' ? staged
    : scenario === 'choose' ? choices
    : scenario === 'telegram-revoked' ? pausedOnLogin
    : scenario === 'telegram-revoked-before' ? burnedOnRevoke : [] as Bundle[])
  vi_spy(api, 'uploads', async () => ({ enabled: false, provider: null, uploads: [], summary: { total: 0, active: 0, completed: 0, peers: 0, bytes: 0 }, error: null }))

  switch (scenario) {
    case 'library-narrow':
    case 'library-normal':
      vi_spy(api, 'health', async () => health())
      vi_spy(api, 'library', async () => [
        track({ id: 1, path: '/lib/Example Artist/Track One.flac', artist: 'Example Artist', title: 'Track One' }),
        track({ id: 2, path: '/lib/Example Artist/Track Two.mp3', artist: 'Example Artist', title: 'Track Two', fmt: 'mp3', bitrate_kbps: 320, source_fmt: null, catalog: null }),
        track({ id: 3, path: '/lib/Other Artist/Track Three.flac', artist: 'Other Artist', title: 'Track Three' }),
      ])
      break
    case 'library-empty':
      vi_spy(api, 'health', async () => health())
      vi_spy(api, 'library', async () => [])
      break
    case 'library-filtered-empty':
      vi_spy(api, 'health', async () => health())
      vi_spy(api, 'library', async (q?: string) => (q ? [] : [track({})]))
      break
    /* Two settings scenarios, because the interesting thing about the panel is what it says when a
       connection is missing: `settings` is the everything-works reading, `settings-telegram-signed-out`
       is the same panel with the account the Deezer bot depends on signed out from under it. */
    case 'settings':
    case 'settings-telegram-signed-out': {
      const signedOut = scenario === 'settings-telegram-signed-out'
      vi_spy(api, 'settings', async () => settingsPanel)
      vi_spy(api, 'health', async () => health({ telegram_authorized: !signedOut,
        lossless: { enabled: true, provider: { name: 'slskd', status: 'ok', username: 'saffrontempo0147' },
          fpcalc: true, attempts_24h: {}, raw_mb: 0 },
        sharing: { port: 50300, enabled: true, checking: false, mapping: 'natpmp', reachable: true,
          public_ip: null, lan_ip: '10.0.0.5', gateway: '10.0.0.1', checked_at: null, error: null } }))
      vi_spy(api, 'library', async () => [])
      vi_spy(api, 'telegramStatus', async () => ({ authorized: !signedOut, configured: true,
        phone_masked: signedOut ? null : '+97 •••• ••79' }))
      vi_spy(api, 'pickFolderAvailable', async () => ({ available: true }))
      vi_spy(api, 'update', async () => ({ ok: true, current: '0.1.4', newer: false, available: false,
        latest: '0.1.4', url: null, release_url: null, size: null, size_label: null,
        published_at: null, published_date: null, prerelease: false }))
      break
    }
    /* Telegram revoked mid-run, on a copy with no Soulseek to fall back on -- which is the only
       arrangement where the loss actually stops the digging, and so the one worth a picture. The sidebar
       footer reads from both sources at once, so leaving Soulseek connected here would keep it green and
       hide exactly the thing this scenario exists to show. */
    case 'telegram-revoked':
      vi_spy(api, 'health', async () => health({ telegram_authorized: false, worker_running: false,
        lossless: { enabled: false, provider: null, fpcalc: true, attempts_24h: {}, raw_mb: 0 } }))
      vi_spy(api, 'library', async () => [])
      break
    /* The bug, not a state the app can still reach: `telegram_authorized` stays true because nothing
       ever flipped it, which is what left the footer green while the queue emptied into the Failed tab. */
    case 'telegram-revoked-before':
      vi_spy(api, 'health', async () => health({ telegram_authorized: true, worker_running: true,
        lossless: { enabled: false, provider: null, fpcalc: true, attempts_24h: {}, raw_mb: 0 } }))
      vi_spy(api, 'library', async () => [])
      break
    case 'setup':
      vi_spy(api, 'health', async () => health({ setup_done: false, telegram_authorized: false }))
      vi_spy(api, 'pickFolderAvailable', async () => ({ available: false }))
      vi_spy(api, 'saveSettings', async (library_root: string) => ({ ...settings, library_root }))
      vi_spy(api, 'telegramStatus', async () => ({ authorized: false, configured: true, phone_masked: null }))
      vi_spy(api, 'skipTelegram', async () => ({ source_enabled: false }))
      vi_spy(api, 'soulseekSetup', async () => ({ configured: false, username: null }))
      vi_spy(api, 'slskdSetup', async () => ({ installed: true, running: false, version: '0.26.0' }))
      vi_spy(api, 'tools', async () => ({ ffmpeg: true, ffprobe: true, yt_dlp: true }))
      break
    /* Both issue #60 tabs come from one queue, so there is one scenario for them: the Downloads tab is
       what it lands on, and the Failed tab is one chip press away -- the same division of labour the
       `failed` scenario below already uses. */
    case 'stages':
      vi_spy(api, 'health', async () => health())
      vi_spy(api, 'library', async () => [])
      break
    case 'choose':
      vi_spy(api, 'health', async () => health())
      vi_spy(api, 'library', async () => [])
      // There is no server here to push the 'queue' SSE event a real choose triggers, so this stands in
      // for it: mutate the bundle in place, then fire the event by hand so the page re-fetches `queue()`
      // and re-renders with the chosen ring -- needed only to screenshot the chosen state on a card that
      // is not also playing.
      vi_spy(api, 'choose', async (rid: number, cid: number) => {
        const b = choices.find(b => b.request.id === rid)
        if (!b) throw new Error(`no bundle for request ${rid}`)
        b.request.chosen_candidate_id = cid
        lastEventSource()?.handlers['queue']?.({ data: '' })
        return b.request
      })
      // Nothing is serving /api/candidates/{id}/preview here, so every press would take a 404 and the
      // element would fire `error` -- a screenshot of the playing card showing none of the playing card.
      // So the whole clip is faked for this scenario only: `src` goes nowhere, `play` resolves, and a
      // clock runs for 30 s, dispatching the `timeupdate`s the page drives its rail and its numeral from
      // and the `ended` it fades on. That makes every state reachable by pressing the button -- the
      // sliver for the first quarter second, then the fill, then the fade. The harness is proving the
      // arrangement and the states, not the network behind them.
      Object.defineProperty(HTMLMediaElement.prototype, 'src', { set() { /* no-op */ }, get: () => '' })
      {
        let at = 0
        let clock = 0
        Object.defineProperty(HTMLMediaElement.prototype, 'currentTime', { get: () => at, set(v: number) { at = v } })
        Object.defineProperty(HTMLMediaElement.prototype, 'duration', { get: () => 30 })
        HTMLMediaElement.prototype.pause = function () { window.clearInterval(clock) }
        HTMLMediaElement.prototype.load = function () { window.clearInterval(clock); at = 0 }
        HTMLMediaElement.prototype.play = async function () {
          window.clearInterval(clock)
          at = 0
          clock = window.setInterval(() => {
            at = Math.min(30, at + 0.25)
            if (at >= 30) window.clearInterval(clock)
            this.dispatchEvent(new Event(at >= 30 ? 'ended' : 'timeupdate'))
          }, 250)
        }
      }
      break
    case 'failed':
      // The Failed tab is not the landing view, so a screenshot of it needs the chip pressed once the
      // queue has arrived. Left to the operator (or the screenshot script) rather than faked here: a
      // harness that forced the view would stop proving the chip reaches it.
      vi_spy(api, 'health', async () => health())
      vi_spy(api, 'library', async () => [])
      break
    default:
      vi_spy(api, 'health', async () => health())
      vi_spy(api, 'library', async () => [])
  }
}

const playlist: Playlist = { id: 1, source_url: 'https://open.spotify.com/playlist/x', name: 'Goa Set',
  created_at: '', updated_at: '', track_ids: [1], track_positions: [1], file: '/lib/Playlists/Goa Set.m3u8' }

function vi_spy<T extends object, K extends keyof T>(obj: T, key: K, impl: T[K]) {
  ;(obj as any)[key] = impl
}

const staged = scenario === 'stages' ? stageBundles() : []

scenarioSetup()

/* One `status` frame, after the first render has subscribed. `live` learns a transfer's position from the
   SSE stream and from nothing else, so without this the one downloading row draws an empty platter. */
if (scenario === 'stages') {
  const fetching = staged.find(b => b.request.state === 'fetching')!
  setTimeout(() => sources.forEach(es =>
    es.handlers['status']?.({ data: JSON.stringify({ fetch_progress: [stageProgress(fetching.request.id)] }) })), 200)
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>)
