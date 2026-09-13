import { useEffect, useState } from 'react'
import { api, ApiError } from '../../api'
import Icon from '../Icon'
import type { Tools } from '../../api'

const NAME: Record<keyof Tools, string> = { ffmpeg: 'ffmpeg', ffprobe: 'ffprobe', yt_dlp: 'yt-dlp' }
const BREW: Record<keyof Tools, string> = { ffmpeg: 'ffmpeg', ffprobe: 'ffmpeg', yt_dlp: 'yt-dlp' }

export default function ReadyStep({ libraryRoot, onStart, error, telegram, soulseek }: { libraryRoot: string; onStart: () => Promise<void>; error?: string | null; telegram: 'connected' | 'skipped'; soulseek: 'connected' | 'pending' | 'skipped' }) {
  const [tools, setTools] = useState<Tools | null>(null)
  const [checkFailed, setCheckFailed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const check = () => { setCheckFailed(false); api.tools().then(setTools).catch(() => setCheckFailed(true)) }
  useEffect(() => { check() }, [])
  const missing = tools ? (Object.keys(tools) as (keyof Tools)[]).filter(k => !tools[k]) : []
  const start = () => {
    setBusy(true); setErr(null)
    onStart().catch(e => setErr(e instanceof ApiError ? e.message : 'Could not start. Try again.')).finally(() => setBusy(false))
  }
  if (checkFailed) return (<>
    <div className="ready-disc bad"><Icon name="x" size={24} stroke={2.4} /></div>
    <h1>Something is missing</h1>
    <p className="lead">Couldn't check for ffmpeg, ffprobe and yt-dlp. Try again.</p>
    <button className="btn-primary lg" onClick={check}>Try again</button>
  </>)
  if (tools && missing.length) {
    const names = missing.map(k => NAME[k])
    const brewArgs = Array.from(new Set(missing.map(k => BREW[k]))).join(' ')
    return (<>
      <div className="ready-disc bad"><Icon name="x" size={24} stroke={2.4} /></div>
      <h1>Something is missing</h1>
      <p className="lead">{names.join(' and ')} {names.length > 1 ? 'are' : 'is'} not installed. Install {names.length > 1 ? 'them' : 'it'} with <span className="mono">brew install {brewArgs}</span>, then try again.</p>
      <button className="btn-primary lg" onClick={check}>Try again</button>
    </>)
  }
  // Both sources may be skipped, and the wizard still has to let them finish -- so the last screen stops
  // claiming Telegram is connected and says instead what they will and will not get.
  const nothing = telegram === 'skipped' && soulseek === 'skipped'
  const sources = [telegram === 'connected' ? 'Telegram is connected' : null,
    soulseek === 'connected' ? 'Soulseek is connected' : null].filter(Boolean).join(' and ')
  return (<>
    <div className="ready-disc"><Icon name="check" size={26} stroke={2.4} /></div>
    <h1>{nothing ? 'Almost set' : "You're set"}</h1>
    <p className="lead">{nothing
      ? 'There is nowhere to fetch from yet: Telegram and Soulseek are both off. You can turn either on later from Settings. Your music will be filed into'
      : `${sources || 'Your sources are saved'} and your music will be filed into`}</p>
    <div className="path-well">{libraryRoot}</div>
    {/* Credentials are read once, at startup (build_providers / SlskdProcess.start in app.py), so an
        account saved during this wizard does not take effect on the process that is already running.
        Say so plainly rather than letting the user wonder why nothing lossless ever arrives. */}
    {soulseek === 'pending' && <div className="hint-row">Soulseek is saved. It starts looking for lossless copies the
      next time you open Flackey.</div>}
    <button className="btn-primary lg" onClick={start} disabled={!tools || busy}>Start digging</button>
    {err && <div className="err">{err}</div>}
    {error && <div className="err">{error}</div>}
  </>)
}
