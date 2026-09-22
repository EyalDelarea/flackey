import Icon from '../Icon'
import PlayButton from './PlayButton'
import type { SampleView, SamplesView } from '../../presentation'
import type { PlayerState } from './DownloadPage'

interface Props { s: SamplesView; player: PlayerState; onPlay: (key: string, url: string) => void; onAccept: () => void }

/** "It says a different recording — is it?" answered by ear (issue #92).
 *
 *  Two rows, always in the same order: what was asked for above what was found. The comparison only means
 *  anything in that direction, and a block that reordered itself depending on which half existed would
 *  make the owner re-read it every time. Each half is optional and says so when it is missing, rather than
 *  disappearing and leaving a lopsided block with nothing to explain the gap.
 *
 *  The page's one <audio> serves both, so starting one stops the other -- which is the whole point:
 *  these are meant to be heard back to back, not together. */
export default function Compare({ s, player, onPlay, onAccept }: Props) {
  return (
    <div className="compare">
      <div className="compare-head">Listen to both, then decide</div>
      <Side label="What you asked for" sample={s.reference} player={player} onPlay={onPlay}
        fallback={s.video
          ? <a className="btn-link" href={s.video.url} target="_blank" rel="noreferrer">Open your video at {s.video.at}</a>
          : <span className="muted">No copy of this to play</span>} />
      <Side label="What Flackey found" sample={s.found} player={player} onPlay={onPlay}
        fallback={<span className="muted">The copy is no longer here</span>} />
      {/* Only where there is still a file to file. Everything else on this row is a way of looking at what
          happened; this is the one button that changes it, so it is the one thing drawn as a commitment. */}
      {s.found && (
        <div className="compare-act">
          <button className="btn-primary" onClick={onAccept}>
            <Icon name="check" size={12} stroke={2.2} />Keep it anyway
          </button>
          <span className="muted">Files this copy, tagged and named like any other track.</span>
        </div>)}
    </div>
  )
}

function Side({ label, sample, player, onPlay, fallback }:
  { label: string; sample: SampleView | null; player: PlayerState; onPlay: (key: string, url: string) => void; fallback: React.ReactNode }) {
  const playing = sample != null && player.playing === sample.key
  const clock = sample != null && player.clock?.key === sample.key ? player.clock : null
  const waiting = playing && clock == null
  return (
    <div className={`compare-side${playing ? ' lit' : ''}`}>
      <span className="compare-label">{label}</span>
      {sample ? <PlayButton sample={sample} player={player} onPlay={onPlay} /> : fallback}
      {sample && (
        <span className={`sample-rail${waiting && player.slow ? ' stalled' : waiting ? ' waiting' : ''}`} aria-hidden>
          <span className="sample-rail-fill" style={waiting ? undefined : { transform: `scaleX(${clock ? Math.min(1, clock.at / clock.of) : 0})` }} />
        </span>)}
      {sample && player.failed === sample.key && <span className="muted">Couldn't load — try again</span>}
    </div>
  )
}
