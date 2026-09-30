import { render, screen } from '@testing-library/react'
import FormatOptions from './FormatOptions'

it('tooltips the WAV choice with the Rekordbox artwork caveat', () => {
  render(<FormatOptions formats={['aiff', 'wav', 'flac']} value="aiff" onChange={() => {}} />)
  expect(screen.getByRole('button', { name: /WAV/ }))
    .toHaveAttribute('title', 'Uncompressed audio with Rekordbox-readable text tags. Rekordbox does not import cover art from WAV files.')
})

it('puts the Default badge and the recommendation on AIFF when nothing says otherwise', () => {
  render(<FormatOptions formats={['aiff', 'wav', 'flac']} value="aiff" onChange={() => {}} />)
  expect(screen.getByRole('button', { name: /AIFF/ })).toHaveTextContent(/Default.*Recommended for Rekordbox/)
  expect(screen.getByRole('button', { name: /FLAC/ })).not.toHaveTextContent('Default')
})
