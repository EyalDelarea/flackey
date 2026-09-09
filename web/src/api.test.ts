import { api, ApiError } from './api'

function mockFetch(status: number, body: unknown) {
  globalThis.fetch = vi.fn(async () => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })) as never
}

describe('api', () => {
  it('parses JSON and sends bodies', async () => {
    mockFetch(200, { summary: 'Queued', request_ids: [1] })
    const s = await api.submit('https://youtu.be/x')
    expect(s.request_ids).toEqual([1])
    const [url, init] = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(url).toBe('/api/requests')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body)).toEqual({ url: 'https://youtu.be/x' })
  })
  it('turns a 400 detail into ApiError', async () => {
    mockFetch(400, { detail: 'Paste a YouTube link' })
    await expect(api.submit('nope')).rejects.toMatchObject({ status: 400, message: 'Paste a YouTube link' })
    await expect(api.submit('nope')).rejects.toBeInstanceOf(ApiError)
  })
  it('builds query strings', async () => {
    mockFetch(200, [])
    await api.library('astral', 3)
    expect((fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0][0]).toBe('/api/library?q=astral&playlist_id=3')
  })
})
