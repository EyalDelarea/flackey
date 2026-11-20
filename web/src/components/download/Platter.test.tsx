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

it('draws nought the way it draws a queue wait: a sweep and no number', () => {
  // A round linecap on a zero-length dash renders as a dot at twelve o'clock, which claims progress that
  // has not happened -- and a big `0` beside a row queued at the peer drew two pictures of the same thing:
  // no bytes yet. Under a whole percent is that state too, so it never rounds to a `0` either.
  for (const pct of [0, 0.4]) {
    const { container } = render(<Platter pct={pct} label="0.0 MB of 58.9 MB from peer" />)
    expect(container.querySelector('.platter')).toHaveClass('waiting')
    expect(container.querySelector('text')).toBeNull()
    expect(container.querySelector('.platter')).toHaveAttribute('aria-label', '0.0 MB of 58.9 MB from peer')
  }
  const { container: queued } = render(<Platter pct={null} label="Waiting in peer's queue" />)
  const { container: nought } = render(<Platter pct={0} label="Waiting in peer's queue" />)
  expect(nought.innerHTML).toBe(queued.innerHTML)
})

it('shows the number from the first whole percent', () => {
  const { container } = render(<Platter pct={1} label="x" />)
  expect(container.querySelector('.platter')).not.toHaveClass('waiting')
  expect(container.querySelector('text')).toHaveTextContent('1')
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
