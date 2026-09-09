import Icon from '../Icon'
import type { Playlist } from '../../api'
export default function PlaylistNav({ playlists, selected, onSelect }: { playlists: Playlist[]; selected: number | null; onSelect: (id: number | null) => void }) {
  return (<div className="pl-nav">
    <div className="pl-label">Playlists</div>
    <button className={`pl-item${selected == null ? ' active' : ''}`} onClick={() => onSelect(null)}><Icon name="playlist" size={15} />All tracks</button>
    {playlists.map(p => <button key={p.id} className={`pl-item${selected === p.id ? ' active' : ''}`} onClick={() => onSelect(p.id)} title={p.name}><Icon name="playlist" size={15} /><span className="ellipsis">{p.name}</span></button>)}
  </div>)
}
