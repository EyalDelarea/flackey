import { useEffect, useState } from 'react'
import { api } from '../../api'
import { optIn } from '../../telemetry'
import UsageStatsAsk from '../UsageStatsAsk'
import type { UsageStatsChoice } from '../UsageStatsAsk'

export default function WelcomeStep({ onStart }: { onStart: () => void }) {
  // Hidden until the server says usage stats can run here, and on any error: no card beats a broken one.
  const [available, setAvailable] = useState(false)
  // Nothing chosen, always: opting in has to be the owner's own click, and "Not now" is just as easy.
  const [choice, setChoice] = useState<UsageStatsChoice | null>(null)
  useEffect(() => {
    let live = true
    api.telemetry().then(t => { if (live) setAvailable(!!t?.available && !t.enabled) }).catch(() => undefined)
    return () => { live = false }
  }, [])
  // The choice is applied here, not on the segment's click, so changing your mind before Get started never
  // switches stats on and off again. The opt-in goes out first and every setup event after it waits on it,
  // so the funnel starts at this click. The wizard itself never waits on it, and a failure leaves stats off.
  // "Not now", or no choice at all, leaves stats off and only records that the question was asked, so the
  // one-time prompt for existing owners never follows a new owner out of setup.
  const start = () => {
    if (available) {
      if (choice === 'share') optIn()
      else api.telemetryAsked().catch(() => undefined)
    }
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
      {available && <UsageStatsAsk value={choice} onChoose={setChoice} />}
      <div className="cta">
        <button className="btn-primary lg" onClick={start}>Get started</button>
        <div className="note">Two minutes: pick a folder, then connect Telegram, Soulseek or both.</div>
      </div>
    </div>
  </div>)
}
