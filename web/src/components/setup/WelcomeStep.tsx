import type { CSSProperties } from 'react'

// Overlay geometry as percentages of the illustration (1078x460) so a higher-res file drops in.
// Jog wheels: [x%, y%, ring diameter px at the 560px slot]. Screens: [x%, y%, w, h, tilt deg].
const WHEELS: [number, number, number][] = [[16, 70, 78], [34, 58, 74], [68.5, 58, 74], [85.5, 70, 78]]
const SCREENS: [number, number, number, number, number][] = [[11, 41, 64, 36, -20], [31.3, 28.5, 64, 36, -8], [69, 28.5, 64, 36, 8], [89, 41, 64, 36, 20]]
const METERS = [46.5, 49.1, 51.7, 54.3]  // x% of the mixer's LED strips; top 27%

const bars = Array.from({ length: 40 }, (_, i) => {
  const h = 6 + Math.abs(Math.sin(i * 1.7) * 12 + Math.cos(i * 0.6) * 6)
  return `<rect x='${i * 5}' y='${(30 - h) / 2}' width='2.5' height='${h.toFixed(1)}' rx='1' fill='%2366ccff'/>`
}).join('')
const WAVE = `url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='200' height='30' viewBox='0 0 200 30'>${bars}</svg>")`

const ringStyle = ([x, y, d]: [number, number, number], i: number): CSSProperties => {
  const mask = `radial-gradient(circle, transparent 0 ${d / 2 - 9}px, #000 ${d / 2 - 8}px ${d / 2 - 2}px, transparent ${d / 2}px)`
  return { left: `${x}%`, top: `${y}%`, width: d, height: d, margin: `${-d / 2}px 0 0 ${-d / 2}px`, WebkitMaskImage: mask, maskImage: mask, animationDelay: `${(i * -0.45).toFixed(2)}s` }
}
const screenStyle = ([x, y, w, h, r]: [number, number, number, number, number]): CSSProperties =>
  ({ left: `${x}%`, top: `${y}%`, width: w, height: h, margin: `${-h / 2}px 0 0 ${-w / 2}px`, transform: `rotate(${r}deg)` })

export default function WelcomeStep({ onStart }: { onStart: () => void }) {
  return (<div className="setup welcome">
    <div className="setup-title pywebview-drag-region" />
    <div className="welcome-body">
      <div className="rig">
        <svg width="0" height="0" aria-hidden="true" style={{ position: 'absolute' }}>
          {/* Keys out the crop's light background (anything lighter than ~87% grey) so the rig sits on the window colour in both modes. */}
          <filter id="rig-keyout" x="0" y="0" width="100%" height="100%" colorInterpolationFilters="sRGB"><feColorMatrix type="matrix" values="1 0 0 0 0  0 1 0 0 0  0 0 1 0 0  -8 -8 -8 0 20.9" /></filter>
        </svg>
        <img src="/welcome-rig.jpg" alt="" />
        {SCREENS.map((s, i) => <div key={i} className="screen" style={screenStyle(s)}>
          <div className="wave" style={{ backgroundImage: WAVE, animationDelay: `${(i * -0.9).toFixed(1)}s` }} /><div className="playhead" /></div>)}
        {WHEELS.map((w, i) => <div key={i} className="ring" style={ringStyle(w, i)} />)}
        {METERS.map((x, i) => <div key={i} className="meter" style={{ left: `${x}%`, animationDelay: `${(i * -0.12).toFixed(2)}s` }} />)}
      </div>
      <div className="pitch">
        <h1>Krater</h1>
        <p className="lead">Paste a YouTube link. Get the best copy that exists — lossless where Soulseek has it — tagged and filed where Rekordbox will find it.</p>
      </div>
      <div className="cta">
        <button className="btn-primary lg" onClick={onStart}>Get started</button>
        <div className="note">Two minutes: pick a folder, connect Telegram, add Soulseek.</div>
      </div>
    </div>
  </div>)
}
