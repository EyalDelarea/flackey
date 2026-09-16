import { useState } from 'react'
import { api, ApiError } from '../../api'
import FormatOptions from '../FormatOptions'

export default function FormatStep({ libraryRoot, initial, formats, onDone }: {
  libraryRoot: string; initial: string; formats: string[]; onDone: (format: string) => void
}) {
  const [format, setFormat] = useState(initial || 'aiff')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  async function go() {
    setBusy(true); setErr(null)
    try {
      const s = await api.saveSettings(libraryRoot, { lossless_filing_format: format })
      onDone(s.lossless_filing_format ?? format)
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : 'Could not save that format.')
    } finally {
      setBusy(false)
    }
  }
  return (<>
    <h1>Choose a file format</h1>
    <p className="lead">This applies to new lossless tracks from now on. Files already in your library stay as they are.</p>
    <FormatOptions formats={formats} value={format} onChange={setFormat} disabled={busy} />
    <button className="btn-primary lg" onClick={go} disabled={busy}>Continue</button>
    {err && <div className="err">{err}</div>}
  </>)
}
