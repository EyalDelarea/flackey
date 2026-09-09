import { nextZoom, installZoom } from './zoom'

describe('nextZoom', () => {
  it('resets to 1', () => {
    expect(nextZoom(0.8, 'reset')).toBe(1)
    expect(nextZoom(1.5, 'reset')).toBe(1)
  })

  it('zooms in by 10%', () => {
    expect(nextZoom(1, 'in')).toBe(1.1)
    expect(nextZoom(1.1, 'in')).toBe(1.2)
  })

  it('zooms out by 10%', () => {
    expect(nextZoom(1, 'out')).toBe(0.9)
    expect(nextZoom(1.1, 'out')).toBe(1)
  })

  it('clamps to min 0.7', () => {
    expect(nextZoom(0.7, 'out')).toBe(0.7)
    expect(nextZoom(0.75, 'out')).toBe(0.7)
  })

  it('clamps to max 2', () => {
    expect(nextZoom(2, 'in')).toBe(2)
    expect(nextZoom(1.95, 'in')).toBe(2)
  })
})

describe('installZoom', () => {
  beforeEach(() => {
    document.documentElement.style.zoom = ''
    const store: Record<string, string> = {}
    const mockStorage = {
      getItem: (key: string) => store[key] ?? null,
      setItem: (key: string, value: string) => { store[key] = value },
      removeItem: (key: string) => { delete store[key] },
      clear: () => { Object.keys(store).forEach(k => delete store[k]) },
      key: (i: number) => Object.keys(store)[i] ?? null,
      length: 0,
    }
    Object.defineProperty(mockStorage, 'length', { get: () => Object.keys(store).length })
    vi.stubGlobal('localStorage', mockStorage)
  })

  afterEach(() => {
    document.documentElement.style.zoom = ''
    vi.unstubAllGlobals()
  })

  it('loads zoom from localStorage on install', () => {
    localStorage.setItem('flackey.zoom', '1.5')
    const uninstall = installZoom()
    expect(document.documentElement.style.zoom).toBe('1.5')
    uninstall()
  })

  it('handles keydown for Cmd+=', () => {
    const uninstall = installZoom()
    const e = new KeyboardEvent('keydown', { key: '=', metaKey: true })
    document.dispatchEvent(e)
    expect(document.documentElement.style.zoom).toBe('1.1')
    uninstall()
  })

  it('handles keydown for Cmd+-', () => {
    document.documentElement.style.zoom = '1.2'
    const uninstall = installZoom()
    const e = new KeyboardEvent('keydown', { key: '-', metaKey: true })
    document.dispatchEvent(e)
    expect(document.documentElement.style.zoom).toBe('1.1')
    uninstall()
  })

  it('handles keydown for Cmd+0', () => {
    document.documentElement.style.zoom = '1.5'
    const uninstall = installZoom()
    const e = new KeyboardEvent('keydown', { key: '0', metaKey: true })
    document.dispatchEvent(e)
    expect(document.documentElement.style.zoom).toBe('1')
    uninstall()
  })

  it('persists zoom to localStorage', () => {
    const uninstall = installZoom()
    const e = new KeyboardEvent('keydown', { key: '=', metaKey: true })
    document.dispatchEvent(e)
    expect(localStorage.getItem('flackey.zoom')).toBe('1.1')
    uninstall()
  })

  it('calls preventDefault on handled keys', () => {
    const uninstall = installZoom()
    const e = new KeyboardEvent('keydown', { key: '=', metaKey: true })
    const preventSpy = vi.spyOn(e, 'preventDefault')
    document.dispatchEvent(e)
    expect(preventSpy).toHaveBeenCalled()
    uninstall()
  })

  it('does not call preventDefault on unhandled keys', () => {
    const uninstall = installZoom()
    const e = new KeyboardEvent('keydown', { key: 'a', metaKey: true })
    const preventSpy = vi.spyOn(e, 'preventDefault')
    document.dispatchEvent(e)
    expect(preventSpy).not.toHaveBeenCalled()
    uninstall()
  })

  it('returns an uninstaller function that stops handling keys', () => {
    const uninstall = installZoom()
    const e1 = new KeyboardEvent('keydown', { key: '=', metaKey: true })
    document.dispatchEvent(e1)
    expect(document.documentElement.style.zoom).toBe('1.1')
    uninstall()
    const e2 = new KeyboardEvent('keydown', { key: '=', metaKey: true })
    document.dispatchEvent(e2)
    expect(document.documentElement.style.zoom).toBe('1.1')
  })

  it('works with ctrlKey on non-macOS', () => {
    const uninstall = installZoom()
    const e = new KeyboardEvent('keydown', { key: '=', ctrlKey: true })
    document.dispatchEvent(e)
    expect(document.documentElement.style.zoom).toBe('1.1')
    uninstall()
  })
})
