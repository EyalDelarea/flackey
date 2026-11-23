import { Fragment, useState } from 'react'
import Artwork from '../download/Artwork'
import TrackReceipt from './TrackReceipt'
import HelpTag from '../HelpTag'
import Icon from '../Icon'
import type { Track } from '../../api'
import { revealLabel, usePlatform } from '../../platform'

/** A track whose `source_fmt` is unset was filed from the lossy Deezer copy: no lossless provider supplied
    it. That is the state the owner wants to act on, and it names no provider, so it stays true for the next
    one. Every other row is the verified lossless file and gets the green check. */
const isLossy = (t: Track) => !t.source_fmt

/** The badge a row wears when its file is not simply trusted: the owner kept it after the quality check
    refused it, the check could not vouch for it, or checking it again found something. */
function trustBadge(t: Track): { text: string; tone: 'bad' | 'plain' } | null {
  const ev = (kind: string) => t.evidence?.filter(e => e.kind === kind).at(-1)?.value
  if (ev('quality_check')?.status === 'failed') return { text: 'Kept anyway', tone: 'bad' }
  const again = ev('recheck')
  if (again && !(again.status === 'checked' && again.tier === 'clear')) return { text: 'Check flagged', tone: 'bad' }
  if (ev('verification')?.tier === 'unverified') return { text: 'Unverified', tone: 'plain' }
  return null
}

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

export default function TrackTable({ tracks, onReveal, onUpgrade, recheckRunning, header = true, wideActions }: {
  tracks: Track[]; onReveal: (path: string) => void
  onUpgrade?: (t: Track) => Promise<void>
  /** The whole library is being checked again; a single "Check again" waits for it. */
  recheckRunning?: boolean
  /** Off for every folder but the first, which carries the one column header for the page. */
  header?: boolean
  /** Whether any table on the page has a "Find lossless" row: folder tables share one header, so they share
   *  one actions width. Worked out from this table's own rows when absent. */
  wideActions?: boolean
}) {
  const platform = usePlatform()
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
  const width = (wideActions ?? tracks.some(isLossy)) && onUpgrade ? '210px' : '110px'
  return (<div className="group table" style={{ '--actions': width } as React.CSSProperties}>
    {header && <div className="thead"><span /><span>Track</span><span className="col-optional">Genre</span><span className="col-optional">Label</span><span className="col-optional">Year</span><span>Kbps</span><span>Format</span><span /></div>}
    {tracks.map(t => {
      const c = t.catalog
      const lossy = isLossy(t)
      // The year the file is tagged with. The row's own `tag_date` first: the record is shared, and a remaster
      // keeps its own year while the record may carry the original album's (issue #181). An older row has none
      // and falls back to the record -- the first release's where one is known, as `tag.py` writes it.
      const year = (t.tag_date || c?.original_date || c?.release_date)?.slice(0, 4) ?? ''
      const compactMeta = [c?.genre, c?.label, year].filter(Boolean).join(' · ')
      const badge = trustBadge(t)
      const open = selected === t.id
      return (<Fragment key={t.id}><div className={`trow${open ? ' selected' : ''}${lossy ? ' lossy-row' : ''}`} aria-expanded={open}
        onClick={() => setSelected(open ? null : t.id)} onDoubleClick={() => onReveal(t.path)}>
        <Artwork url={c?.artwork_url ?? null} title={releaseOf(c)} small />
        <div className="cell"><div className="t" title={`${t.artist} – ${t.title}`}>{t.artist} – {t.title}</div><div className="v">{t.mix_name}{badge && <span className={`trust-badge ${badge.tone}`}>{badge.text}</span>}</div>
          {compactMeta && <div className="v compact-meta">{compactMeta}</div>}</div>
        {/* A faint dash where there is no value, so the real genres and labels stand out down the column */}
        <span className="ellipsis col-optional" title={c?.genre ?? undefined}>{c?.genre ?? <span className="missing" aria-label="Unknown">—</span>}</span>
        <span className="ellipsis col-optional" title={c?.label ?? undefined}>{c?.label ?? <span className="missing" aria-label="Unknown">—</span>}</span><span className="col-optional">{year}</span>
        {/* The app's own help tag, not `title`: the Mac window (WKWebView) shows no `title` tooltip */}
        <span className={lossy ? 'kbps lossy' : 'kbps'}>
          {lossy ? <HelpTag text="Filed from Deezer — no lossless copy was found">{t.bitrate_kbps}</HelpTag> : t.bitrate_kbps}
          {!lossy && t.verified_at && !badge && <Icon name="check" size={12} stroke={2.4} />}
        </span>
        <span className="fmt">{t.fmt.toUpperCase()}</span>
        <span className="reveal">
          {lossy && onUpgrade && (
            <button className="btn-secondary" disabled={busy !== null} onClick={e => { e.stopPropagation(); setSelected(t.id); upgrade(e, t) }}>
              {busy === t.id ? 'Searching…' : 'Find lossless'}
            </button>
          )}
          <button className="btn-secondary" onClick={e => { e.stopPropagation(); releasePointer(e); onReveal(t.path) }}>{revealLabel(platform)}</button>
        </span>
      </div>
      {open && <TrackReceipt trackId={t.id} disabled={recheckRunning} />}</Fragment>)
    })}
    {tracks.length === 0 && <div className="empty">Nothing here yet.</div>}
  </div>)
}
