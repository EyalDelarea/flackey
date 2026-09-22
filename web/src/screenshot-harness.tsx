import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './theme.css'
import { api } from './api'
import type { AppSettings, Health, Stats, Track, Bundle, Playlist, Request, RequestState } from './api'

const sources: FakeEventSource[] = []
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

const scenario = new URLSearchParams(window.location.search).get('scenario') ?? 'default'

function scenarioSetup() {
  vi_spy(api, 'settings', async () => settings)
  vi_spy(api, 'playlists', async () => scenario === 'library-with-playlist' ? [playlist] : scenario === 'stages' ? [goaPlaylist] : [])
  vi_spy(api, 'stats', async () => stats)
  vi_spy(api, 'queue', async () => scenario === 'failed' ? failures : scenario === 'stages' ? staged : [] as Bundle[])
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
