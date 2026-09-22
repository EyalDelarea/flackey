import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './theme.css'
import { api } from './api'
import type { AppSettings, Health, Stats, Track, Bundle, Candidate, Playlist, Request, RequestState } from './api'

// The 'choose' scenario needs this instance after construction, to fire the 'queue' event by hand once a
// candidate is chosen -- there is no real server here to push it.
let lastEventSource: FakeEventSource | null = null
class FakeEventSource {
  onopen: (() => void) | null = null
  handlers: Record<string, (e: { data: string }) => void> = {}
  addEventListener(name: string, fn: (e: { data: string }) => void) { this.handlers[name] = fn }
  close() { /* no-op */ }
  constructor(_url: string) { lastEventSource = this }
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
const failure = (id: number, state: RequestState, over: Partial<Request> = {}, rest: Partial<Bundle> = {}): Bundle => ({
  request: {
    id, created_at: '2026-09-16T20:00:00Z', updated_at: '2026-09-16T20:05:00Z', kind: 'yt_track', state,
    raw_text: 'https://youtu.be/x', playlist_id: null, playlist_position: null, source_url: null,
    query_artist: 'Astral Projection', query_title: 'Into the Void', query_version: null, query_duration_s: 442,
    chosen_candidate_id: null, catalog_track_id: null, confidence: null, flag_reason: null, error_message: null,
    attempts: 0, retry_after: null, track_id: null, fetch_source: null, ...over,
  },
  candidates: [], catalog: null, track: null, rejection: null, ...rest,
})

const failures: Bundle[] = [
  failure(41, 'error', { query_artist: 'Ace Ventura', query_title: 'Presence', attempts: 3,
    error_message: 'the people who had it stopped sending part-way through' }),
  failure(42, 'not_found', { query_artist: 'Vibrasphere', query_title: 'Lime Twig',
    error_message: 'could not identify this track: no Deezer candidates and no Beatport match; no artist and title could be read from the request, so there is nothing to search for' }),
  /* The row issue #92 is about: genuine audio the fingerprint called a different recording. Its copy was
     kept, and the reference it was checked against is a Deezer preview, so both halves of the compare
     block are playable -- which is the whole state the screenshot has to show. */
  failure(43, 'rejected', { query_artist: 'Symbolic', query_title: 'Gravity Waves' }, {
    rejection: { id: 8, request_id: 43, reason: 'a different recording: best score 0.77 below 0.79',
      bitrate_kbps: 320, cutoff_hz: null, spectrogram_path: null, created_at: '2026-09-16T20:05:00Z',
      kind: 'different_recording', audio_path: '/data/rejected/req43-1758000000.flac' },
    reference: { kind: 'deezer', ref: '1109731', excerpt_start_s: 95 },
  }),
  failure(44, 'cancelled', { query_artist: 'Human Element', query_title: 'The Answer' }),
  failure(45, 'cancelled', { query_artist: 'Atmos', query_title: 'Klein Aber Doctor',
    flag_reason: 'Beatport unreachable, will retry' }),
]

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
    fetch_source: null, reviewed: 1, query_artist: null, query_title: null, query_duration_s: null,
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

const scenario = new URLSearchParams(window.location.search).get('scenario') ?? 'default'

function scenarioSetup() {
  vi_spy(api, 'settings', async () => settings)
  vi_spy(api, 'playlists', async () => scenario === 'library-with-playlist' ? [playlist] : [])
  vi_spy(api, 'stats', async () => stats)
  vi_spy(api, 'queue', async () => scenario === 'failed' ? failures : scenario === 'choose' ? choices : [] as Bundle[])
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
        lastEventSource?.handlers['queue']?.({ data: '' })
        return b.request
      })
      fakeAudio()
      break
    case 'failed':
      // The Failed tab is not the landing view, so a screenshot of it needs the chip pressed once the
      // queue has arrived. Left to the operator (or the screenshot script) rather than faked here: a
      // harness that forced the view would stop proving the chip reaches it.
      vi_spy(api, 'health', async () => health())
      vi_spy(api, 'library', async () => [])
      // The refused copy and the reference it was compared against are played through the same element
      // the candidate samples use, so this tab needs the same stand-in clip (issue #92).
      fakeAudio()
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

/* Nothing is serving audio here, so every press would take a 404 and the element would fire `error` -- a
   screenshot of the playing card showing none of the playing card. So the whole clip is faked: `src` goes
   nowhere, `play` resolves, and a clock runs for 30 s, dispatching the `timeupdate`s the page drives its
   rail and its numeral from and the `ended` it fades on. That makes every state reachable by pressing the
   button -- the sliver for the first quarter second, then the fill, then the fade. The harness is proving
   the arrangement and the states, not the network behind them. Called by whichever scenarios have
   something to play; the prototype is patched once either way. */
function fakeAudio() {
  Object.defineProperty(HTMLMediaElement.prototype, 'src', { set() { /* no-op */ }, get: () => '' })
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

scenarioSetup()

createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>)
