import { render, screen, fireEvent } from '@testing-library/react'
import Stepper from './Stepper'
import { STEP_TIPS } from '../../presentation'

const steps = [{ name: 'Search', state: 'done' as const }, { name: 'Download', state: 'current' as const }]

it('explains a rung on hover', () => {
  // The `title` attribute this replaces renders nothing at all inside the native window: WKWebView has no
  // tooltip layer of its own. The explanation has to be an element the app draws itself.
  const { container } = render(<Stepper steps={steps} />)
  const rung = container.querySelectorAll('.step')[1]     // the tooltip names the rung too, so find it by position
  expect(screen.queryByRole('tooltip')).toBeNull()
  fireEvent.mouseEnter(rung)
  expect(screen.getByRole('tooltip')).toHaveTextContent(STEP_TIPS.Download)
  fireEvent.mouseLeave(rung)
  expect(screen.queryByRole('tooltip')).toBeNull()
})

it('explains a rung on keyboard focus too, since it replaces a native affordance', () => {
  const { container } = render(<Stepper steps={steps} />)
  const rung = container.querySelectorAll('.step')[0].querySelector('.step-tip-target')!
  fireEvent.focus(rung)
  expect(screen.getByRole('tooltip')).toHaveTextContent(STEP_TIPS.Search)
  fireEvent.blur(rung)
  expect(screen.queryByRole('tooltip')).toBeNull()
})

it('escapes the scrolling card it lives in rather than being clipped by it', () => {
  // The ladder sits inside `.scroll`, an overflow-y: auto ancestor. A tooltip rendered in place would be
  // cut off by it, so it goes to the end of the document and is placed in viewport coordinates.
  const { container } = render(<Stepper steps={steps} />)
  fireEvent.mouseEnter(container.querySelectorAll('.step')[1])
  const tip = screen.getByRole('tooltip')
  expect(tip.parentElement).toBe(document.body)
  expect(tip.style.position).toBe('fixed')
})

it('drops below a rung that has no room above it, rather than off the top of the window', () => {
  // Every rect is at the origin under jsdom, which is exactly the case this guards: a rung at the very
  // top of the window. Above is the better side -- the pointer is not covering it -- but not off-screen.
  const { container } = render(<Stepper steps={steps} />)
  fireEvent.mouseEnter(container.querySelectorAll('.step')[1])
  expect(screen.getByRole('tooltip')).toHaveClass('below')
})

it('draws a hollow ring on the rung a parked row will resume at, not a fourth grey dot', () => {
  // A row parked on a backoff used to draw four identical grey rungs -- the same picture as a track that
  // has not started. The ring says where it picks up when the wait is over.
  const { container } = render(<Stepper steps={[{ name: 'Search', state: 'paused' }, { name: 'Download', state: 'pending' }]} />)
  expect(container.querySelectorAll('.step')[0]).toHaveClass('paused')
  expect(container.querySelectorAll('.step')[0].getAttribute('aria-current')).toBeNull()
})

it('prints only the name of the rung the row is on, and keeps every name for the tooltip and screen readers', () => {
  // Four names took a 256px column and cut the track titles short. The dots say how far along a row is.
  const { container } = render(<Stepper steps={[...steps, { name: 'Verify', state: 'pending' }]} />)
  expect([...container.querySelectorAll('.step-name')].map(n => n.textContent)).toEqual(['Download'])
  expect(screen.getByRole('button', { name: /^Search:/ })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /^Verify:/ })).toBeInTheDocument()
})

it('names the rung a parked row will resume at', () => {
  const { container } = render(<Stepper steps={[{ name: 'Search', state: 'paused' }, { name: 'Download', state: 'pending' }]} />)
  expect([...container.querySelectorAll('.step-name')].map(n => n.textContent)).toEqual(['Search'])
})
