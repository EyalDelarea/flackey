export default function WelcomeStep({ onStart }: { onStart: () => void }) {
  return (<div className="setup welcome">
    <div className="setup-title pywebview-drag-region" />
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
        <p className="lead">Paste a YouTube link. Get the best copy that exists — lossless where Soulseek has it — tagged and filed where Rekordbox will find it.</p>
      </div>
      <div className="cta">
        <button className="btn-primary lg" onClick={onStart}>Get started</button>
        <div className="note">Two minutes: pick a folder, connect Telegram, add Soulseek.</div>
      </div>
    </div>
  </div>)
}
