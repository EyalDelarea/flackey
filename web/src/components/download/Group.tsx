import Banner from '../Banner'
import RequestRow from './RequestRow'
import type { GroupView, RowAction } from '../../presentation'

interface Props { g: GroupView; whyOpen: Set<number>; onAction: (kind: RowAction['kind'], rowId: number, path?: string) => void
  onChoose: (rowId: number, cid: number) => void; onTryNow: (ids: number[]) => void
  playing: number | null; noPreview: ReadonlySet<number>; onPlay: (cid: number) => void }
export default function Group({ g, whyOpen, onAction, onChoose, onTryNow, playing, noPreview, onPlay }: Props) {
  const s = g.summary
  return (
    <section className="group-section">
      <div className="group-head"><h2>{g.name}</h2>
        <span className="summary">{s.filed} of {s.total} filed{s.inFlight > 0 && <> · <span className="needs">{s.inFlight} in progress</span></>}{s.needsChoice > 0 && <> · <span className="needs">{s.needsChoice} needs your choice</span></>}{s.rejected > 0 && <> · <span className="rej">{s.rejected} rejected</span></>}</span></div>
      <div className="group-bar" role="progressbar" aria-valuenow={s.pct} aria-valuemin={0} aria-valuemax={100}
           aria-label={`${g.name}: ${s.filed} of ${s.total} filed`}>
        <span className="group-bar-fill" style={{ width: `${s.pct}%` }} />
        {s.rejected > 0 && <span className="group-bar-bad" style={{ width: `${Math.round((s.rejected / Math.max(s.total, 1)) * 100)}%` }} />}
      </div>
      <div className="group">{g.rows.map(r => <RequestRow key={r.id} view={r} whyOpen={whyOpen.has(r.id)} onAction={onAction} onChoose={onChoose}
        playing={playing} noPreview={noPreview} onPlay={onPlay} />)}</div>
      {g.beatportDown && <Banner tone="red" text={`Beatport is unreachable. Matching is paused — trying again in ${g.beatportDown.seconds} seconds. Nothing is lost.`} action={{ label: 'Try now', onClick: () => onTryNow(g.beatportDown!.requestIds) }} />}
    </section>
  )
}
