import { render, screen } from '@testing-library/react'
import Banner from './Banner'
import { driveBannerText } from '../presentation'

// The banner the app shows while the library's drive is unplugged (`App.tsx`). The text is built here and
// the App test checks it reaches the page; this pins the words and that nothing offers to wave it away.
describe('the music drive banner', () => {
  it('names the drive and how many tracks wait for it', () => {
    expect(driveBannerText({ connected: false, name: 'WD', waiting: 3 }))
      .toBe("Your music drive (WD) isn't connected. 3 tracks are waiting and will be filed when you plug it in.")
    expect(driveBannerText({ connected: false, name: 'WD', waiting: 1 }))
      .toBe("Your music drive (WD) isn't connected. 1 track is waiting and will be filed when you plug it in.")
  })

  it('still says so with nothing waiting yet, since anything downloaded next will wait', () => {
    expect(driveBannerText({ connected: false, name: 'E:', waiting: 0 }))
      .toBe("Your music drive (E:) isn't connected. New tracks will wait and be filed when you plug it in.")
  })

  it('is not there while the drive is plugged in, or before the worker has looked', () => {
    expect(driveBannerText({ connected: true, name: null, waiting: 0 })).toBeNull()
    expect(driveBannerText(null)).toBeNull()
    expect(driveBannerText(undefined)).toBeNull()
  })

  it('cannot be dismissed', () => {
    render(<Banner tone="amber" text={driveBannerText({ connected: false, name: 'WD', waiting: 2 })!} />)
    expect(screen.getByRole('status')).toHaveTextContent('2 tracks are waiting')
    expect(screen.queryByRole('button', { name: 'Dismiss' })).not.toBeInTheDocument()
  })
})
