import type { CandidateView } from '../../presentation'
export default function CandidateCard({ c, onChoose }: { c: CandidateView; onChoose: () => void }) {
  const meta = [c.length, c.onBeatport ? 'On Beatport' : 'Not on Beatport', c.lengthNote].filter(Boolean).join(' · ')
  return (
    <div className={`candidate${c.chosen ? ' chosen' : ''}`}>
      <div className="head"><span>{c.title} <span className="version">({c.version})</span></span>{c.score != null && <span className="score">{c.score}% match</span>}</div>
      <div className="meta">{meta}</div>
      <div><button className={c.chosen ? 'btn-primary' : 'btn-secondary'} onClick={onChoose}>Use this</button></div>
    </div>
  )
}
