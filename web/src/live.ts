import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError } from './api'
import type { AppSettings, Bundle, FetchProgress, Health, Playlist, ProviderHealth, Stats, UpdateDownload, UpdateStatus } from './api'

const UNREACHABLE = "Can't reach Flackey. Is `flackey start` running?"

/** Only a request that never got an answer means Flackey is unreachable. An ApiError is the server
 *  answering -- a 500, a 403 -- so it says what the server said (WEB-1). */
function loadErrorOf(e: unknown): string {
  if (!(e instanceof ApiError)) return UNREACHABLE
  return e.message || `Flackey hit an error (${e.status}). Try again.`
}

/** One malformed frame must not throw out of the handler and take the rest of the event with it. */
function parse<T>(data: string): T | null {
  try { return JSON.parse(data) as T } catch { return null }
}

export interface Live {
  health: Health | null; bundles: Map<number, Bundle>; playlists: Playlist[]; stats: Stats | null; settings: AppSettings | null
  fetchProgress: FetchProgress[]
  libraryVersion: number; loadError: string | null; loading: boolean; connected: boolean; lastSeen: Date | null
  upgradeActivity: string | null; setUpgradeActivity: (message: string | null) => void
  // Checked once per launch, not on every reconnect -- a background version check has no business
  // repeating every time the SSE connection blips. Stays null when `auto_update_check` is off.
  update: UpdateStatus | null
  // The installer download, owned by the server. Null until something starts one; the stream seeds a
  // freshly opened page with whatever it was already doing.
  updateDownload: UpdateDownload | null
  refresh: () => Promise<void>; refreshLibrary: () => Promise<void>; retry: () => Promise<void>
  setHealth: (h: Health) => void; setSettings: (s: AppSettings) => void; dropBundle: (id: number) => void
}

export function useLive(): Live {
  const [health, setHealth] = useState<Health | null>(null)
  const [fetchProgress, setFetchProgress] = useState<FetchProgress[]>([])
  const [bundles, setBundles] = useState<Map<number, Bundle>>(new Map())
  const [playlists, setPlaylists] = useState<Playlist[]>([])
  const [stats, setStats] = useState<Stats | null>(null)
  const [settings, setSettings] = useState<AppSettings | null>(null)
  const [libraryVersion, setLibraryVersion] = useState(0)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [upgradeActivity, setUpgradeActivity] = useState<string | null>(null)
  const [connected, setConnected] = useState(false)
  const [lastSeen, setLastSeen] = useState<Date | null>(null)
  const [update, setUpdate] = useState<UpdateStatus | null>(null)
  const [updateDownload, setUpdateDownload] = useState<UpdateDownload | null>(null)
  const updateChecked = useRef(false)
  const firstOpen = useRef(true)
  const staleTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  // Every write of a whole queue snapshot takes a ticket when its fetch starts, and only the newest ticket
  // may land: an older /api/queue answer arriving last would otherwise put rows back to a stage they have
  // already left. Row events that arrive while a snapshot is in flight are newer than it, so they are kept
  // and laid back over it when it lands (WEB-3).
  const queueTicket = useRef(0)
  const rowsSinceSnapshot = useRef(new Map<number, Bundle>())
  const libraryTicket = useRef(0)
  const startSnapshot = () => { rowsSinceSnapshot.current.clear(); return ++queueTicket.current }
  const applySnapshot = useCallback((ticket: number, q: Bundle[]) => {
    if (ticket !== queueTicket.current) return
    const next = new Map(q.map(b => [b.request.id, b]))
    rowsSinceSnapshot.current.forEach((b, id) => next.set(id, b))
    rowsSinceSnapshot.current.clear()
    setBundles(next)
  }, [])
  const markAlive = useCallback(() => {
    if (staleTimer.current) { clearTimeout(staleTimer.current); staleTimer.current = null }
    setConnected(true); setLastSeen(new Date())
  }, [])

  const refreshLibrary = useCallback(async () => {
    const ticket = ++libraryTicket.current
    const [p, s] = await Promise.all([api.playlists(), api.stats()])
    if (ticket !== libraryTicket.current) return
    setPlaylists(p); setStats(s); setLibraryVersion(v => v + 1)
  }, [])
  const refresh = useCallback(async () => {
    try {
      const ticket = startSnapshot()
      // The queue lands as soon as it answers: having taken the newest ticket, it must not be lost just
      // because health or settings failed beside it.
      const [h, , st] = await Promise.all([api.health(), api.queue().then(q => applySnapshot(ticket, q)), api.settings()])
      setHealth(h); setSettings(st)
      markAlive()
      await refreshLibrary()
      setLoadError(null)
    } catch (e) {
      // A 401 is a page opened without the launch token, not a server that is down: say how to get in.
      setLoadError(loadErrorOf(e))
      throw e
    } finally {
      setLoading(false)
    }
  }, [refreshLibrary, applySnapshot])
  const retry = useCallback(async () => { setLoadError(null); await refresh() }, [refresh])

  useEffect(() => {
    if (!settings || updateChecked.current) return
    updateChecked.current = true
    if (settings.auto_update_check === false) return
    api.update('auto').then(setUpdate).catch(() => undefined)
  }, [settings])
  // The event bus drops events rather than blocking when a subscriber falls behind, and the frame it
  // drops could be the last one -- a `ready` or `error` lost that way would leave the button disabled on
  // a download that had already finished. Polling while one is running is the backstop for exactly that,
  // and it stops the moment the download does.
  const downloadState = updateDownload?.state
  useEffect(() => {
    if (downloadState !== 'downloading') return
    const id = setInterval(() => { api.updateProgress().then(setUpdateDownload).catch(() => undefined) }, 3000)
    return () => clearInterval(id)
  }, [downloadState])
  const dropBundle = useCallback((id: number) => {
    rowsSinceSnapshot.current.delete(id)
    setBundles(prev => { const next = new Map(prev); next.delete(id); return next })
  }, [])

  useEffect(() => {
    refresh().catch(() => undefined)
    const es = new EventSource('/api/events')
    es.addEventListener('request', e => {
      const b = parse<Bundle>((e as MessageEvent).data)
      markAlive()
      if (!b) return
      rowsSinceSnapshot.current.set(b.request.id, b)
      setBundles(prev => { const next = new Map(prev); next.set(b.request.id, b); return next })
    })
    es.addEventListener('track', () => { markAlive(); refreshLibrary().catch(() => undefined) })
    es.addEventListener('queue', () => { markAlive(); const ticket = startSnapshot(); api.queue().then(q => applySnapshot(ticket, q)).catch(() => undefined); refreshLibrary().catch(() => undefined) })
    es.addEventListener('status', e => {
      // The server's status dict is flat and /api/health is nested, so `lossless_provider` has to be
      // folded into `lossless.provider` by hand. Spreading it straight in put a stray top-level key on
      // the object and left health.lossless.provider frozen at whatever the last full refresh saw --
      // which is why the Soulseek line could sit on "Signing in…" long after the worker had signed in.
      const status = parse<Partial<Health> &
          { lossless_provider?: ProviderHealth | null; fetch_progress?: FetchProgress[] | null
            update_download?: UpdateDownload | null }>((e as MessageEvent).data)
      if (!status) { markAlive(); return }
      const { lossless_provider: provider, fetch_progress: fetching, update_download: downloading, ...flags } = status
      if (fetching !== undefined) setFetchProgress(fetching ?? [])
      if (downloading !== undefined) setUpdateDownload(downloading ?? null)
      markAlive()
      setHealth(prev => {
        if (!prev) return prev
        const next: Health = { ...prev, ...flags }
        if (provider !== undefined && prev.lossless) next.lossless = { ...prev.lossless, provider }
        return next
      })
    })
    es.onopen = () => { markAlive(); if (firstOpen.current) { firstOpen.current = false; return } refresh().catch(() => undefined) }
    // EventSource fires `error` on every transient retry attempt, not just real outages, so a short
    // grace period keeps the reconnecting banner from flashing during routine reconnects.
    es.onerror = () => {
      if (staleTimer.current) return
      staleTimer.current = setTimeout(() => { setConnected(false); staleTimer.current = null }, 5000)
    }
    return () => { if (staleTimer.current) clearTimeout(staleTimer.current); es.close() }
  }, [refresh, refreshLibrary, applySnapshot])

  return { health, bundles, playlists, stats, settings, fetchProgress, libraryVersion, loadError, loading, connected, lastSeen, upgradeActivity, setUpgradeActivity, update, updateDownload, refresh, refreshLibrary, retry, setHealth, setSettings, dropBundle }
}
