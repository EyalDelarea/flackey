import Artwork from './Artwork'
import CandidateCard from './CandidateCard'
import Checks from './Checks'
import SpectrogramWell from './SpectrogramWell'
import Stepper from './Stepper'
import Platter from './Platter'
import Icon from '../Icon'
import type { RowAction, RowView } from '../../presentation'

interface Props { view: RowView; whyOpen?: boolean; onAction: (kind: RowAction['kind'], rowId: number, path?: string) => void; onChoose: (rowId: number, candidateId: number) => void }

export default function RequestRow({ view: v, whyOpen, onAction, onChoose }: Props) {
  const cls = ['row', v.dimmed && 'dimmed', v.washed && 'washed', v.rejected && 'rejected'].filter(Boolean).join(' ')
  // The ladder belongs under the title, spanning the row - not squeezed into the right rail beside the buttons.
  const showSteps = v.steps && !v.rejected && v.formatLabel == null
  return (
    <div className={cls}>
      <div className="row-main">
        <Artwork url={v.artworkUrl} rejected={v.rejected} />
        <div className="row-text">
          <div className="title">{v.title}{v.version && <span className="version"> ({v.version})</span>}</div>
          <div className={`status ${v.statusTone}${v.statusPath ? ' path' : ''}`}>{v.status}</div>
          {showSteps && <Stepper steps={v.steps!} />}
          {/* The percentage moved to the platter on the right; what stays here is the part it cannot
              show -- how much of how big, from whom, how fast. */}
          {v.progress && <div className="xfer-label">{v.progress.label}</div>}
          {v.fallback && <div className="fallback" title={v.fallback.reason}>{v.fallback.reason}</div>}
          <Checks checks={v.checks} />
        </div>
        <div className="row-right">
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
