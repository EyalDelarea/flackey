import { adoptToken, apiToken, TOKEN_KEY } from './session'
import { api } from './api'

describe('the launch token', () => {
  beforeEach(() => { sessionStorage.clear(); window.history.replaceState(null, '', '/') })

  it('is taken from the fragment, kept for the session, and stripped from the address', () => {
    window.history.replaceState(null, '', '/?titlebar=inset#t=abc-123_XYZ')
    adoptToken()
    expect(apiToken()).toBe('abc-123_XYZ')
    expect(sessionStorage.getItem(TOKEN_KEY)).toBe('abc-123_XYZ')
    // The query the window opened with stays: it says which title bar to draw.
    expect(window.location.search).toBe('?titlebar=inset')
    expect(window.location.hash).toBe('')
  })

  it('survives a reload, when the fragment is already gone', () => {
    sessionStorage.setItem(TOKEN_KEY, 'kept')
    adoptToken()
    expect(apiToken()).toBe('kept')
  })

  it('leaves any other fragment alone', () => {
    window.history.replaceState(null, '', '/#settings')
    adoptToken()
    expect(apiToken()).toBeNull()
    expect(window.location.hash).toBe('#settings')
  })

  it('is sent on every API call', async () => {
    sessionStorage.setItem(TOKEN_KEY, 'tok')
    globalThis.fetch = vi.fn(async () => new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } })) as never
    await api.health()
    await api.submit('https://youtu.be/x')
    for (const [, init] of (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls) {
      expect(init.headers['x-flackey-token']).toBe('tok')
      expect(init.headers['x-flackey-app']).toBe('1')
    }
  })

  it('opens a session for the media elements, which cannot send a header', async () => {
    sessionStorage.setItem(TOKEN_KEY, 'tok')
    globalThis.fetch = vi.fn(async () => new Response('{"ok":true}', { status: 200, headers: { 'content-type': 'application/json' } })) as never
    await api.session()
    const [url, init] = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(url).toBe('/api/session')
    expect(init.method).toBe('POST')
    expect(init.headers['x-flackey-token']).toBe('tok')
  })
})
