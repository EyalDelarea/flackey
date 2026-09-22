import { useEffect, useState } from 'react'
import Artwork from './Artwork'
import CandidateCard from './CandidateCard'
import Checks from './Checks'
import SpectrogramWell from './SpectrogramWell'
import Stepper from './Stepper'
import Platter from './Platter'
import Icon from '../Icon'
import type { RowAction, RowView } from '../../presentation'

/** The wait, on the right rail as a pill. It says what the row is doing -- waiting -- and not only when
 *  that ends, because "Retry in 14m" under a status line read as a footnote to a row that looked idle for
 *  no reason. The three scales and the coarse tick above an hour are unchanged (#74). */
function RetryCountdown({ seconds }: { seconds: number }) {
  const [remaining, setRemaining] = useState(seconds)
  useEffect(() => { setRemaining(seconds) }, [seconds])
  // Above an hour the label only changes once a minute, so tick once a minute: the 6 h wait for Soulseek
  // (issue #74) was otherwise 21,600 re-renders of the same five words. Inside the hour it goes back to a
  // real per-second countdown, which is where a countdown is worth watching.
  const coarse = remaining > 3600
  const done = remaining <= 0
  useEffect(() => {
    if (done) return
    const step = coarse ? 60 : 1
    const timer = window.setInterval(() => setRemaining(value => Math.max(0, value - step)), step * 1000)
    return () => window.clearInterval(timer)
  }, [coarse, done])
  // Three scales, because the waits now span three orders of magnitude: 30 s on the quick ladder, 15 min
  // after a Soulseek queue, and 6 h while the request waits for the people online to change. A bare
  // `360m 0s` would read as a stuck row.
  const label = remaining <= 0 ? 'Retrying now' : remaining >= 3600
    ? `Waiting · ${Math.floor(remaining / 3600)}h ${Math.floor((remaining % 3600) / 60)}m`
    : remaining >= 60
      ? `Waiting · ${Math.floor(remaining / 60)}m ${remaining % 60}s`
      : `Waiting · ${remaining}s`
  return <span className="waiting-pill" aria-live="off"><span className="waiting-ring" aria-hidden="true" />{label}</span>
}

interface Props { view: RowView; whyOpen?: boolean; onAction: (kind: RowAction['kind'], rowId: number, path?: string) => void; onChoose: (rowId: number, candidateId: number) => void }

export default function RequestRow({ view: v, whyOpen, onAction, onChoose }: Props) {
  // `parked` washes a run of waiting rows into a band the eye can skip, at about half `washed` so it does
  // not compete with the rows that are actually asking for something.
  const cls = ['row', v.dimmed && 'dimmed', v.washed && 'washed', v.rejected && 'rejected',
    v.stage === 'waiting' && 'parked'].filter(Boolean).join(' ')
  // The ladder belongs under the title, spanning the row - not squeezed into the right rail beside the buttons.
  const showSteps = v.steps && !v.rejected && v.formatLabel == null
  return (
    <div className={cls}>
      <div className="row-main">
        <Artwork url={v.artworkUrl} rejected={v.rejected} />
        <div className="row-text">
          {/* Where it stopped, on the row rather than only in the chips above: the All list mixes every kind
              of failure, and a failed row has no ladder left to read it off. */}
          <div className="title">{v.bucket === 'failed' && v.stage && <span className={`stage-tag tone-${v.stage}`}>{v.stage}</span>}{v.title}{v.version && <span className="version"> ({v.version})</span>}</div>
          {v.status && <div className={`status ${v.statusTone}`}>{v.status}</div>}
          {/* The percentage moved to the platter on the right; what stays here is the part it cannot
              show -- how much of how big, from whom, how fast. */}
          {v.progress && <div className="xfer-label">{v.progress.label}</div>}
          {v.fallback && <div className="fallback" title={v.fallback.reason}>{v.fallback.reason}</div>}
          {/* The status line above says what happened; this one says whether it is over. Every failed row
              carries it, on the Failed tab and in History alike -- "why is this here and can it come back"
              is the same question wherever the row is read. */}
          {v.outcome && <div className={`outcome${v.outcome.retryable ? '' : ' final'}`}>{v.outcome.note}</div>}
          <Checks checks={v.checks} />
        </div>
        {showSteps && <div className="row-steps"><Stepper steps={v.steps!} /></div>}
        <div className="row-right">
          {v.retryInSeconds != null && <RetryCountdown seconds={v.retryInSeconds} />}
          {v.progress && <Platter pct={v.progress.pct} label={v.progress.label} />}
          {v.fallback ? <span className="tag amber">{v.fallback.label}</span>
            : v.formatLabel ? <span className="verified"><Icon name="check" size={13} stroke={2.2} />{v.formatLabel}</span>
            : v.tag ? <span className="tag">{v.tag}</span> : null}
          {/* A cancel sits under the choices when there are choices to skip past, and on the right of the
              row when there are not -- which is where a track that is downloading right now needs it. */}
          {v.action && (v.action.kind !== 'cancel' || !v.candidates) && (
            <button className="btn-secondary" onClick={() => onAction(v.action!.kind, v.id, v.action!.path)}>{v.action.label}</button>)}
          {v.removable && <button className="btn-link muted" onClick={() => onAction('remove', v.id, undefined)}>Remove</button>}
        </div>
      </div>
      {v.candidates && (<>
        <div className="candidates">{v.candidates.map(c => <CandidateCard key={c.id} c={c} onChoose={() => onChoose(v.id, c.id)} />)}</div>
        {v.action?.kind === 'cancel' && <div className="skip"><button className="btn-link" onClick={() => onAction('cancel', v.id, undefined)}>{v.action.label}</button></div>}
      </>)}
      {v.rejection && whyOpen && <SpectrogramWell r={v.rejection} />}
    </div>
  )
}
