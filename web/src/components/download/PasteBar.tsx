import { useState } from 'react'
import { ApiError } from '../../api'
import Toolbar from '../Toolbar'

export default function PasteBar({ onSubmit }: { onSubmit: (url: string) => Promise<string> }) {
  const [url, setUrl] = useState(''); const [pending, setPending] = useState(false)
  const [note, setNote] = useState<{ text: string; error: boolean } | null>(null)
  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!url.trim()) {
      setNote({ text: 'Paste a YouTube, YouTube Music, or Spotify link first.', error: true })
      return
    }
    if (pending) return
    setPending(true); setNote(null)
    try {
      const summary = await onSubmit(url.trim())
      setUrl(''); setNote({ text: summary, error: false }); setTimeout(() => setNote(n => n?.error ? n : null), 6000)
    } catch (err) {
      setNote({ text: err instanceof ApiError ? err.message : 'Something went wrong. Try again.', error: true })
    } finally { setPending(false) }
  }
  return (
    <Toolbar>
      <form className="pastebar" onSubmit={submit} aria-label="add link" role="form">
        <label className="paste-label" htmlFor="download-link">Track or playlist link</label>
        <div className="paste-controls">
          <input id="download-link" className="input" placeholder="YouTube, YouTube Music, or Spotify" value={url} onChange={e => setUrl(e.target.value)} />
          <button className="btn-primary" type="submit" disabled={pending}>{pending ? 'Adding…' : 'Add'}</button>
        </div>
      </form>
      {note && <div className={`note${note.error ? ' error' : ''}`} role="status" aria-live={note.error ? 'assertive' : 'polite'}>{note.text}</div>}
    </Toolbar>
  )
}
