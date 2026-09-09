import { useState } from 'react'
import Artwork from '../download/Artwork'
import Icon from '../Icon'
import type { Track } from '../../api'

/** A track whose `source_fmt` is unset was filed from the lossy Deezer copy: no lossless provider supplied
    it. That is the state the owner wants to act on, and it names no provider, so it stays true for the next
    one. Every other row is the verified lossless file and gets the green check. */
const isLossy = (t: Track) => !t.source_fmt

export default function TrackTable({ tracks, onReveal, onUpgrade }: {
  tracks: Track[]; onReveal: (path: string) => void
  onUpgrade?: (t: Track) => Promise<void>
}) {
  const [selected, setSelected] = useState<number | null>(null)
  const [busy, setBusy] = useState<number | null>(null)
  const upgrade = async (e: React.MouseEvent, t: Track) => {
    e.stopPropagation()
    if (!onUpgrade || busy !== null) return
    setBusy(t.id)
    try { await onUpgrade(t) } finally { setBusy(null) }
  }
  return (<div className="group table">
    <div className="thead"><span /><span>Track</span><span>Genre</span><span>Label</span><span>Year</span><span className="num">Kbps</span><span /></div>
    {tracks.map(t => {
      const c = t.catalog
      const lossy = isLossy(t)
      return (<div className={`trow${selected === t.id ? ' selected' : ''}`} key={t.id} onClick={() => setSelected(t.id)} onDoubleClick={() => onReveal(t.path)}>
        <Artwork url={c?.artwork_url ?? null} small />
        <div className="cell"><div className="t">{t.artist} – {t.title}</div><div className="v">{t.mix_name}</div></div>
        <span>{c?.genre ?? 'Unknown'}</span><span className="ellipsis">{c?.label ?? 'Unknown'}</span><span>{c?.release_date?.slice(0, 4) ?? ''}</span>
        <span className={lossy ? 'kbps lossy' : 'kbps'} title={lossy ? 'Filed from Deezer — no lossless copy was obtained' : undefined}>
          {t.bitrate_kbps}{!lossy && t.verified_at && <Icon name="check" size={12} stroke={2.4} />}
        </span>
        <span className="reveal">
          {lossy && onUpgrade && (
            <button className="btn-secondary" disabled={busy !== null} onClick={e => upgrade(e, t)}>
              {busy === t.id ? 'Searching…' : 'Find lossless'}
            </button>
          )}
          <button className="btn-secondary" onClick={e => { e.stopPropagation(); onReveal(t.path) }}>Show in Finder</button>
        </span>
      </div>)
    })}
    {tracks.length === 0 && <div className="empty">Nothing here yet.</div>}
  </div>)
}
