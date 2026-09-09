// What the page knows about the window it lives in. Set by src/krater/desktop.py: the launcher adds
// `?titlebar=inset` on macOS and exposes `window.pywebview.api.resize`. In a plain browser both are absent.
type Bridge = { api?: { resize: (width: number, height: number) => unknown } }
const bridge = () => (window as Window & { pywebview?: Bridge }).pywebview

export const insetTitlebar = (): boolean => new URLSearchParams(window.location.search).get('titlebar') === 'inset'

export function requestWindowSize(width: number, height: number): void {
  const api = bridge()?.api
  if (api) { api.resize(width, height); return }
  window.addEventListener('pywebviewready', () => bridge()?.api?.resize(width, height), { once: true })
}
