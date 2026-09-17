import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './theme.css'
import { api } from './api'
import type { AppSettings, Health, Stats, Track, Bundle, Playlist, Request, RequestState } from './api'

class FakeEventSource {
  onopen: (() => void) | null = null
  handlers: Record<string, (e: { data: string }) => void> = {}
  addEventListener(name: string, fn: (e: { data: string }) => void) { this.handlers[name] = fn }
  close() { /* no-op */ }
  constructor(_url: string) { /* no-op */ }
}
;(window as any).EventSource = FakeEventSource

const settings: AppSettings = {
  library_root: '/Users/dj/Music/Flackey', data_dir: '/data', version: '0.1.0',
  telegram_configured: true, log_path: '/data/flackey.log',
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
    attempts: 0, retry_after: null, track_id: null, fetch_source: null, ...over,
  },
  candidates: [], catalog: null, track: null, rejection: null,
})

const failures: Bundle[] = [
  failure(41, 'error', { query_artist: 'Ace Ventura', query_title: 'Presence', attempts: 3,
    error_message: 'the people who had it stopped sending part-way through' }),
  failure(42, 'not_found', { query_artist: 'Vibrasphere', query_title: 'Lime Twig',
    error_message: 'neither Deezer nor Beatport has a match for it' }),
  failure(43, 'rejected', { query_artist: 'Symbolic', query_title: 'Gravity Waves' }),
  failure(44, 'cancelled', { query_artist: 'Human Element', query_title: 'The Answer' }),
  failure(45, 'cancelled', { query_artist: 'Atmos', query_title: 'Klein Aber Doctor',
    flag_reason: 'Beatport unreachable, will retry' }),
]

const scenario = new URLSearchParams(window.location.search).get('scenario') ?? 'default'

function scenarioSetup() {
  vi_spy(api, 'settings', async () => settings)
  vi_spy(api, 'playlists', async () => scenario === 'library-with-playlist' ? [playlist] : [])
  vi_spy(api, 'stats', async () => stats)
  vi_spy(api, 'queue', async () => scenario === 'failed' ? failures : [] as Bundle[])
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

scenarioSetup()

createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>)
