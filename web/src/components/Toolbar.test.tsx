import { render } from '@testing-library/react'
import Toolbar from './Toolbar'

// The toolbar carried a `.toolbar-drag` layer so empty toolbar space moved the window. It went with
// pywebview's JavaScript drag handling, which computed the new window position from screen coordinates
// and threw the window across the display; AppKit's title bar is the drag handle now.
it('renders its children and nothing else', () => {
  const { container } = render(<Toolbar><button>Click me</button></Toolbar>)
  const toolbar = container.querySelector('.toolbar')
  expect(toolbar).not.toBeNull()
  expect(toolbar?.children).toHaveLength(1)
  expect(toolbar?.firstElementChild?.tagName).toBe('BUTTON')
})

it('emits no drag layer and no pywebview drag region', () => {
  const { container } = render(<Toolbar><button>Click me</button></Toolbar>)
  expect(container.querySelector('.toolbar-drag')).toBeNull()
  expect(container.querySelector('.pywebview-drag-region')).toBeNull()
})
