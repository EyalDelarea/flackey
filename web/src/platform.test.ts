import { insetTitlebar, requestWindowSize } from './platform'

const setSearch = (search: string) => { window.history.replaceState(null, '', `/${search}`) }
type W = Window & { pywebview?: { api?: { resize: (w: number, h: number) => void } } }

afterEach(() => { setSearch(''); delete (window as W).pywebview })

it('reads the inset flag from the query string', () => {
  expect(insetTitlebar()).toBe(false)
  setSearch('?titlebar=inset')
  expect(insetTitlebar()).toBe(true)
})

it('asks the pywebview window to resize when the bridge is there', () => {
  const resize = vi.fn()
  ;(window as W).pywebview = { api: { resize } }
  requestWindowSize(720, 540)
  expect(resize).toHaveBeenCalledWith(720, 540)
})

it('waits for pywebviewready when the bridge is not there yet, and is a no-op in a plain browser', () => {
  requestWindowSize(720, 540)   // nothing to call, must not throw
  const resize = vi.fn()
  ;(window as W).pywebview = { api: { resize } }
  window.dispatchEvent(new Event('pywebviewready'))
  expect(resize).toHaveBeenCalledWith(720, 540)
})
