import { useState } from 'react'
import { ApiError } from '../../api'
import Toolbar from '../Toolbar'

export default function PasteBar({ onSubmit, inset }: { onSubmit: (url: string) => Promise<string>; inset?: boolean }) {
  const [url, setUrl] = useState(''); const [pending, setPending] = useState(false)
  const [note, setNote] = useState<{ text: string; error: boolean } | null>(null)
  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!url.trim() || pending) return
    setPending(true); setNote(null)
    try {
      const summary = await onSubmit(url.trim())
      setUrl(''); setNote({ text: summary, error: false }); setTimeout(() => setNote(n => n?.error ? n : null), 6000)
    } catch (err) {
      setNote({ text: err instanceof ApiError ? err.message : 'Something went wrong. Try again.', error: true })
    } finally { setPending(false) }
  }
  return (
    <Toolbar inset={inset}>
      <form className="pastebar" onSubmit={submit} aria-label="add link" role="form">
        <input className="input" placeholder="Paste a YouTube or YouTube Music link" value={url} onChange={e => setUrl(e.target.value)} />
        <button className="btn-primary" type="submit" disabled={pending}>{pending ? 'Adding…' : 'Add'}</button>
      </form>
      {note && <div className={`note${note.error ? ' error' : ''}`}>{note.text}</div>}
    </Toolbar>
  )
}
