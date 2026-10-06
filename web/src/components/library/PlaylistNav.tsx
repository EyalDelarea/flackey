import { useState } from 'react'
import Icon from '../Icon'
import type { Playlist } from '../../api'

/* Every album link becomes its own playlist, so a few downloads bury the hand-made playlists under a
   wall of "Album - …" rows. The link says which kind it is; the name is only a fallback. */
const ALBUM_PREFIX = /^album\s*[-–—]\s*/i
export const isAlbum = (p: Playlist) => /\/album\//.test(p.source_url) || ALBUM_PREFIX.test(p.name)
/* The search box earns its place only once the list is long enough to scroll. */
const SEARCH_FROM = 8
const COLLAPSED_KEY = 'flackey.albumsCollapsed'

function readCollapsed() {
  try { return localStorage.getItem(COLLAPSED_KEY) === '1' } catch { return false }
}

export default function PlaylistNav({ playlists, selected, onSelect }: { playlists: Playlist[]; selected: number | null; onSelect: (id: number | null) => void }) {
  const [query, setQuery] = useState('')
  const [collapsed, setCollapsed] = useState(readCollapsed)
  const toggle = () => {
    const next = !collapsed
    setCollapsed(next)
    try { localStorage.setItem(COLLAPSED_KEY, next ? '1' : '0') } catch { /* a convenience, not state */ }
  }
  const q = query.trim().toLowerCase()
  const shown = q ? playlists.filter(p => p.name.toLowerCase().includes(q)) : playlists
  const own = shown.filter(p => !isAlbum(p))
  // Newest first, so the album that just finished is at the top rather than past the bottom edge.
  const albums = shown.filter(isAlbum).sort((a, b) => b.created_at.localeCompare(a.created_at))
  const open = !collapsed || !!q
  const item = (p: Playlist, name: string) => <button key={p.id} className={`pl-item${selected === p.id ? ' active' : ''}`} onClick={() => onSelect(p.id)} title={p.name}><Icon name="playlist" size={15} /><span className="ellipsis">{name}</span></button>
  return (<div className="pl-nav">
    <div className="pl-label">Playlists</div>
    {playlists.length >= SEARCH_FROM && <div className="pl-search"><Icon name="search" size={13} />
      <input className="input" type="search" placeholder="Find a playlist" aria-label="Find a playlist" value={query} onChange={e => setQuery(e.target.value)} /></div>}
    <div className="pl-list">
      {!q && <button className={`pl-item${selected == null ? ' active' : ''}`} onClick={() => onSelect(null)}><Icon name="playlist" size={15} />All tracks</button>}
      {own.map(p => item(p, p.name))}
      {albums.length > 0 && <>
        <button className={`pl-group${open ? ' open' : ''}`} aria-expanded={open} onClick={toggle} disabled={!!q}>
          {!q && <Icon name="chevron" size={11} stroke={2} />}Albums<span className="pl-count">{albums.length}</span></button>
        {open && albums.map(p => item(p, p.name.replace(ALBUM_PREFIX, '') || p.name))}
      </>}
      {q && shown.length === 0 && <div className="pl-empty">No playlist matches “{query.trim()}”</div>}
    </div>
  </div>)
}
