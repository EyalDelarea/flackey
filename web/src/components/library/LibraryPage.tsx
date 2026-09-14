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
  const [refreshing, setRefreshing] = useState(false)
  const [format, setFormat] = useState('all')
  const [folder, setFolder] = useState('all')
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
  const relativeFolder = (t: Track) => {
    const root = s?.library_root.replace(/\/$/, '') ?? ''
    const relative = root && t.path.startsWith(root + '/') ? t.path.slice(root.length + 1) : t.path
    return relative.split('/').slice(0, -1).join('/') || 'Library root'
  }
  const folders = [...new Set(tracks.map(relativeFolder))].sort((a, b) => a.localeCompare(b))
  const formats = [...new Set(tracks.map(t => t.fmt.toUpperCase()))].sort()
  const visible = tracks.filter(t => (format === 'all' || t.fmt.toUpperCase() === format) &&
    (folder === 'all' || relativeFolder(t) === folder))
  const refresh = async () => {
    setRefreshing(true); setActionError(null)
    try {
      const result = await api.refreshLibrary()
      await live.refreshLibrary()
      setNote(result.removed ? `Removed ${result.removed} missing ${result.removed === 1 ? 'file' : 'files'} from the library.` : 'Library is up to date.')
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Couldn't refresh library. Try again.")
    } finally { setRefreshing(false) }
  }
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
      <button className="btn-secondary" onClick={refresh} disabled={refreshing}>{refreshing ? 'Refreshing…' : 'Refresh library'}</button>
      {s && <span className="counts">{s.tracks} tracks · {s.playlists} playlists · {gb(s.bytes)} on disk</span>}
      {s && <button className="btn-secondary" onClick={() => reveal(s.library_root)}>Open library folder</button>}
    </Toolbar>
    <div className="scroll">
      {queryError && <Banner tone="red" text={queryError} action={{ label: 'Dismiss', onClick: () => setQueryError(null) }} />}
      {actionError && <Banner tone="red" text={actionError} action={{ label: 'Dismiss', onClick: () => setActionError(null) }} />}
      {note && <Banner tone="amber" text={note} action={{ label: 'Dismiss', onClick: () => setNote(null) }} />}
      {pl && <PlaylistCard playlist={pl} count={pl.track_ids.length} onShowFile={() => reveal(pl.file)} />}
      <div className="library-filters" aria-label="Library filters">
        <label>Folder <span className="picker"><select value={folders.includes(folder) ? folder : 'all'} onChange={e => setFolder(e.target.value)}><option value="all">All folders</option>{folders.map(f => <option key={f} value={f}>{f}</option>)}</select></span></label>
        <label>Format <span className="picker"><select value={formats.includes(format) ? format : 'all'} onChange={e => setFormat(e.target.value)}><option value="all">All formats</option>{formats.map(f => <option key={f} value={f}>{f}</option>)}</select></span></label>
        <span className="counts">{visible.length} shown</span>
      </div>
      {folder === 'all' && !pl && !q
        ? folders.map(f => <section key={f} className="library-folder"><h2>{f} <span className="counts">{visible.filter(t => relativeFolder(t) === f).length}</span></h2><TrackTable tracks={visible.filter(t => relativeFolder(t) === f)} onReveal={reveal} onUpgrade={upgrade} /></section>)
        : <TrackTable tracks={visible} onReveal={reveal} onUpgrade={upgrade} />}
    </div>
  </>)
}
