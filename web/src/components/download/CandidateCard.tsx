import Icon from '../Icon'
import type { CandidateView } from '../../presentation'

interface Props { c: CandidateView; playing: boolean; noPreview: boolean; onChoose: () => void; onPlay: () => void }

export default function CandidateCard({ c, playing, noPreview, onChoose, onPlay }: Props) {
  const meta = [c.length, c.onBeatport ? 'On Beatport' : 'Not on Beatport', c.lengthNote].filter(Boolean).join(' · ')
  // The glyph is aria-hidden, so the button's whole name is this label -- and it carries the title because
  // five of these sit side by side and a row above may hold five more.
  const label = noPreview ? `No sample for ${c.title}` : `${playing ? 'Stop' : 'Play'} a sample of ${c.title}`
  return (
    <div className={`candidate${c.chosen ? ' chosen' : ''}`}>
      <div className="head"><span>{c.title} <span className="version">({c.version})</span></span>{c.score != null && <span className="score">{c.score}% match</span>}</div>
      <div className="meta">{meta}</div>
      <div className="actions">
        <button className={c.chosen ? 'btn-primary' : 'btn-secondary'} onClick={onChoose}>Use this</button>
        {/* Nothing is resolved until this is pressed: the route calls Deezer on every request, so a
            preloaded src per candidate would be one upstream call per card on screen. */}
        <button className="btn-secondary play" onClick={onPlay} disabled={noPreview} aria-label={label}
          title={noPreview ? 'No sample for this version' : undefined}>
          <Icon name={playing ? 'stop' : 'play'} size={12} stroke={1.8} />
        </button>
      </div>
    </div>
  )
}
