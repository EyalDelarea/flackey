import { render, screen } from '@testing-library/react'
import FormatOptions from './FormatOptions'

it('tooltips the WAV choice with the Rekordbox artwork caveat', () => {
  render(<FormatOptions formats={['aiff', 'wav', 'flac']} value="aiff" onChange={() => {}} />)
  expect(screen.getByRole('button', { name: /WAV/ }))
    .toHaveAttribute('title', 'Uncompressed audio with Rekordbox-readable text tags. Rekordbox does not import cover art from WAV files.')
})
