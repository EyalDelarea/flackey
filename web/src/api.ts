export type RequestState = 'queued'|'identifying'|'awaiting_review'|'fetching'|'verifying'|'filing'|'done'|'duplicate'|'rejected'|'cancelled'|'not_found'|'error'
export interface Request { id:number; created_at:string; updated_at:string; raw_text:string; kind:string; state:RequestState;
  playlist_id:number|null; playlist_position:number|null; source_url:string|null; query_artist:string|null; query_title:string|null;
  query_version:string|null; query_duration_s:number|null; chosen_candidate_id:number|null; catalog_track_id:number|null;
  confidence:number|null; flag_reason:string|null; error_message:string|null; attempts:number; retry_after:string|null; track_id:number|null;
  fetch_source:string|null
  /** Where a run that ended in `error` stopped -- 'search' | 'choose' | 'download' | 'verify'. Stamped by
      `_set_state` from the state the row still held on its way into `error`, because the state itself is
      about to be overwritten and the stage is not recoverable after that. Null on rows written before the
      column existed, which read as Unknown rather than being guessed at. Required rather than optional: the serializer walks
      `dataclasses.fields`, so the key is always sent and is null only when unset, and a backend that
      stopped sending it would land every failed row in Unknown with nothing on screen saying so. That has
      to be a build break rather than a quiet one. (`reviewed` below is sent by the very same walk and is
      optional here anyway -- it predates this and is the weaker pattern, not the one to copy.) */
  failed_stage:string|null
  /** 1 once the request has been parked for the owner to choose; never cleared. The only durable record
      that a choice was ever asked for -- `flag_reason` is wiped when they answer. */
  reviewed?:number }
export interface Candidate { id:number; request_id:number; source:string; source_ref:string; artist:string; title:string; mix_name:string|null;
  duration_s:number|null; deezer_id:number|null; isrc:string|null; rank:number; score:number|null; catalog_track_id:number|null }
export interface Catalog { id:number; artist:string; title:string; mix_name:string; label:string; genre:string; isrc:string|null; sub_genre:string|null;
  catalog_number:string|null; release_name:string|null; release_date:string|null; bpm:number|null; key:string|null; duration_ms:number|null; artwork_url:string|null }
export interface TrackFormat { fmt:string; bit_depth:number|null; sample_rate:number|null; source:string; source_fmt:string|null; label:string }
export interface Track { id:number; path:string; fmt:string; bitrate_kbps:number; cutoff_hz:number; file_size:number; artist:string; title:string; mix_name:string;
  duration_s:number|null; isrc:string|null; catalog_track_id:number|null; request_id:number|null; added_at:string; verified_at:string|null; spectrogram_path:string|null;
  source:string; source_fmt:string|null; bit_depth:number|null; sample_rate:number|null; format?:TrackFormat; catalog:Catalog|null }
export interface Fingerprint { status:string; score:number|null; offset_s:number|null; reason:string|null }
export interface Attempt { id:number; request_id:number; provider:string; created_at:string; query:string; outcome:string|null;
  fingerprint:Fingerprint|null; spectrogram_path:string|null; first_byte_ms:number|null; total_ms:number|null }
/** `kind` says which check the file failed -- 'quality' (the spectral check) or 'different_recording'
    (genuine audio, wrong track). Optional so a fixture or a row written before the column existed still
    type-checks; absent reads as 'quality', which is all there was then. */
export interface Rejection { id:number; request_id:number; reason:string; bitrate_kbps:number|null; cutoff_hz:number|null; spectrogram_path:string|null; created_at:string; kind?:string }
export interface Bundle { request:Request; candidates:Candidate[]; catalog:Catalog|null; track:Track|null; rejection:Rejection|null; attempt?:Attempt|null }
export interface Playlist { id:number; source_url:string; name:string; created_at:string; updated_at:string; track_ids:number[]; track_positions?:number[]; file:string }
export interface Stats { tracks:number; bytes:number; playlists:number; rejections:number; library_root:string; playlist_dir:string; requests_by_state:Record<string,number> }
export interface ProviderHealth { name:string; status:string; username:string|null }
export interface LosslessHealth { enabled:boolean; provider:ProviderHealth|null; fpcalc:boolean;
  attempts_24h:Record<string,number>; raw_mb:number }
/** Whether other Soulseek users can open a connection back to this Mac. `reachable` is the only answer
    that matters; the addresses exist so the panel can tell the owner what to forward and where, and are
    null whenever the router, the network or the check itself would not say. */
export interface SharingState { port:number|null; enabled:boolean; checking:boolean; mapping:'natpmp'|'upnp'|null
  reachable:boolean|null; public_ip:string|null; lan_ip:string|null; gateway:string|null; checked_at:string|null; error:string|null }
export interface Health { ok:boolean; version:string; telegram_authorized:boolean; worker_running:boolean;
  setup_done:boolean; lossless?:LosslessHealth
  /** Whether this copy has Telegram API keys at all, and whether the owner left the source switched on.
      Optional so a fixture written before they existed still type-checks. */
  telegram_configured?:boolean; source_enabled?:boolean
  /** Pushed on every `status` event, so a panel reading it re-renders without polling. Null before the
      sharing loop has published anything, absent in fixtures written before it existed. */
  sharing?:SharingState|null }
export interface FetchProgress { request_id:number; bytes:number; size:number; peer:string; pct:number
  speed_bps:number; pick:number; state:string
  /** Set only after the transfer, while the file is being checked, fingerprinted or converted. */
  phase?:string }
export interface PortInfo { port:number; host:string; public:boolean }
export interface Ranking { max_picks:number; max_queue:number|null; fingerprint_min:number }
export interface AppSettings { library_root:string; data_dir:string; version:string; telegram_configured:boolean;
  log_path:string; soulseek_enabled?:boolean; slskd_url?:string; slskd_downloads_dir?:string;
  lossless_filing_format?:string; filing_formats?:string[]; auto_update_check?:boolean;
  ports?:{ app:PortInfo; sidecar:PortInfo; soulseek_listen:PortInfo }; ranking?:Ranking }
export interface Submission { summary:string; request_ids:number[]; playlist_id:number|null; name:string; total:number; already_in_library:number; already_queued:number }
export interface Tools { ffmpeg:boolean; ffprobe:boolean; yt_dlp:boolean }
export interface Upload { id:string|null; peer:string; file:string; folder:string; size:number; bytes:number; pct:number;
  state:string; speed_bps:number; started_at:string|null; ended_at:string|null }
export interface UploadSummary { total:number; active:number; completed:number; peers:number; bytes:number }
export interface UploadFeed { enabled:boolean; provider:string|null; uploads:Upload[]; summary:UploadSummary; error:string|null }
export interface TelegramStatus { authorized:boolean; configured:boolean; phone_masked:string|null }
export interface QrStart { id:string; url:string; expires_at:string }
export interface SoulseekSetup { configured: boolean; username: string | null }
export interface SoulseekPassword { username: string | null; password: string }
export interface SoulseekConnect { state: 'idle' | 'connecting' | 'connected' | 'failed'
  username: string | null; error: string | null }
export interface SlskdSetup { installed: boolean; running: boolean; version: string }
export interface SlskdProgress { state: 'idle' | 'downloading' | 'extracting' | 'done' | 'error'; done: number; total: number; error: string | null }
export interface UpdateStatus { ok:boolean; current:string; newer:boolean; available:boolean; latest:string|null;
  url:string|null; release_url:string|null; size:number|null; size_label:string|null; published_at:string|null;
  published_date:string|null; prerelease:boolean; error?:string
  /** Update replaces the app in place rather than opening the installer. False is the old flow. */
  seamless?:boolean; archive_url?:string|null; archive_size?:number|null; signature_url?:string|null }
/** Lives on the server, so leaving Settings mid-download and coming back finds it where it is. */
export interface UpdateDownload {
  state:'idle'|'downloading'|'verifying'|'staged'|'installing'|'ready'|'error'
  percent:number; received:number; total:number|null; version:string|null; path:string|null
  error:string|null
  seamless:boolean
  /** Transfers in flight, for the restart prompt: restarting loses them. */
  busy:number }

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message) }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { headers: { 'content-type': 'application/json' }, ...init })
  const body = res.status === 204 ? null : await res.json().catch(() => null)
  if (!res.ok) throw new ApiError(res.status, (body && body.detail) || res.statusText)
  return body as T
}
const post = <T,>(path: string, body?: unknown) => call<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })
const del = <T,>(path: string) => call<T>(path, { method: 'DELETE' })
/* For the handful of presses that act on the machine rather than on the library. Another site's page can
   make the browser POST to loopback, and CORS hides only the reply -- but it cannot invent a header
   without turning the request into a preflighted one, and that preflight is refused. The header's value
   carries nothing; that it is there at all is the whole signal. */
const appPost = <T,>(path: string) =>
  call<T>(path, { method: 'POST', headers: { 'content-type': 'application/json', 'x-flackey-app': '1' } })

export const api = {
  health: () => call<Health>('/api/health'),
  queue: () => call<Bundle[]>('/api/queue'),
  submit: (url: string) => post<Submission>('/api/requests', { url }),
  choose: (rid: number, cid: number) => post<Request>(`/api/requests/${rid}/choose/${cid}`),
  cancel: (rid: number) => post<Request>(`/api/requests/${rid}/cancel`),
  retry: (rid: number) => post<Request>(`/api/requests/${rid}/retry`),
  retryFailed: (ids: number[]) => post<{ retried: number[]; skipped: number[] }>('/api/requests/retry-failed', { ids }),
  removeRequest: (id: number) => del<{ ok: boolean }>('/api/requests/' + id),
  clearFailed: () => post<{ removed: number[] }>('/api/requests/clear-failed'),
  library: (q?: string, playlistId?: number | null) => {
    const p = new URLSearchParams()
    if (q) p.set('q', q)
    if (playlistId != null) p.set('playlist_id', String(playlistId))
    const qs = p.toString()
    return call<Track[]>(`/api/library${qs ? '?' + qs : ''}`)
  },
  refreshLibrary: () => post<{ removed: number }>('/api/library/refresh'),
  upgradeTrack: (trackId: number) =>
    post<{ ok: boolean; message: string; upgraded: boolean }>(`/api/lossless/upgrade/${trackId}`),
  playlists: () => call<Playlist[]>('/api/playlists'),
  stats: () => call<Stats>('/api/stats'),
  settings: () => call<AppSettings>('/api/settings'),
  update: () => call<UpdateStatus>('/api/update'),
  // No URL is passed: the server looks the release up again for itself, so a page left open on a stale
  // check cannot name what gets downloaded and opened. These two go through `appPost` because they are
  // wanted for their side effect, which CORS does not hide -- see `from_the_app` on the server.
  installUpdate: () => appPost<UpdateDownload>('/api/update/install'),
  updateProgress: () => call<UpdateDownload>('/api/update/progress'),
  openRelease: () => appPost<{ ok: boolean; url: string }>('/api/update/release'),
  // `appPost`: an app that quits itself at a stranger's choosing is not an improvement.
  restartForUpdate: () => appPost<UpdateDownload>('/api/update/restart'),
  saveSettings: (library_root: string, extra: Partial<{ lossless_filing_format: string; auto_update_check: boolean }> = {}) =>
    call<AppSettings>('/api/settings', { method: 'PUT', body: JSON.stringify({ library_root, ...extra }) }),
  reveal: (path: string) => post<{ ok: boolean }>('/api/reveal', { path }),
  pickFolder: (initial: string | null) => post<{ path: string | null }>('/api/pick-folder', { initial }),
  pickFolderAvailable: () => call<{ available: boolean }>('/api/pick-folder/available'),
  tools: () => call<Tools>('/api/tools'),
  setupDone: () => post<{ setup_done: boolean }>('/api/setup/done'),
  setupReset: () => post<{ setup_done: boolean }>('/api/setup/reset'),
  telegramStatus: () => call<TelegramStatus>('/api/telegram/status'),
  telegramKeys: (api_id: string, api_hash: string) => post<{ configured: boolean }>('/api/telegram/keys', { api_id, api_hash }),
  telegramSource: (enabled: boolean) => post<{ source_enabled: boolean }>('/api/telegram/source', { enabled }),
  skipTelegram: () => post<{ source_enabled: boolean }>('/api/telegram/skip'),
  qrStart: () => post<QrStart>('/api/telegram/qr'),
  qrState: (id: string) => call<{ state: 'waiting' | 'password_needed' | 'done' | 'expired' | 'unknown' }>(`/api/telegram/qr/${id}`),
  password: (password: string) => post<{ state: string }>('/api/telegram/password', { password }),
  sendCode: (phone: string) => post<{ ok: boolean }>('/api/telegram/phone', { phone }),
  signIn: (phone: string, code: string) => post<{ state: 'done' | 'password_needed' }>('/api/telegram/code', { phone, code }),
  logout: () => post<{ ok: boolean }>('/api/telegram/logout'),
  uploads: () => call<UploadFeed>('/api/lossless/uploads'),
  soulseekSetup: () => call<SoulseekSetup>('/api/setup/soulseek'),
  saveSoulseek: (username: string, password: string) =>
    post<{ ok: boolean; restart_required: boolean; connecting: boolean }>('/api/setup/soulseek', { username, password }),
  // Its own path, not a field on soulseekSetup: that one is polled, and a secret must not ride along
  // on a poll. It exists at all because Soulseek has no password reset -- see the route's docstring.
  soulseekPassword: () => call<SoulseekPassword>('/api/setup/soulseek/password'),
  slskdCredentials: () => call<{ username: string; password: string }>('/api/setup/slskd/credentials'),
  soulseekConnectStatus: () => call<SoulseekConnect>('/api/setup/soulseek/status'),
  connectSoulseek: () => post<SoulseekConnect>('/api/setup/soulseek/connect'),
  slskdSetup: () => call<SlskdSetup>('/api/setup/slskd'),
  installSlskd: () => post<{ state: SlskdProgress['state'] }>('/api/setup/slskd'),
  slskdProgress: () => call<SlskdProgress>('/api/setup/slskd/progress'),
  sharing: () => call<SharingState>('/api/sharing'),
  // Answers with `checking: true` and does the work behind it: the result arrives on the status event,
  // not in this response. 409 when there is no Soulseek account to share anything from.
  checkSharing: () => post<SharingState>('/api/sharing/check'),
}
export const spectrogramUrl = (rejectionId: number) => `/api/rejections/${rejectionId}/spectrogram.png`
