import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api'
import type { AppSettings, Bundle, FetchProgress, Health, Playlist, ProviderHealth, Stats } from './api'

export const UNREACHABLE = "Can't reach Flackey. Is `crate start` running?"

export interface Live {
  health: Health | null; bundles: Map<number, Bundle>; playlists: Playlist[]; stats: Stats | null; settings: AppSettings | null
  fetchProgress: FetchProgress[]
  libraryVersion: number; loadError: string | null; loading: boolean
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
  const firstOpen = useRef(true)

  const refreshLibrary = useCallback(async () => {
    const [p, s] = await Promise.all([api.playlists(), api.stats()])
    setPlaylists(p); setStats(s); setLibraryVersion(v => v + 1)
  }, [])
  const refresh = useCallback(async () => {
    try {
      const [h, q, st] = await Promise.all([api.health(), api.queue(), api.settings()])
      setHealth(h); setBundles(new Map(q.map(b => [b.request.id, b]))); setSettings(st)
      await refreshLibrary()
      setLoadError(null)
    } catch (e) {
      setLoadError(UNREACHABLE)
      throw e
    } finally {
      setLoading(false)
    }
  }, [refreshLibrary])
  const retry = useCallback(async () => { setLoadError(null); await refresh() }, [refresh])
  const dropBundle = useCallback((id: number) => {
    setBundles(prev => { const next = new Map(prev); next.delete(id); return next })
  }, [])

  useEffect(() => {
    refresh().catch(() => undefined)
    const es = new EventSource('/api/events')
    es.addEventListener('request', e => {
      const b = JSON.parse((e as MessageEvent).data) as Bundle
      setBundles(prev => { const next = new Map(prev); next.set(b.request.id, b); return next })
    })
    es.addEventListener('track', () => { refreshLibrary().catch(() => undefined) })
    es.addEventListener('queue', () => { api.queue().then(q => setBundles(new Map(q.map(b => [b.request.id, b])))).catch(() => undefined); refreshLibrary().catch(() => undefined) })
    es.addEventListener('status', e => {
      // The server's status dict is flat and /api/health is nested, so `lossless_provider` has to be
      // folded into `lossless.provider` by hand. Spreading it straight in put a stray top-level key on
      // the object and left health.lossless.provider frozen at whatever the last full refresh saw --
      // which is why the Soulseek line could sit on "Signing in…" long after the worker had signed in.
      const { lossless_provider: provider, fetch_progress: fetching, ...flags } =
        JSON.parse((e as MessageEvent).data) as Partial<Health> &
          { lossless_provider?: ProviderHealth | null; fetch_progress?: FetchProgress[] | null }
      if (fetching !== undefined) setFetchProgress(fetching ?? [])
      setHealth(prev => {
        if (!prev) return prev
        const next: Health = { ...prev, ...flags }
        if (provider !== undefined && prev.lossless) next.lossless = { ...prev.lossless, provider }
        return next
      })
    })
    es.onopen = () => { if (firstOpen.current) { firstOpen.current = false; return } refresh().catch(() => undefined) }
    return () => es.close()
  }, [refresh, refreshLibrary])

  return { health, bundles, playlists, stats, settings, fetchProgress, libraryVersion, loadError, loading, refresh, refreshLibrary, retry, setHealth, setSettings, dropBundle }
}
