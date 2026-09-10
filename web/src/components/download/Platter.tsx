/** Geometry, in the 44-unit box: the ring's inner edge (R - SW/2 = 17.4) sits a fraction under the disc's
    own edge (17.8), so the two are one object. The version before this left 1.5 units of gap between them
    and drew the track in `--selection` -- 9% white, invisible on the card -- so the arc had nothing behind
    it and nothing to hold on to, and read as a stroke floating beside a dark circle. */
const R = 19
const SW = 3.2
const CIRC = 2 * Math.PI * R
const VINYL = 17.8

/** The transfer's percentage drawn as the app's own icon: a record on a platter, its rim filling with the
    accent colour and the number where a pressing's catalogue number would be. It replaces the thin bar
    that used to sit under the title -- the point of the redesign was to make the percentage the thing you
    see first, and a 4px line beside a paragraph of grey text was not it.

    Every colour comes from a token. The first version painted the disc `#12161F` and the number `#fff`,
    which is a dark-mode record: on a light row it was a blue-black puck belonging to some other app. The
    disc is vinyl-neutral now -- dark in both themes, because records are -- and only the arc is accent.

    The number sits straight on the vinyl. It used to sit on a paper label, which cannot survive three
    digits: `100` is about 23 units wide, and a label big enough to hold it is 79% of the disc, at which
    point it is not a record label but a white circle with a thin dark rim. A real pressing's label is
    barely a third of the diameter, so the metaphor and a legible number were never going to fit into 44px
    together. The vinyl won -- it is the bigger surface, and it is the part that makes the thing a record.

    A null `pct` is every state with no proportion to draw yet -- queued at the peer, sending before the
    size is known, or the bytes all landed and the file being checked. The rim sweeps a short arc instead
    of filling, the same signal the old bar's `.waiting` state gave: something is happening, but no bytes
    are moving. Nothing is progressing in those states, so the sweep is grey and slow rather than accent
    and quick: an indeterminate wait should not ask for attention that a running transfer has earned.
    There is no number to announce either, so the row's own `label` stands in -- it is the one string that
    tells those states apart, and inventing a second one here got it wrong.

    That sweep is the only thing here that moves, and the distinction is the point: motion means nobody can
    say how far along this is, and a measured transfer holds still and shows you the number instead. The
    disc used to turn on a 4.2s loop, which was motion that could not be seen -- every shape in the group
    was a circle concentric with the centre of rotation, so the animation was the identity transform. All
    it bought was a widget re-rasterised at sub-pixel offsets forever, which is what made it look soft. */
export default function Platter({ pct, label }: { pct: number | null; label: string }) {
  const waiting = pct === null
  const swept = waiting ? CIRC * 0.22 : (CIRC * Math.min(100, Math.max(0, pct))) / 100
  return (
    <div className={`platter${waiting ? ' waiting' : ''}`} role="img"
         aria-label={waiting ? label : `${Math.round(pct)}% transferred`}>
      <svg viewBox="0 0 44 44" width="44" height="44" aria-hidden focusable="false">
        <circle className="vinyl" cx="22" cy="22" r={VINYL} />
        {/* One groove, and a whole unit wide. The pair this replaces were .6 wide, which is under a device
            pixel on a 1x display: they never resolved into lines, they only made the disc look out of
            focus. Static, so it rasterises once. */}
        <circle className="groove" cx="22" cy="22" r="14.4" fill="none" strokeWidth="1" />
        {/* Rotated as a group so the fill starts at twelve o'clock: a CSS transform on the arc itself
            would replace this attribute outright, and the waiting sweep needs one of its own. */}
        <g transform="rotate(-90 22 22)">
          <circle className="track" cx="22" cy="22" r={R} fill="none" strokeWidth={SW} />
          {swept > 0 && (
            <circle className="arc" cx="22" cy="22" r={R} fill="none" strokeWidth={SW} strokeLinecap="round"
                    strokeDasharray={`${swept} ${CIRC}`} />
          )}
        </g>
        {/* The spindle hole, and only while there is no number to put in its place. */}
        {waiting && <circle className="spindle" cx="22" cy="22" r="1.6" />}
        {/* `dy`, not `dominant-baseline: central`: WKWebView is the engine that renders the `title`
            attribute as nothing at all, and its baseline handling is not worth trusting for the one
            element whose whole job is to sit dead centre. .355em puts the cap height on the centre line. */}
        {!waiting && (
          <text className="pct" x="22" y="22" dy=".355em" textAnchor="middle">{Math.round(pct)}</text>
        )}
      </svg>
    </div>
  )
}
