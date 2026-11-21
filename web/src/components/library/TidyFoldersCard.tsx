import { useEffect, useState } from 'react'
import { ApiError, api } from '../../api'
import type { TidyGroup } from '../../api'
import { plural } from '../../presentation'
import Icon from '../Icon'

const name = (path: string) => path.split(/[\\/]/).slice(-2).join('/')

/** Tidy artist folders: one artist split over two spellings ("Miranda" and "Miranda!"), or a track number
    leaked into a folder name. Opt-in only: the owner reviews every move first, and nothing moves until they
    confirm -- moving a file makes Rekordbox show it as missing until it is relinked. */
export default function TidyFoldersCard({ version, onDone, onError }: {
  version: number
  onDone: (message: string) => void
  onError: (message: string) => void
}) {
  const [groups, setGroups] = useState<TidyGroup[]>([])
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let current = true
    api.tidyPreview().then(r => { if (current) setGroups(r.groups.filter(g => g.moves.length)) }).catch(() => undefined)
    return () => { current = false }
  }, [version])

  const moves = groups.flatMap(g => g.moves)
  if (!moves.length) return null
  const confirm = async () => {
    setBusy(true)
    try {
      const r = await api.tidyApply(moves)
      setOpen(false)
      onDone(`Moved ${plural(r.moved, 'file')}.` + (r.failed.length ? ` ${plural(r.failed.length, 'file')} could not be moved and stayed where ${r.failed.length === 1 ? 'it was' : 'they were'}.` : '')
        + ' In Rekordbox, relink them with File → Display All Missing Files.')
      setGroups((await api.tidyPreview()).groups.filter(g => g.moves.length))
    } catch (err) {
      onError(err instanceof ApiError ? err.message : "Couldn't tidy the folders. Nothing was moved.")
      setGroups((await api.tidyPreview().catch(() => ({ groups: [] }))).groups.filter(g => g.moves.length))
    } finally { setBusy(false) }
  }
  return (<div className="group pl-card tidy-folders">
    <span className="pl-icon"><Icon name="folder" size={22} /></span>
    <div className="how">
      <h2>Tidy artist folders <span>· {plural(groups.length, 'artist')}, {plural(moves.length, 'file')}</span></h2>
      Some artists are split over two folders, or have a track number in their folder name. New downloads already
      go to the right folder; this tidies the files you have.
      {open && <div className="tidy-preview" role="region" aria-label="Files that would move">
        <p className="tidy-warning" role="note">Rekordbox will show these tracks as
          missing until it relinks them. Nothing moves until you press “Move {plural(moves.length, 'file')}”.</p>
        <ul>{groups.map(g => <li key={g.folder}>
          <strong>{g.merges.length ? <>{g.merges.join(', ')} → {g.folder}</> : <>→ {g.folder}</>}</strong>
          <ul>{g.moves.map(m => <li key={m.track_id}><span className="from">{name(m.source)}</span> → {name(m.dest)}</li>)}</ul>
          {g.left_behind > 0 && <p className="muted">{plural(g.left_behind, 'other file')} Flackey didn’t download {g.left_behind === 1 ? 'stays where it is' : 'stay where they are'}.</p>}
          {g.conflicts.length > 0 && <p className="muted">{plural(g.conflicts.length, 'file')} left alone: the name is already taken in {g.folder}.</p>}
        </li>)}</ul>
        <div className="tidy-actions">
          <button className="btn-primary" onClick={confirm} disabled={busy}>{busy ? 'Moving…' : `Move ${plural(moves.length, 'file')}`}</button>
          <button className="btn-secondary" onClick={() => setOpen(false)} disabled={busy}>Cancel</button>
        </div>
      </div>}
    </div>
    {!open && <button className="btn-secondary" onClick={() => setOpen(true)}>Review</button>}
  </div>)
}
