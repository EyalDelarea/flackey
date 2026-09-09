import { useEffect, useRef, useState } from 'react'
import { ApiError, api } from '../../api'
import type { Track } from '../../api'
import type { Live } from '../../live'
import { gb } from '../../presentation'
import Icon from '../Icon'
import Banner from '../Banner'
import Toolbar from '../Toolbar'
import PlaylistCard from './PlaylistCard'
import TrackTable from './TrackTable'

export default function LibraryPage({ live, selectedPlaylist, inset }: { live: Live; selectedPlaylist: number | null; inset?: boolean }) {
  const [q, setQ] = useState(''); const [tracks, setTracks] = useState<Track[]>([])
  const [actionError, setActionError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [queryError, setQueryError] = useState<string | null>(null)
  const requestCounter = useRef(0)

  useEffect(() => {
    const currentRequest = ++requestCounter.current
    const t = setTimeout(() => {
      api.library(q, selectedPlaylist)
        .then(result => {
          if (currentRequest === requestCounter.current) {
            setTracks(result)
            setQueryError(null)
          }
        })
        .catch(err => {
          if (currentRequest === requestCounter.current) {
            setQueryError(err instanceof ApiError ? err.message : "Couldn't load library. Try again.")
          }
        })
    }, q ? 200 : 0)
    return () => clearTimeout(t)
  }, [q, selectedPlaylist, live.libraryVersion])

  const s = live.stats; const pl = live.playlists.find(p => p.id === selectedPlaylist) ?? null
  const reveal = (p: string) => {
    api.reveal(p)
      .then(() => setActionError(null))
      .catch(err => setActionError(err instanceof ApiError ? err.message : "Couldn't open folder. Try again."))
  }

  // A found copy re-paths the file, so the row must be re-read rather than patched in place; a miss says
  // why in the same banner instead of leaving the click looking like it did nothing.
  const upgrade = async (t: Track) => {
    setNote(null); setActionError(null)
    try {
      const res = await api.upgradeTrack(t.id)
      setNote(`${t.artist} – ${t.title}: ${res.message}`)
      if (res.upgraded) setTracks(await api.library(q, selectedPlaylist))
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Couldn't search for a lossless copy. Try again.")
    }
  }

  return (<>
    <Toolbar inset={inset}>
      <div className="search"><Icon name="search" size={14} /><input className="input" placeholder="Search" value={q} onChange={e => setQ(e.target.value)} aria-label="search library" /></div>
      {s && <span className="counts">{s.tracks} tracks · {s.playlists} playlists · {gb(s.bytes)} on disk</span>}
      {s && <button className="btn-secondary" onClick={() => reveal(s.library_root)}>Open library folder</button>}
    </Toolbar>
    <div className="scroll">
      {queryError && <Banner tone="red" text={queryError} action={{ label: 'Dismiss', onClick: () => setQueryError(null) }} />}
      {actionError && <Banner tone="red" text={actionError} action={{ label: 'Dismiss', onClick: () => setActionError(null) }} />}
      {note && <Banner tone="amber" text={note} action={{ label: 'Dismiss', onClick: () => setNote(null) }} />}
      {pl && <PlaylistCard playlist={pl} count={pl.track_ids.length} onShowFile={() => reveal(pl.file)} />}
      <TrackTable tracks={tracks} onReveal={reveal} onUpgrade={upgrade} />
    </div>
  </>)
}
