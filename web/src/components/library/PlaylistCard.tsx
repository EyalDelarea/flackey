import Icon from '../Icon'
import type { Playlist } from '../../api'
import { plural } from '../../presentation'
export default function PlaylistCard({ playlist, count, onShowFile }: { playlist: Playlist; count: number; onShowFile: () => void }) {
  return (<div className="group pl-card">
    <span className="pl-icon"><Icon name="playlist" size={22} /></span>
    <div className="how"><h2>{playlist.name} <span>· {plural(count, 'track')}</span></h2>In Rekordbox: File → Import → Playlist, then choose this file. Safe to re-import after new tracks are added.</div>
    <button className="btn-secondary" onClick={onShowFile}>Show playlist file</button>
  </div>)
}
