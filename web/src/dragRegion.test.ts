import { readFileSync, readdirSync } from 'node:fs'
import { join, resolve } from 'node:path'

const src = resolve(__dirname)

function sources(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap(e =>
    e.isDirectory() ? sources(join(dir, e.name))
      // Test files are skipped on purpose: several of them name the class in order to assert it is gone.
      : /\.(tsx?|css)$/.test(e.name) && !e.name.includes('.test.') ? [join(dir, e.name)] : [])
}

// The window is moved by AppKit's title bar, which sits above the web view and hit-tests first, so the
// top 28px drag natively across the whole window. pywebview's own drag handling is the thing that broke:
// a mousedown on a `.pywebview-drag-region` makes its injected script post an absolute screen position
// back to Python on every mousemove, and the Cocoa side re-adds the origin of an `NSScreen.mainScreen()`
// frame snapshotted at window creation. On a single display at (0, 0) that addition is a no-op and the
// drag looks fine; with a second display attached the window teleports by that screen's origin. Reviving
// the class anywhere brings the jump back, so it is banned outright rather than per component.
it('no source file emits pywebview-drag-region', () => {
  const offenders = sources(src).filter(f => readFileSync(f, 'utf8').includes('pywebview-drag-region'))
  expect(offenders).toEqual([])
})
