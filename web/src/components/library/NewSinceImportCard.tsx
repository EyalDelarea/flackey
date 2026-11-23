import { useEffect, useState } from 'react'
import { ApiError, api } from '../../api'
import type { NewSinceImport } from '../../api'
import { plural } from '../../presentation'
import Icon from '../Icon'

/** The first-time tip that shares the card: Flackey does not do the Rekordbox import itself, and a new
 *  owner has nowhere else to learn that dragging the folder in works too. Shown until dismissed. */
export interface RekordboxGuide { revealLabel: string; onReveal: () => void; onDismiss: () => void }

/** The one Rekordbox card on the Library: the "Flackey - New" playlist -- everything filed since the owner
    last said they imported -- and, until dismissed, the first-time tip. It used to be two stacked cards that
    both said "get these into Rekordbox", with four buttons between them before the first track.
    Flackey cannot see inside Rekordbox, so "I've imported these" is the only way it learns the import
    happened. */
export default function NewSinceImportCard({ version, onShowFile, onError, guide }: {
  /** Bumped whenever the library changes, so the count follows new filings. */
  version: number
  onShowFile: (path: string) => void
  onError: (message: string) => void
  guide?: RekordboxGuide | null
}) {
  const [state, setState] = useState<NewSinceImport | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let current = true
    api.newSinceImport().then(r => { if (current) setState(r) }).catch(() => undefined)
    return () => { current = false }
  }, [version])

  // The tip stands on its own while the count has not loaded, or could not be.
  if (!state && !guide) return null
  const markImported = async () => {
    if (!state?.through) return
    setBusy(true)
    try { setState(await api.markImported(state.through)) }
    catch (err) { onError(err instanceof ApiError ? err.message : "Couldn't save that. Try again.") }
    finally { setBusy(false) }
  }
  return (<div className="group pl-card new-since-import">
    <span className="pl-icon"><Icon name="playlist" size={22} /></span>
    <div className="how">
      {state && <>
        <h2>{state.count ? `${plural(state.count, 'new track')} for Rekordbox` : 'Nothing new for Rekordbox'}</h2>
        {state.count
          ? <>File → Import → Playlist → “Flackey - New”.</>
          : <>Nothing new since your last import. Tracks you download collect here until you import them.</>}
      </>}
      {guide && <p className="rekordbox-tip">First time? Drag the library folder into Rekordbox’s collection instead.{' '}
        <button className="btn-link" onClick={guide.onReveal}>{guide.revealLabel}</button>
        {/* Waves away the tip, not the card: so it sits on the tip's own line */}
        <span className="sep" aria-hidden="true"> · </span>
        <button className="btn-link muted" onClick={guide.onDismiss}>Dismiss</button></p>}
    </div>
    {state && state.count > 0 && <button className="btn-primary" onClick={markImported} disabled={busy}>
      {busy ? 'Saving…' : 'I’ve imported these'}</button>}
    {state && <button className="btn-secondary" onClick={() => onShowFile(state.file)}>Show playlist file</button>}
  </div>)
}
