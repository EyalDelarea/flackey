import { render, screen } from '@testing-library/react'
import EmptyDownloads from './EmptyDownloads'
import { PlatformContext } from '../../platform'

it('says what Flackey does and which links it takes', () => {
  render(<EmptyDownloads />)
  expect(screen.getByRole('heading', { name: 'Nothing downloading yet' })).toBeInTheDocument()
  expect(screen.getByText(/finds a lossless copy, checks it and files it/)).toBeInTheDocument()
  const chips = screen.getByRole('list', { name: 'Links Flackey takes' })
  expect(chips).toHaveTextContent('YouTube')
  expect(chips).toHaveTextContent('Spotify')
  expect(chips).toHaveTextContent('⌘V to paste')
})

it('names the Windows paste key on Windows', () => {
  render(<PlatformContext.Provider value="windows"><EmptyDownloads /></PlatformContext.Provider>)
  expect(screen.getByText('Ctrl+V to paste')).toBeInTheDocument()
})
