import { useState } from 'react'
import Icon from '../Icon'

/** A stand-in link, plainly not a real video. The walkthrough never downloads anything, so there is no
 *  need for a real track -- and a real one would be either a commercial release, which nudges a new owner
 *  into fetching someone's copyrighted music before they have chosen anything, or a free/CC track, which
 *  is unlikely to be on Soulseek in lossless and would make the first thing they see a failure. */
export const SAMPLE_LINK = 'https://www.youtube.com/watch?v=your-track'

/** What happens to a pasted link, in the order the Download list's ladder shows it (STEP_TIPS in
 *  presentation.ts has the long versions). */
function steps(libraryRoot: string, soulseek: boolean, telegram: boolean): { name: string; text: string }[] {
  // Only the sources this owner set up: a skipped one is not part of their first download.
  const download = soulseek && telegram ? 'Soulseek first, for a lossless copy. Deezer is the fallback.'
    : soulseek ? 'From Soulseek, as a lossless copy.'
    : "From Deezer through Telegram, as an MP3. Add Soulseek in Settings for lossless copies."
  return [
    { name: 'Search', text: 'Works out which track it is: official title, remix and release.' },
    { name: 'Download', text: download },
    { name: 'Verify', text: soulseek ? 'Reads the spectrogram for signs of an MP3 dressed up as lossless.' : 'Checks the copy before it is filed.' },
    { name: 'Done', text: `Tagged with artwork and filed under the artist's folder in ${libraryRoot || 'your library'}.` },
  ]
}

/** Idea 10: the Ready screen's "first win" -- a short walkthrough of one download, so the first real paste
 *  on the empty Download list is not a leap in the dark. It is a demo only: no request leaves the page and
 *  nothing is added to the list. Revealed on a press rather than on arrival, so the Ready screen stays
 *  short for anyone who just wants to start. */
export default function TryItDemo({ libraryRoot, soulseek, telegram }: { libraryRoot: string; soulseek: boolean; telegram: boolean }) {
  const [open, setOpen] = useState(false)
  if (!open) return <button type="button" className="btn-link" onClick={() => setOpen(true)}>See how a download goes</button>
  return (<section className="try-it" aria-label="How a download goes">
    <div className="try-paste">
      <span className="try-link mono">{SAMPLE_LINK}</span>
      <span className="try-kind">Example</span>
    </div>
    <ol className="try-steps">
      {steps(libraryRoot, soulseek, telegram).map((s, i) => (
        <li key={s.name} style={{ animationDelay: `${300 + i * 450}ms` }}>
          <span className="try-mark"><Icon name="check" size={10} stroke={3} /></span>
          <span><b>{s.name}</b> {s.text}</span>
        </li>))}
    </ol>
    <p className="try-note">This is only a walkthrough; nothing was downloaded. Next, paste a real YouTube,
      YouTube Music or Spotify link on the Download page.</p>
  </section>)
}
