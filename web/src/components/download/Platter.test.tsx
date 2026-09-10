import { render } from '@testing-library/react'
import Platter from './Platter'

it('always draws the track, so the arc is never a stroke with nothing behind it', () => {
  // The defect this locks: the track was painted in `--selection`, a value meant for the tint behind a
  // selected row, and it sat 1.5 units clear of the disc. Invisible ring plus a gap meant the arc read as
  // a fragment floating beside a dark circle rather than as the record filling up.
  for (const pct of [0, 15, 100, null]) {
    const { container } = render(<Platter pct={pct} label="x" />)
    expect(container.querySelector('.track')).not.toBeNull()
  }
})

it('leaves the ring empty at nought rather than drawing a cap with no length', () => {
  // A round linecap on a zero-length dash renders as a dot at twelve o'clock, which claims progress that
  // has not happened.
  const { container } = render(<Platter pct={0} label="x" />)
  expect(container.querySelector('.arc')).toBeNull()
  expect(container.querySelector('text')).toHaveTextContent('0')
})

it('holds still while it has a number, and moves only when it has none', () => {
  // The distinction is the whole signal: motion means nobody can say how far along this is. The disc used
  // to turn on a loop that could not be seen -- every shape in it was concentric with the centre of
  // rotation, so it was the identity transform, redrawn forever.
  const { container: measured } = render(<Platter pct={42} label="x" />)
  expect(measured.querySelector('.disc')).toBeNull()
  const { container: waiting } = render(<Platter pct={null} label="Waiting in someone's queue" />)
  expect(waiting.querySelector('.platter')).toHaveClass('waiting')
})

it('fits the three digits every finished transfer passes through', () => {
  const { container } = render(<Platter pct={100} label="x" />)
  expect(container.querySelector('text')).toHaveTextContent('100')
  expect(container.querySelector('.platter')).toHaveAttribute('aria-label', '100% transferred')
})
