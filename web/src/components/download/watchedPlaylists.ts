import { useEffect, useState } from 'react'
import type { Bundle } from '../../api'
import { bucketOf } from '../../presentation'

const isFinished = (b: Bundle) => ['failed', 'done'].includes(bucketOf(b.request.state))

export interface WatchedPlaylists {
  /** Playlist id -> when it was first seen moving, which is where its finish card starts timing the run. */
  watched: Map<number, number>
  dismiss: (id: number) => void
}

/** Playlists seen moving since the app opened (idea 9). One of these that has stopped moving gets its
 *  finish card on the Downloads tab until the owner dismisses it; a batch that was already finished when
 *  the app opened is old news and gets none. App holds this rather than the Downloads page, which unmounts
 *  on every tab switch: checking the Library is the likeliest thing to do after a batch finishes, and the
 *  card has to still be there on the way back. */
export function useWatchedPlaylists(bundles: Bundle[]): WatchedPlaylists {
  const [watched, setWatched] = useState<Map<number, number>>(new Map())
  const moving = [...new Set(bundles.filter(b => b.request.playlist_id != null && !isFinished(b))
    .map(b => b.request.playlist_id!))].sort((a, c) => a - c)
  useEffect(() => {
    if (moving.some(id => !watched.has(id))) {
      const seen = Date.now()
      setWatched(w => new Map([...moving.map(id => [id, seen] as const), ...w]))
    }
  }, [moving.join(',')])
  const dismiss = (id: number) => setWatched(w => { const n = new Map(w); n.delete(id); return n })
  return { watched, dismiss }
}
