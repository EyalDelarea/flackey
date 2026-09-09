import type { RejectionView } from '../../presentation'
export default function SpectrogramWell({ r }: { r: RejectionView }) {
  return (
    <div className="well">
      {r.spectrogramUrl && <div className="cut"><img src={r.spectrogramUrl} alt="spectrogram" />
        {r.cutoffKhz != null && <><div className="cutline" /><span className="cutlabel">nothing above {r.cutoffKhz} kHz</span></>}</div>}
      {!r.spectrogramUrl && r.cutoffKhz != null && <div className="cutlabel" style={{ position: 'static' }}>nothing above {r.cutoffKhz} kHz</div>}
      <div className="caption">{r.caption}</div>
    </div>
  )
}
