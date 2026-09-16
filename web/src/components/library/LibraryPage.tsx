import { useEffect, useRef, useState } from 'react'
import { ApiError, api } from '../../api'
import type { Bundle, Track } from '../../api'
import type { Live } from '../../live'
import { gb } from '../../presentation'
import Icon from '../Icon'
import Banner from '../Banner'
import Toolbar from '../Toolbar'
import PlaylistCard from './PlaylistCard'
import TrackTable from './TrackTable'

const ACTIVE_STATES = new Set(['queued', 'identifying', 'awaiting_review', 'fetching', 'verifying', 'filing'])
const FAILED_STATES = new Set(['not_found', 'error', 'rejected', 'cancelled'])
const LABEL: Record<string, string> = {
  queued: 'Queued', identifying: 'Searching', awaiting_review: 'Needs your choice', fetching: 'Getting file',
  verifying: 'Checking file', filing: 'Filing', not_found: 'No match found', error: 'Failed',
  rejected: 'Rejected', cancelled: 'Cancelled',
}

function PlaylistImportStatus({ bundles, playlistId, filedPositions = [], onRetry }: {
  bundles: Bundle[]; playlistId: number; filedPositions?: number[]; onRetry: (id: number) => void
}) {
  const filed = new Set(filedPositions)
  const latest = new Map<number, Bundle>()
  for (const b of bundles) {
    const r = b.request
    if (r.playlist_id !== playlistId || r.playlist_position == null) continue
    const old = latest.get(r.playlist_position)
    if (!old || r.id > old.request.id) latest.set(r.playlist_position, b)
  }
  const unresolved = [...latest.values()].filter(b => !filed.has(b.request.playlist_position!) &&
      (ACTIVE_STATES.has(b.request.state) || FAILED_STATES.has(b.request.state)))
    .sort((a, b) => a.request.playlist_position! - b.request.playlist_position!)
  const total = Math.max(0, ...filedPositions, ...latest.keys())
  if (!total) return null
  const active = unresolved.filter(b => ACTIVE_STATES.has(b.request.state)).length
  const failed = unresolved.length - active
  return <section className="group playlist-import-status" aria-label="Playlist import status">
    <h2>Import status <span>{filed.size} of {total} in library{active ? ` · ${active} processing` : ''}{failed ? ` · ${failed} not in library` : ''}</span></h2>
    {unresolved.length
      ? <><p>These entries haven’t been added yet. Completed entries are listed in the playlist above.</p>
        <ul>{unresolved.map(b => <li key={b.request.id}>
          <span className="ellipsis">{b.request.raw_text || b.request.query_title || 'Untitled track'}
            {(b.request.error_message || b.request.flag_reason) && <small className="playlist-status-reason">
              {b.request.state === 'queued' ? 'Previous attempt: ' : ''}{b.request.error_message || b.request.flag_reason}
            </small>}
          </span>
          <span className={FAILED_STATES.has(b.request.state) ? 'rej' : 'needs'}>{LABEL[b.request.state] ?? b.request.state}</span>
          {['not_found', 'error'].includes(b.request.state) && <button className="btn-secondary" onClick={() => onRetry(b.request.id)}>Retry</button>}
        </li>)}</ul></>
      : <p>All playlist entries are in the library.</p>}
  </section>
}

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
  // A folder or format the last result set offered can vanish from a new one (a different search, a
  // playlist switch). Left alone, the picker falls back to displaying "All ..." while `folder`/`format`
  // still hold the stale value, so the filter below silently keeps applying it -- the query looks
  // unrestricted but returns nothing. Resetting the state itself, not just its display, keeps them in sync.
  useEffect(() => { if (folder !== 'all' && !folders.includes(folder)) setFolder('all') }, [folders, folder])
  useEffect(() => { if (format !== 'all' && !formats.includes(format)) setFormat('all') }, [formats, format])
  const visible = tracks.filter(t => (format === 'all' || t.fmt.toUpperCase() === format) &&
    (folder === 'all' || relativeFolder(t) === folder))
  const filtered = q || folder !== 'all' || format !== 'all'
  const clearFilters = () => { setQ(''); setFolder('all'); setFormat('all') }
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
    live.setUpgradeActivity(`Searching for a lossless copy of ${t.artist} – ${t.title}…`)
    setNote(`Searching for a lossless copy of ${t.artist} – ${t.title}…`); setActionError(null)
    try {
      const res = await api.upgradeTrack(t.id)
      setNote(`${t.artist} – ${t.title}: ${res.message}`)
      if (res.upgraded) setTracks(await api.library(q, selectedPlaylist))
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Couldn't search for a lossless copy. Try again.")
    } finally {
      live.setUpgradeActivity(null)
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
      {pl && <PlaylistImportStatus bundles={[...live.bundles.values()]} playlistId={pl.id} filedPositions={pl.track_positions} onRetry={id => {
        api.retry(id).then(() => live.refresh()).catch(err => setActionError(err instanceof ApiError ? err.message : "Couldn't retry this track. Try again."))
      }} />}
      <div className="library-filters" aria-label="Library filters">
        <label>Folder <span className="picker"><select value={folder} onChange={e => setFolder(e.target.value)}><option value="all">All folders</option>{folders.map(f => <option key={f} value={f}>{f}</option>)}</select></span></label>
        <label>Format <span className="picker"><select value={format} onChange={e => setFormat(e.target.value)}><option value="all">All formats</option>{formats.map(f => <option key={f} value={f}>{f}</option>)}</select></span></label>
        <span className="counts">{visible.length} shown</span>
        {filtered && <button className="btn-link" onClick={clearFilters}>Clear filters</button>}
      </div>
      {tracks.length === 0
        ? <div className="empty">{filtered
            ? <>No tracks match these filters. <button className="btn-link" onClick={clearFilters}>Clear filters</button></>
            : 'Your finished tracks will appear here. Paste a link on the Downloads tab to add your first one.'}</div>
        : visible.length === 0
        ? <div className="empty">No tracks match these filters. <button className="btn-link" onClick={clearFilters}>Clear filters</button></div>
        : folder === 'all' && !pl && !q
        ? folders.map(f => <section key={f} className="library-folder"><h2>{f} <span className="counts">{visible.filter(t => relativeFolder(t) === f).length}</span></h2><TrackTable tracks={visible.filter(t => relativeFolder(t) === f)} onReveal={reveal} onUpgrade={upgrade} /></section>)
        : <TrackTable tracks={visible} onReveal={reveal} onUpgrade={upgrade} />}
    </div>
  </>)
}
