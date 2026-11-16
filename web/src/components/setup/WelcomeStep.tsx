import { useEffect, useState } from 'react'
import { api } from '../../api'
import { optIn } from '../../telemetry'
import UsageStatsCatalog from '../UsageStatsCatalog'

export default function WelcomeStep({ onStart }: { onStart: () => void }) {
  // Hidden until the server says usage stats can run here, and on any error: no card beats a broken one.
  const [available, setAvailable] = useState(false)
  // Unticked by default, always: opting in has to be the owner's own click.
  const [share, setShare] = useState(false)
  const [catalogOpen, setCatalogOpen] = useState(false)
  useEffect(() => {
    let live = true
    api.telemetry().then(t => { if (live) setAvailable(!!t?.available && !t.enabled) }).catch(() => undefined)
    return () => { live = false }
  }, [])
  // The opt-in goes out first and every setup event after it waits on it, so the funnel starts at this
  // click. The wizard itself never waits on it, and a failure leaves stats off.
  const start = () => {
    if (available && share) optIn()
    onStart()
  }
  return (<div className="setup welcome">
    <div className="setup-title" />
    <div className="welcome-body">
      <div className="welcome-vinyl">
        <img className="welcome-vinyl-photo" src="/silver-vinyl.png"
          alt="Silver Flackey vinyl record emerging from its sleeve" />
        {/* Rotate a complete circular record in the photo's perspective; the sleeve occludes
            the moving layer and remains stationary. Clip geometry matches this exact photo. */}
        <svg className="welcome-vinyl-motion" viewBox="0 0 1536 1024" aria-hidden="true">
          <defs>
            <clipPath id="welcome-record-exposed">
              <path d="M0 0H638L647 82L730 850H0Z" />
            </clipPath>
            <clipPath id="welcome-record-edge">
              <circle r="378" />
            </clipPath>
          </defs>
          <g clipPath="url(#welcome-record-exposed)">
            <g transform="translate(542 478) matrix(.923 0 .177 .96 0 0)">
              <g clipPath="url(#welcome-record-edge)">
                <g className="welcome-vinyl-disc-spin">
                  <image href="/silver-record-spin.png" x="-398.4" y="-398.4" width="796.8" height="796.8" />
                </g>
              </g>
            </g>
          </g>
        </svg>
      </div>
      <div className="pitch">
        <h1>Flackey</h1>
        <p className="lead">Paste a YouTube link. Get the best copy that exists — lossless where Soulseek has it — tagged and filed in your library folder, ready to drag into Rekordbox.</p>
      </div>
      {available && <div className="optin-card">
        <div className="optin-title">Help make Flackey better <span className="optin-optional">(optional)</span></div>
        <p className="optin-text">Send usage stats: which steps work, which fail, and how long they take. No names, links or
          file names, and nothing about the music you download. You can see every report before it leaves, and turn
          this off in Settings at any time, which also deletes what was sent.</p>
        <div className="optin-actions">
          <label className="optin-check"><input type="checkbox" checked={share} onChange={e => setShare(e.target.checked)} />
            Share usage stats</label>
          <button type="button" className="btn-link" onClick={() => setCatalogOpen(true)}>See exactly what's sent</button>
        </div>
      </div>}
      <div className="cta">
        <button className="btn-primary lg" onClick={start}>Get started</button>
        <div className="note">Two minutes: pick a folder, then connect Telegram, Soulseek or both.</div>
      </div>
    </div>
    {catalogOpen && <UsageStatsCatalog onClose={() => setCatalogOpen(false)} />}
  </div>)
}
