import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const css = readFileSync(resolve(__dirname, 'app.css'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '')
const desktop = readFileSync(resolve(__dirname, '../../src/flackey/desktop.py'), 'utf8')

/** The `(width, height)` of a tuple constant in desktop.py -- the launcher is the only place that knows
 *  how small the window may be dragged, and the stylesheet has to be written for the same number. */
function pySize(name: string): [number, number] {
  const m = desktop.match(new RegExp(`^${name}\\s*=\\s*\\((\\d+),\\s*(\\d+)\\)`, 'm'))
  if (!m) throw new Error(`${name} is not a (width, height) tuple in desktop.py any more`)
  return [Number(m[1]), Number(m[2])]
}

/** The declarations of every rule naming exactly `selector` inside `@media (max-width: <px>)`. */
function atWidth(px: number, selector: string): string[] {
  const block = css.match(new RegExp(`@media \\(max-width: ${px}px\\) \\{([\\s\\S]*?)\\n\\}`))
  if (!block) return []
  return [...block[1].matchAll(/([^{}]+)\{([^{}]*)\}/g)]
    .filter(([, sel]) => sel.split(',').some(s => s.trim() === selector))
    .flatMap(([, , body]) => body.split(';').map(d => d.trim()).filter(Boolean))
}

const NARROW = 700

// The window opens at `MAIN_SIZE` only on a first launch and can be dragged down to `MIN_SIZE`. With the
// inset title bar the window is one full-size content view, so its frame width *is* the viewport width
// the stylesheet sees -- the two numbers live in different languages but mean the same pixels. Lower
// MIN_SIZE past the narrowest breakpoint and the fixed-width pieces (a 220px sidebar, a 220px QR column,
// three format cards abreast) run off the right of a window the owner is allowed to make.
it('the smallest window the owner can drag to is inside the narrow layout', () => {
  const [minWidth] = pySize('MIN_SIZE')
  expect(minWidth).toBeLessThanOrEqual(NARROW)
})

it('opens no smaller than it can be dragged', () => {
  const [mainWidth, mainHeight] = pySize('MAIN_SIZE')
  const [minWidth, minHeight] = pySize('MIN_SIZE')
  expect(mainWidth).toBeGreaterThanOrEqual(minWidth)
  expect(mainHeight).toBeGreaterThanOrEqual(minHeight)
})

// jsdom does no layout, so these hold the rules that decide whether the content fits rather than watching
// it fit. Each is a fixed width wider than the content pane at MIN_SIZE once the sidebar has taken its cut.
it('stacks the two-column pieces that are wider than the content pane at the minimum', () => {
  expect(atWidth(NARROW, '.tg')).toContain('grid-template-columns: minmax(0, 1fr)')
  expect(atWidth(NARROW, '.format-options')).toContain('grid-template-columns: minmax(0, 1fr)')
})

it('narrows the fixed-width sidebar so the content pane keeps a usable share', () => {
  const [minWidth] = pySize('MIN_SIZE')
  const sidebar = atWidth(NARROW, '.sidebar').find(d => d.startsWith('width:'))
  expect(sidebar).toBeDefined()
  const px = Number(sidebar!.match(/(\d+)px/)![1])
  expect(minWidth - px).toBeGreaterThanOrEqual(320)
})

// The setup steps are the reason the page used to resize the window: a step too tall for the frame drew
// its heading over the stepper, and the fix was to make the window bigger. The step scrolls instead now,
// so this is what stops that overlap coming back the next time a step grows.
it('lets a setup step that outgrows the window scroll inside it', () => {
  const rule = css.match(/\.setup-content \{([^}]*)\}/)
  expect(rule?.[1]).toMatch(/overflow-y:\s*auto/)
  expect(rule?.[1]).toMatch(/min-height:\s*0/)
})
