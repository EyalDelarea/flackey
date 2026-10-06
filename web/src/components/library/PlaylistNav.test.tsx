import { render, screen, fireEvent } from '@testing-library/react'
import PlaylistNav from './PlaylistNav'
import type { Playlist } from '../../api'

let nextId = 1
const pl = (name: string, source_url: string, created_at = '2026-01-01T00:00:00'): Playlist =>
  ({ id: nextId++, source_url, name, created_at, updated_at: created_at, track_ids: [], file: '' })
const mine = (name: string) => pl(name, `https://open.spotify.com/playlist/${name}`)
const album = (name: string, created_at?: string) => pl(`Album - ${name}`, `https://open.spotify.com/album/${name}`, created_at)
const labels = () => screen.getAllByRole('button').map(b => b.textContent)

beforeEach(() => localStorage.clear())

test('album playlists sit under their own Albums group, newest first, without the prefix', () => {
  render(<PlaylistNav playlists={[mine('Goa'), album('Old', '2026-01-01'), album('New', '2026-02-01')]} selected={null} onSelect={() => {}} />)
  expect(labels()).toEqual(['All tracks', 'Goa', 'Albums2', 'New', 'Old'])
})

test('collapsing Albums hides them and is remembered', () => {
  const props = { playlists: [mine('Goa'), album('Old')], selected: null, onSelect: () => {} }
  const { unmount } = render(<PlaylistNav {...props} />)
  fireEvent.click(screen.getByRole('button', { name: /Albums/ }))
  expect(screen.queryByText('Old')).toBeNull()
  unmount()
  render(<PlaylistNav {...props} />)
  expect(screen.getByRole('button', { name: /Albums/ })).toHaveAttribute('aria-expanded', 'false')
})

test('the search box appears once the list is long, and filters across both groups', () => {
  const many = [mine('Goa trance'), ...Array.from({ length: 8 }, (_, i) => album(`Record ${i}`)), album('Goa Gil')]
  render(<PlaylistNav playlists={many} selected={null} onSelect={() => {}} />)
  fireEvent.click(screen.getByRole('button', { name: /Albums/ }))
  fireEvent.change(screen.getByLabelText('Find a playlist'), { target: { value: 'goa' } })
  expect(labels()).toEqual(['Goa trance', 'Albums1', 'Goa Gil'])
  fireEvent.change(screen.getByLabelText('Find a playlist'), { target: { value: 'zzz' } })
  expect(screen.getByText(/No playlist matches/)).toBeInTheDocument()
})

test('a short list has no search box', () => {
  render(<PlaylistNav playlists={[mine('Goa')]} selected={null} onSelect={() => {}} />)
  expect(screen.queryByLabelText('Find a playlist')).toBeNull()
})
