import PlayButton from './PlayButton'
import type { CandidateView } from '../../presentation'
import type { PlayerState } from './DownloadPage'

// Counting up, not down: the owner listens for three to eight seconds and moves on, so the end of the
// clip is not an event worth waiting for. The number's only job is to say this is running.
const elapsed = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`

interface Props { c: CandidateView; player: PlayerState; onChoose: () => void; onPlay: (key: string, url: string) => void }

export default function CandidateCard({ c, player, onChoose, onPlay }: Props) {
  const key = c.sample?.key
  const playing = key != null && player.playing === key
  // The clock outlives `playing` by the length of the ended fade, so it is read off the key rather than off
  // the state: that is what leaves the rail full while it fades instead of draining as it goes.
  const clock = key != null && player.clock?.key === key ? player.clock : null
  const waiting = playing && clock == null
  const stalled = waiting && player.slow
  const failed = key != null && player.failed === key
  // Both of these stand in for the meta line rather than adding one: a card that grows a line on a press
  // pushes every card beside it down, mid-compare.
  const meta = stalled ? 'Still loading the sample…'
    : failed ? "Couldn't load — try again"
    : [c.length, c.onBeatport ? 'On Beatport' : 'Not on Beatport', c.lengthNote].filter(Boolean).join(' · ')
  return (
    // Playing lives on the fill axis and chosen on the border axis, so a card that is both composes
    // without either reading as the other.
    <div className={`candidate${c.chosen ? ' chosen' : ''}${playing ? ' lit' : ''}`}>
      <div className="head"><span>{c.title} <span className="version">({c.version})</span></span>{c.score != null && <span className="score">{c.score}% match</span>}</div>
      <div className={`meta${stalled || failed ? ' note' : ''}`}>{meta}</div>
      <div className="actions">
        {/* Listen, then choose: the task reads in that order, and the eye used to land on the commitment
            first. Nothing is resolved until this is pressed -- the route calls Deezer on every request.
            No sample means no control at all -- a disabled button is a thing to try, and it drops out of
            tab order under the keyboard user who just reached it. */}
        {c.sample && <PlayButton sample={c.sample} player={player} onPlay={onPlay} />}
        <button className={c.chosen ? 'btn-primary' : 'btn-secondary'} onClick={onChoose}>Use this</button>
        {playing && <span className="elapsed mono">{elapsed(clock?.at ?? 0)}</span>}
      </div>
      {/* The card's own bottom edge, played portion accent and the rest tertiary. It is rendered at rest
          too, at zero opacity, so the fade at the end of a clip has something to fade. While the press is
          still resolving there is no position to draw, so the fill sweeps as an indeterminate sliver
          instead -- the card is already the playing card, it just has no position yet. */}
      {c.sample && (
        <span className={`sample-rail${waiting ? ' waiting' : ''}${stalled ? ' stalled' : ''}`} aria-hidden>
          <span className="sample-rail-fill" style={waiting ? undefined : { transform: `scaleX(${clock ? Math.min(1, clock.at / clock.of) : 0})` }} />
        </span>)}
    </div>
  )
}
