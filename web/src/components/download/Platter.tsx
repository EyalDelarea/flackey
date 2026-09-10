import { useId } from 'react'

const R = 20.4
const CIRC = 2 * Math.PI * R

/** The transfer's percentage drawn as the app's own icon: a record spinning on a platter, its rim filling
    with the accent colour and the number sitting on the label where a pressing's own catalogue number
    would. It replaces the thin bar that used to sit under the title -- the point of the redesign was to
    make the percentage the thing you see first, and a 4px line beside a paragraph of grey text was not it.

    `pct === null` is the queued state: the peer has accepted the request but has not started sending, so
    there is no number to show. The rim sweeps a short arc instead of filling, which is the same signal the
    old bar's `.waiting` sweep gave -- something is happening, but nothing is arriving yet. */
export default function Platter({ pct }: { pct: number | null }) {
  const waiting = pct === null
  // One gradient per instance: several rows download at once, and a shared `id` would be a duplicate.
  // `useId` returns colons, which are legal in an id but awkward inside `url(#...)`; strip them.
  const label = `platter${useId().replace(/:/g, '')}`
  const swept = waiting ? CIRC * 0.22 : (CIRC * Math.min(100, Math.max(0, pct))) / 100
  return (
    <div className={`platter${waiting ? ' waiting' : ''}`} role="img"
         aria-label={waiting ? 'Waiting in the queue' : `${Math.round(pct)}% transferred`}>
      <svg viewBox="0 0 44 44" width="44" height="44" aria-hidden focusable="false">
        <defs>
          <linearGradient id={label} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#7DDCFF" /><stop offset="1" stopColor="#0A84FF" />
          </linearGradient>
        </defs>
        {/* Rotated as a group so the fill starts at twelve o'clock: a CSS transform on the arc itself
            would replace this attribute outright, and the waiting sweep needs one of its own. */}
        <g transform="rotate(-90 22 22)">
          <circle className="rim" cx="22" cy="22" r={R} fill="none" strokeWidth="2.4" />
          <circle className="arc" cx="22" cy="22" r={R} fill="none" strokeWidth="2.4" strokeLinecap="round"
                  strokeDasharray={`${swept} ${CIRC}`} />
        </g>
        <g className="disc">
          <circle cx="22" cy="22" r="17.6" fill="#12161F" />
          <circle cx="22" cy="22" r="14.7" fill="none" stroke="rgba(255,255,255,.16)" strokeWidth=".6" />
          <circle cx="22" cy="22" r="12.4" fill="none" stroke="rgba(255,255,255,.10)" strokeWidth=".6" />
          <circle cx="22" cy="22" r="9.4" fill={`url(#${label})`} stroke="rgba(0,0,0,.35)" strokeWidth=".8" />
          {waiting && <circle cx="22" cy="22" r="1" fill="#0B0D12" />}
        </g>
        {!waiting && <text className="pct" x="22" y="22" textAnchor="middle" dominantBaseline="central">{Math.round(pct)}</text>}
      </svg>
    </div>
  )
}
