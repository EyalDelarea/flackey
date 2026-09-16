import { useEffect, useState } from 'react'
import { api, ApiError } from '../../api'
import Icon from '../Icon'

export default function FolderStep({ initial, onDone }: { initial: string; onDone: (path: string) => void }) {
  const [path, setPath] = useState(initial); const [err, setErr] = useState<string | null>(null); const [busy, setBusy] = useState(false)
  const [pickerAvailable, setPickerAvailable] = useState(false)
  useEffect(() => {
    api.pickFolderAvailable().then(r => setPickerAvailable(r.available)).catch(() => {})
  }, [])
  async function go() {
    setBusy(true); setErr(null)
    try { const s = await api.saveSettings(path); onDone(s.library_root) }
    catch (e) { setErr(e instanceof ApiError ? e.message : 'Could not use that folder.') } finally { setBusy(false) }
  }
  async function chooseFolderClicked() {
    setBusy(true); setErr(null)
    try { const r = await api.pickFolder(path || null); if (r.path) setPath(r.path) }
    catch (e) { setErr(e instanceof ApiError ? e.message : "Couldn't open the folder chooser.") } finally { setBusy(false) }
  }
  return (<>
    <h1>Where should your music live?</h1>
    <p className="lead">Every finished track is filed here, one folder per artist. Drag this folder into Rekordbox's collection whenever you want to import what's new.</p>
    <div className="folder">
      <div className="folder-well"><Icon name="folder" size={18} /><input value={path} onChange={e => setPath(e.target.value)} aria-label="library folder" /></div>
      <div className="hint-row">Type or paste the full folder path{pickerAvailable && <button className="btn-secondary" onClick={chooseFolderClicked} disabled={busy}>Choose…</button>}</div>
      {err && <div className="err">{err}</div>}
    </div>
    <button className="btn-primary lg" onClick={go} disabled={busy}>Continue</button>
  </>)
}
