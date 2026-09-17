import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './theme.css'
import { api } from './api'
import type { AppSettings, Health, Stats, Track, Bundle, Playlist } from './api'

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
  ranking: { max_picks: 4, duration_tolerance_s: 3, title_ratio: 90, require_artist: false,
    max_queue: null, fingerprint_min: 0.9 },
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

const scenario = new URLSearchParams(window.location.search).get('scenario') ?? 'default'

function scenarioSetup() {
  vi_spy(api, 'settings', async () => settings)
  vi_spy(api, 'playlists', async () => scenario === 'library-with-playlist' ? [playlist] : [])
  vi_spy(api, 'stats', async () => stats)
  vi_spy(api, 'queue', async () => [] as Bundle[])
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
