import { useEffect, useState } from 'react'
import { ApiError, api } from '../../api'
import type { NewSinceImport } from '../../api'
import { plural } from '../../presentation'
import Icon from '../Icon'

/** The "Flackey - New" playlist: everything filed since the owner last said they imported into Rekordbox.
    Flackey cannot see inside Rekordbox, so the button is the only way it learns the import happened. */
export default function NewSinceImportCard({ version, onShowFile, onError }: {
  /** Bumped whenever the library changes, so the count follows new filings. */
  version: number
  onShowFile: (path: string) => void
  onError: (message: string) => void
}) {
  const [state, setState] = useState<NewSinceImport | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let current = true
    api.newSinceImport().then(r => { if (current) setState(r) }).catch(() => undefined)
    return () => { current = false }
  }, [version])

  if (!state) return null
  const markImported = async () => {
    if (!state.through) return
    setBusy(true)
    try { setState(await api.markImported(state.through)) }
    catch (err) { onError(err instanceof ApiError ? err.message : "Couldn't save that. Try again.") }
    finally { setBusy(false) }
  }
  return (<div className="group pl-card new-since-import">
    <span className="pl-icon"><Icon name="playlist" size={22} /></span>
    <div className="how">
      <h2>New since your last Rekordbox import <span>· {plural(state.count, 'track')}</span></h2>
      {state.count
        ? <>In Rekordbox: File → Import → Playlist, then choose “Flackey - New”. When it’s in, press
          “I’ve imported these” and the list starts fresh.</>
        : <>Nothing new since your last import. Tracks you download collect here until you import them.</>}
    </div>
    <button className="btn-secondary" onClick={() => onShowFile(state.file)}>Show playlist file</button>
    {state.count > 0 && <button className="btn-secondary" onClick={markImported} disabled={busy}>
      {busy ? 'Saving…' : 'I’ve imported these'}</button>}
  </div>)
}
