import { render, screen } from '@testing-library/react'
import FormatOptions from './FormatOptions'

it('gives the WAV choice the Rekordbox artwork caveat on the card itself, with no duplicate tooltip', () => {
  render(<FormatOptions formats={['aiff', 'wav', 'flac']} value="aiff" onChange={() => {}} />)
  const wav = screen.getByRole('button', { name: /WAV/ })
  expect(wav).toHaveTextContent('Uncompressed audio with Rekordbox-readable text tags. Rekordbox does not import cover art from WAV files.')
  expect(wav).not.toHaveAttribute('title')
})

it('puts the Default badge and the recommendation on AIFF when nothing says otherwise', () => {
  render(<FormatOptions formats={['aiff', 'wav', 'flac']} value="aiff" onChange={() => {}} />)
  expect(screen.getByRole('button', { name: /AIFF/ })).toHaveTextContent(/Default.*Recommended for Rekordbox/)
  expect(screen.getByRole('button', { name: /FLAC/ })).not.toHaveTextContent('Default')
})
