import { readFileSync, readdirSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { insetTitlebar } from './platform'

const setSearch = (search: string) => { window.history.replaceState(null, '', `/${search}`) }

afterEach(() => { setSearch('') })

it('reads the inset flag from the query string', () => {
  expect(insetTitlebar()).toBe(false)
  setSearch('?titlebar=inset')
  expect(insetTitlebar()).toBe(true)
})

const src = resolve(__dirname)

function sources(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap(e =>
    e.isDirectory() ? sources(join(dir, e.name))
      // Test files are skipped on purpose: this one names the call in order to assert it is gone.
      : /\.tsx?$/.test(e.name) && !e.name.includes('.test.') ? [join(dir, e.name)] : [])
}

// The size of the window is the owner's answer, not the page's. Welcome and the setup steps used to ask
// for 720x600 through the bridge and the main shell asked for 1100x720 back, so the window snapped to a
// size of its own choosing whenever the screen changed underneath it -- discarding whatever the owner had
// just dragged the corner out to, and doing it again on the next render. The launcher opens the window at
// the size it was last left at (`desktop.window_size`) and nothing in the page may overrule that, so the
// bridge call is banned outright rather than removed from the two call sites that had it.
it('no source file asks the native window to resize itself', () => {
  const offenders = sources(src).filter(f => /pywebview[\s\S]{0,80}\.resize\b|requestWindowSize/.test(readFileSync(f, 'utf8')))
  expect(offenders).toEqual([])
})
