import Banner from '../Banner'
import RequestRow from './RequestRow'
import type { GroupView, RowAction, Stage } from '../../presentation'

/** One segment of the batch bar: a count, the class that tones it and the words beside its dot. The two
 *  tabs draw the same widget over different questions -- "where is this batch" and "where did it stop" --
 *  so they share everything but this list. */
interface Segment { key: string; n: number; label: string }
const shown = (segments: Segment[]) => segments.filter(s => s.n > 0)

/* The Failed tab's bar, split by the stage a run stopped on. Worded rather than named: "Search 12" is the
   chip's job, and a legend beneath a bar has room to say what stopping there meant. Pipeline order, with
   the two answers that are not rungs at the end. */
const FAILED_LEGEND: Record<Stage, string> = {
  search: 'found nothing to download',
  choose: 'stopped while waiting on you',
  download: 'could not finish the download',
  verify: 'failed the quality check',
  stopped: 'you stopped',
  waiting: 'still waiting',
  unknown: 'stopped for a reason this copy did not record',
}
const FAILED_ORDER: Stage[] = ['search', 'choose', 'download', 'verify', 'stopped', 'waiting', 'unknown']

interface Props {
  g: GroupView
  /** Which question the batch bar answers. The rows are the tab's business; the bar is the batch's. */
  view: 'active' | 'history' | 'failed'
  whyOpen: Set<number>
  onAction: (kind: RowAction['kind'], rowId: number, path?: string) => void
  onChoose: (rowId: number, cid: number) => void
  onTryNow: (ids: number[]) => void
}
export default function Group({ g, view, whyOpen, onAction, onChoose, onTryNow }: Props) {
  const s = g.summary
  const failedView = view === 'failed'
  /* Every row in the batch lands in exactly one segment, so the bar fills its width and nothing goes
     missing into an unexplained gap: filed, working, waiting on the owner, parked on a backoff, failed.
     `inFlight` is working right now and no longer counts the parked rows -- the header used to say
     "41 in progress" an inch above the bar drawn to correct precisely that. */
  const segments = failedView
    ? shown(FAILED_ORDER.map(st => ({ key: st, n: s.failedStages[st], label: FAILED_LEGEND[st] })))
    : shown([
      { key: 'filed', n: s.filed, label: 'filed' },
      { key: 'working', n: s.inFlight, label: 'working' },
      { key: 'needs', n: s.needsChoice, label: 'needs you' },
      { key: 'waiting', n: s.waiting, label: 'waiting to retry' },
      { key: 'failed', n: s.failed, label: 'failed' },
    ])
  // Proportional, never rounded: five rounded percentages do not add up to 100 and the error shows as a
  // sliver of track at the end of a bar that is in fact complete.
  const bar = segments.map(x => <span key={x.key} className={`group-seg tone-${x.key}`} style={{ flexGrow: x.n }} />)
  return (
    <section className="group-section">
      <div className="group-head"><h2>{g.name}</h2>
        {/* The counts live under the bar now, beside the colour that carries them. What belongs up here is
            the size of the batch -- and, on the Failed tab, how much of it stopped. */}
        <span className="summary">{failedView ? `${s.failed} of ${s.total} stopped` : `${s.total} tracks`}</span></div>
      {failedView
        ? <div className="group-bar" role="img" aria-label={`${g.name}: ${segments.map(x => `${x.n} ${x.label}`).join(', ')}`}>{bar}</div>
        : <div className="group-bar" role="progressbar" aria-valuenow={s.pct} aria-valuemin={0} aria-valuemax={100}
               aria-label={`${g.name}: ${s.filed} of ${s.total} filed`}>{bar}</div>}
      <ul className="group-legend">
        {segments.map(x => (
          <li key={x.key} className="legend-item"><span className={`legend-dot tone-${x.key}`} aria-hidden="true" />{x.n} {x.label}</li>
        ))}
      </ul>
      <div className="group">{g.rows.map(r => <RequestRow key={r.id} view={r} whyOpen={whyOpen.has(r.id)} onAction={onAction} onChoose={onChoose} />)}</div>
      {g.beatportDown && <Banner tone="red" text={`Beatport is unreachable. Matching is paused — trying again in ${g.beatportDown.seconds} seconds. Nothing is lost.`} action={{ label: 'Try now', onClick: () => onTryNow(g.beatportDown!.requestIds) }} />}
    </section>
  )
}
