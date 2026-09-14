export default function WelcomeStep({ onStart }: { onStart: () => void }) {
  return (<div className="setup welcome">
    <div className="setup-title pywebview-drag-region" />
    <div className="welcome-body">
      <img className="welcome-vinyl" src="/silver-vinyl.png"
        alt="Silver Flackey vinyl record emerging from its sleeve" />
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
