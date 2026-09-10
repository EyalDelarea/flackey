const R = 20.4
const CIRC = 2 * Math.PI * R

/** The transfer's percentage drawn as the app's own icon: a record spinning on a platter, its rim filling
    with the accent colour and the number sitting on the label where a pressing's own catalogue number
    would. It replaces the thin bar that used to sit under the title -- the point of the redesign was to
    make the percentage the thing you see first, and a 4px line beside a paragraph of grey text was not it.

    Every colour comes from a token. The first version painted the disc `#12161F` and the number `#fff`,
    which is a dark-mode record: on a light row it was a blue-black puck belonging to some other app. It
    also drew the loudest thing in the row, louder than the title -- so the disc is vinyl-neutral now and
    only the arc and the label carry the accent.

    A null `pct` is every state with no proportion to draw yet -- queued at the peer, sending before the
    size is known, or the bytes all landed and the file being checked. The rim sweeps a short arc instead
    of filling, the same signal the old bar's `.waiting` state gave: something is happening, but no bytes
    are moving. Nothing is progressing in those states, so the sweep is grey and slow rather than accent
    and quick: an indeterminate wait should not ask for attention that a running transfer has earned.
    There is no number to announce either, so the row's own `label` stands in -- it is the one string that
    tells those states apart, and inventing a second one here got it wrong. */
export default function Platter({ pct, label }: { pct: number | null; label: string }) {
  const waiting = pct === null
  const swept = waiting ? CIRC * 0.2 : (CIRC * Math.min(100, Math.max(0, pct))) / 100
  return (
    <div className={`platter${waiting ? ' waiting' : ''}`} role="img"
         aria-label={waiting ? label : `${Math.round(pct)}% transferred`}>
      <svg viewBox="0 0 44 44" width="44" height="44" aria-hidden focusable="false">
        {/* Rotated as a group so the fill starts at twelve o'clock: a CSS transform on the arc itself
            would replace this attribute outright, and the waiting sweep needs one of its own. */}
        <g transform="rotate(-90 22 22)">
          <circle className="rim" cx="22" cy="22" r={R} fill="none" strokeWidth="2.6" />
          <circle className="arc" cx="22" cy="22" r={R} fill="none" strokeWidth="2.6" strokeLinecap="round"
                  strokeDasharray={`${swept} ${CIRC}`} />
        </g>
        <g className="disc">
          <circle className="vinyl" cx="22" cy="22" r="17.6" />
          <circle className="groove" cx="22" cy="22" r="14.7" fill="none" strokeWidth=".6" />
          <circle className="groove faint" cx="22" cy="22" r="12.4" fill="none" strokeWidth=".6" />
          <circle className="label" cx="22" cy="22" r="10" />
          {/* The spindle hole, and only while there is no number to put in its place. */}
          {waiting && <circle className="spindle" cx="22" cy="22" r="1.4" />}
        </g>
        {!waiting && <text className="pct" x="22" y="22" textAnchor="middle" dominantBaseline="central">{Math.round(pct)}</text>}
      </svg>
    </div>
  )
}
