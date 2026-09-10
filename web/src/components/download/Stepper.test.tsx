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
  render(<Stepper steps={steps} />)
  const rung = screen.getByText('Search').closest('.step')!.querySelector('.step-tip-target')!
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
