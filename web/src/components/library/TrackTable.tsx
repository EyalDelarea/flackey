import { useState } from 'react'
import Artwork from '../download/Artwork'
import Icon from '../Icon'
import type { Track } from '../../api'

/** A track whose `source_fmt` is unset was filed from the lossy Deezer copy: no lossless provider supplied
    it. That is the state the owner wants to act on, and it names no provider, so it stays true for the next
    one. Every other row is the verified lossless file and gets the green check. */
const isLossy = (t: Track) => !t.source_fmt

/** Let go of a button the pointer clicked. The buttons live in a cell held at opacity 0 and revealed by
    `:hover`, `:focus-within` or selection, so a button that keeps focus after a click holds its whole row
    revealed until something else takes focus -- several rows at once, none of them under the pointer.
    `detail` is 0 when the click came from the keyboard (Enter or Space on a focused button); those keep
    their focus, or tabbing through the table would drop the caller back to the top of the page. */
const releasePointer = (e: React.MouseEvent<HTMLButtonElement>) => { if (e.detail) e.currentTarget.blur() }

/** What the cover is worth saying on hover. The row already carries the artist, title, mix, genre, label
    and year, so a tooltip that repeated them would earn nothing -- this is the release the track was
    lifted off and its catalogue number, which are the two catalogue facts the table has no column for. */
function releaseOf(c: Track['catalog']): string | undefined {
  const parts = [c?.release_name, c?.catalog_number].filter(Boolean)
  return parts.length ? parts.join(' · ') : undefined
}

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
  // The actions column has to be a fixed length -- every row is its own grid, so an `auto` track would be
  // measured per row and the columns would stop lining up. It does not have to be the *same* length in
  // every table, though: "Find lossless" only exists while some row is still on the lossy copy, and a
  // library that is entirely lossless can hand those ~100px back to the track and genre names.
  const width = tracks.some(isLossy) && onUpgrade ? '210px' : '110px'
  return (<div className="group table" style={{ '--actions': width } as React.CSSProperties}>
    <div className="thead"><span /><span>Track</span><span>Genre</span><span>Label</span><span>Year</span><span>Kbps</span><span>Format</span><span /></div>
    {tracks.map(t => {
      const c = t.catalog
      const lossy = isLossy(t)
      return (<div className={`trow${selected === t.id ? ' selected' : ''}${lossy ? ' lossy-row' : ''}`} key={t.id} onClick={() => setSelected(t.id)} onDoubleClick={() => onReveal(t.path)}>
        <Artwork url={c?.artwork_url ?? null} title={releaseOf(c)} small />
        <div className="cell"><div className="t">{t.artist} – {t.title}</div><div className="v">{t.mix_name}</div></div>
        <span className="ellipsis" title={c?.genre ?? undefined}>{c?.genre ?? 'Unknown'}</span>
        <span className="ellipsis" title={c?.label ?? undefined}>{c?.label ?? 'Unknown'}</span><span>{c?.release_date?.slice(0, 4) ?? ''}</span>
        <span className={lossy ? 'kbps lossy' : 'kbps'} title={lossy ? 'Filed from Deezer — no lossless copy was obtained' : undefined}>
          {t.bitrate_kbps}{!lossy && t.verified_at && <Icon name="check" size={12} stroke={2.4} />}
        </span>
        <span className="fmt">{t.fmt.toUpperCase()}</span>
        <span className="reveal">
          {lossy && onUpgrade && (
            <button className="btn-secondary" disabled={busy !== null} onClick={e => { e.stopPropagation(); setSelected(t.id); upgrade(e, t) }}>
              {busy === t.id ? 'Searching…' : 'Find lossless'}
            </button>
          )}
          <button className="btn-secondary" onClick={e => { e.stopPropagation(); releasePointer(e); onReveal(t.path) }}>Show in Finder</button>
        </span>
      </div>)
    })}
    {tracks.length === 0 && <div className="empty">Nothing here yet.</div>}
  </div>)
}
