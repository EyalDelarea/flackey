import Icon from '../Icon'
import { usePlatform } from '../../platform'

/** What Download shows before the first link (UI proposal 5): what Flackey does, in one line, and which
 *  links it takes. The chips are labels, not buttons -- the field above is where a link goes. */
export default function EmptyDownloads() {
  const platform = usePlatform()
  const paste = platform === 'mac' ? '⌘V' : 'Ctrl+V'
  return (
    <div className="empty empty-downloads">
      <span className="empty-icon"><Icon name="download" size={40} stroke={1.4} /></span>
      <h2>Nothing downloading yet</h2>
      <p>Paste a track or a whole playlist link above and Flackey finds a lossless copy, checks it and files it in your library.</p>
      <ul className="empty-chips" aria-label="Links Flackey takes">
        <li>YouTube</li><li>YouTube Music</li><li>Spotify</li>
        <li className="shortcut">{paste} to paste</li>
      </ul>
    </div>
  )
}
