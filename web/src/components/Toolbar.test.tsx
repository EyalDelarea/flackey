import { render } from '@testing-library/react'
import Toolbar from './Toolbar'

it('renders the drag layer only when inset', () => {
  const { container, rerender } = render(<Toolbar>child</Toolbar>)
  expect(container.querySelector('.toolbar-drag')).toBeNull()
  expect(container.querySelector('.pywebview-drag-region')).toBeNull()

  rerender(<Toolbar inset={false}>child</Toolbar>)
  expect(container.querySelector('.toolbar-drag')).toBeNull()

  rerender(<Toolbar inset>child</Toolbar>)
  const drag = container.querySelector('.toolbar-drag')
  expect(drag).not.toBeNull()
  expect(drag).toHaveClass('pywebview-drag-region')
})

it('renders the drag layer as a sibling of the controls, not an ancestor', () => {
  const { container } = render(<Toolbar inset><button>Click me</button></Toolbar>)
  const toolbar = container.querySelector('.toolbar')
  expect(toolbar?.firstElementChild).toHaveClass('toolbar-drag')
  const button = toolbar?.querySelector('button')
  expect(button).not.toBeNull()
  // pywebview starts a window drag by walking up from the click target looking for a
  // `.pywebview-drag-region` ancestor. If the button were nested inside the drag layer
  // instead of next to it, every click on it would start a drag and the click would be lost.
  expect(button?.closest('.pywebview-drag-region')).toBeNull()
})
