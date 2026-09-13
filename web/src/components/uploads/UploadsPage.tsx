import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api'
import type { SharingState, UploadFeed } from '../../api'
import Banner from '../Banner'
import Toolbar from '../Toolbar'


/** `gb` from presentation.ts floors at whole megabytes, which reads "0 MB" for the first small transfer. */
const size = (b: number) => b >= 1e9 ? `${(b / 1e9).toFixed(1)} GB` : b >= 1e6 ? `${Math.round(b / 1e6)} MB`
  : b >= 1e3 ? `${Math.round(b / 1e3)} kB` : `${b} B`
const rate = (bps: number) => bps >= 1e6 ? `${(bps / 1e6).toFixed(1)} MB/s` : `${Math.round(bps / 1e3)} kB/s`
const POLL_MS = 4000

/** Who is pulling from the shared library. Every string on this page - peer names, file and folder names -
 *  was chosen by a peer or by whoever named the file on disk. It is rendered as text and never acted on. */
export default function UploadsPage({ inset }: { inset: boolean }) {
  const [feed, setFeed] = useState<UploadFeed | null>(null)
  const [sharing, setSharing] = useState<SharingState | null>(null)
  const [error, setError] = useState<string | null>(null)
  const load = useCallback(async () => {
    // An empty page here reads as "nobody wants my files" when the real answer is often "nobody can
    // reach you". The port answer rides along with the feed so the two arrive together.
    setSharing(await api.sharing().catch(() => null))
    try { setFeed(await api.uploads()); setError(null) } catch { setError("Can't reach Flackey.") }
  }, [])
  useEffect(() => {
    load().catch(() => undefined)
    const t = setInterval(() => { load().catch(() => undefined) }, POLL_MS)
    return () => clearInterval(t)
  }, [load])

  const s = feed?.summary
  return (<>
    <Toolbar inset={inset}><h1>Uploads</h1></Toolbar>
    {error && <Banner tone="red" text={error} />}
    {feed && !feed.enabled && <Banner tone="amber" text="Soulseek is off. Add your slskd key in Settings to share back." />}
    {feed?.error && <Banner tone="amber" text={`Can't read transfers from ${feed.provider ?? 'the sidecar'}: ${feed.error}`} />}
    {sharing?.reachable === false && <Banner tone="amber"
      text="Your Soulseek port is closed, so most people cannot download from you. Settings › Sharing shows how to open it." />}
    {s && (
      <div className="up-summary">
        <div className="up-stat"><span className="n">{s.active}</span><span className="k">sending now</span></div>
        <div className="up-stat"><span className="n">{s.completed}</span><span className="k">completed</span></div>
        <div className="up-stat"><span className="n">{s.peers}</span><span className="k">{s.peers === 1 ? 'person' : 'people'}</span></div>
        <div className="up-stat"><span className="n">{size(s.bytes)}</span><span className="k">shared back</span></div>
      </div>
    )}
    <div className="scroll">
      {feed && feed.enabled && feed.uploads.length === 0 && !feed.error && (
        <p className="empty">Nobody has downloaded from you yet. Soulseek works both ways — leave the app running
          and peers can pull from your library, which is what keeps you in good standing.</p>)}
      {feed?.uploads.map((u, i) => {
        const done = u.state.includes('Completed')
        return (
          <div key={u.id ?? `${u.peer}-${i}`} className={`up-row${done ? ' done' : ''}`}>
            <div className="who">{u.peer}</div>
            <div className="what">
              <div className="f">{u.file}</div>
              {u.folder && <div className="d">{u.folder}</div>}
            </div>
            <div className="up-bar"><span style={{ width: `${u.pct}%` }} /></div>
            <div className="how">
              <span className="hd">{done ? u.state.replace('Completed, ', '') : `${u.pct}% · ${rate(u.speed_bps)}`}</span>
              <span className="hs">{size(u.size)}</span>
            </div>
          </div>
        )
      })}
    </div>
  </>)
}
