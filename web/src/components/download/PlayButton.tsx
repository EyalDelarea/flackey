import Icon from '../Icon'
import type { SampleView } from '../../presentation'
import type { PlayerState } from './DownloadPage'

interface Props { sample: SampleView; player: PlayerState; onPlay: (key: string, url: string) => void }

/** The one play control, wherever a sample can be heard. It was inline in `CandidateCard` until the
 *  rejected row needed exactly the same thing -- same lit state, same "Stop" on a second press, same
 *  naming of what is about to play -- and two copies of that would have drifted the first time either
 *  moved. It owns no state: the page's single <audio> decides what is playing, and this asks. */
export default function PlayButton({ sample, player, onPlay }: Props) {
  const playing = player.playing === sample.key
  return (
    // The glyph is aria-hidden, so the button's whole name is this label -- and it carries what the clip
    // is, because several of these can sit side by side and the ordinary case is samples that differ only
    // in a word.
    <button className={`btn-secondary play${playing ? ' on' : ''}`} onClick={() => onPlay(sample.key, sample.url)}
      aria-label={`${playing ? 'Stop' : 'Play'} a sample of ${sample.label}`}>
      <Icon name={playing ? 'stop' : 'play'} size={11} stroke={2} filled />
    </button>
  )
}
