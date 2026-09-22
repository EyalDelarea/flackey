import { useEffect, useRef, useState } from 'react'
import { ApiError, api, candidatePreviewUrl } from '../../api'
import type { Live } from '../../live'
import { bucketCounts, bucketOf, failedSummary, groupRows, matchesFilter } from '../../presentation'
import type { Filter, RowAction } from '../../presentation'
import type { Bundle } from '../../api'
import Banner from '../Banner'
import FilterBar from './FilterBar'
import Group from './Group'
import PasteBar from './PasteBar'

/** Everything the page's one <audio> knows, spread over the cards that might be the one playing. It lives
 *  here because the element does; `Group` and `RequestRow` only carry it down. */
export interface PlayerState {
  /** The candidate the owner pressed. Set on the press, not on the audio: the card lights immediately. */
  playing: number | null
  /** Where that clip is, once the element has reported a position at all. Null means pressed and waiting. */
  clock: { id: number; at: number; of: number } | null
  /** Four seconds after a press with still nothing to play. */
  slow: boolean
  /** The candidate whose last press could not be loaded. Transient -- the next press clears it. */
  failed: number | null
  /** Candidates Deezer has no sample for, known before any of this is drawn. They show no play control. */
  noSample: ReadonlySet<number>
}

// Deezer's previews are 30 s. Only a fallback: the element reports the real duration on the first
// `timeupdate`, and it is NaN until metadata lands.
const SAMPLE_SECONDS = 30

// Asked of `bucketOf` rather than spelled out here. This file used to keep its own list of the failed
// states, which is how the badge came to count four of them while the button beneath it retried two.
const isFailed = (b: Bundle) => bucketOf(b.request.state) === 'failed'
const isFinished = (b: Bundle) => isFailed(b) || bucketOf(b.request.state) === 'done'

function useNow(ms: number) {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => { const t = setInterval(() => setNow(new Date()), ms); return () => clearInterval(t) }, [ms])
  return now
}

export default function DownloadPage({ live }: { live: Live }) {
  const now = useNow(1000)
  const [whyOpen, setWhyOpen] = useState<Set<number>>(new Set())
  const [actionError, setActionError] = useState<string | null>(null)
  // One axis or the other, never both: `Bucket` and `Stage` share no value, so a single predicate picks
  // whichever the pressed chip named. Stage is a filter here and nothing more -- the list still groups by
  // playlist, because each group owns a bar and a summary that nesting would break.
  const [filter, setFilter] = useState<Filter>('all')
  const [retryingAll, setRetryingAll] = useState(false)
  const [view, setView] = useState<'active' | 'history' | 'failed'>('active')
  // One element for the whole page, and the id it is playing: two rows can sit in `awaiting_review` at
  // once, and starting a sample in one has to stop the one already running in the other.
  const audio = useRef<HTMLAudioElement>(null)
  // What the press says, not what the audio says: the card lights the moment it is pressed, and the clock
  // arrives whenever the element gets round to it.
  const [playing, setPlaying] = useState<number | null>(null)
  // Where the clip is, once the element has said so at all. It carries the id because it outlives
  // `playing` by the length of the ended fade -- a rail belongs to the card it was filling.
  const [clock, setClock] = useState<PlayerState['clock']>(null)
  // A press whose load came to nothing. Availability is settled before the card is drawn, so this is the
  // transient half: a signed URL that expired, Deezer unreachable at press time. The next press clears it
  // and the button never goes away.
  const [failed, setFailed] = useState<number | null>(null)
  // Which candidate the element's `src` points at. The error event carries no id, and the handler's own
  // `playing` can already have moved on to the next press by the time a failed load reports back -- so
  // the id the blame belongs to rides in a ref, written at the same moment as the `src`.
  const source = useRef<number | null>(null)
  // What History has already shown: a fresh id that lands in a terminal state while the owner is looking
  // elsewhere stays counted until they open History, so a finished batch is never silently absorbed.
  const [seenHistoryIds, setSeenHistoryIds] = useState<Set<number>>(new Set())
  const soulseekConnected = live.health?.lossless?.enabled && live.health.lossless.provider?.status === 'ok'
  const opts = { libraryRoot: live.settings?.library_root ?? '', telegramAuthorized: live.health?.telegram_authorized ?? true, soulseekConnected, now, whyOpen: false, fetchProgress: live.fetchProgress }
  const bundles = [...live.bundles.values()]
  // Whether Deezer has a sample is known before anything is drawn: `has_preview` rides along on the
  // candidate the queue already returns, and a card in here shows no play control at all -- not a dead
  // one. Two ways to land in it. An explicit `false` is Deezer answering no. A missing `deezer_id` is a
  // candidate that never went through Deezer enrichment at all, so it reports `has_preview: null` for the
  // same reason it has no identity to resolve -- and the route 404s on it without so much as a call. What
  // is left is a genuine unknown: a row older than the column, which keeps its button and behaves exactly
  // as it always has, because the route may well succeed.
  const noSample = new Set(bundles.flatMap(b => b.candidates)
    .filter(c => c.has_preview === false || c.deezer_id == null).map(c => c.id))
  const scoped = bundles.filter(b => view === 'active' ? !isFinished(b)
    : view === 'failed' ? isFailed(b)
    : isFinished(b))
  const historyIds = bundles.filter(isFinished).map(b => b.request.id)
  // The first snapshot this launch is history the owner already knows about, not a batch that "just"
  // finished -- otherwise a returning owner with a long history sees it all counted as new on open.
  const seededHistory = useRef(false)
  useEffect(() => {
    if (seededHistory.current || bundles.length === 0) return
    seededHistory.current = true
    setSeenHistoryIds(new Set(historyIds))
  }, [bundles.length])
  const newInHistory = view === 'history' ? 0 : historyIds.filter(id => !seenHistoryIds.has(id)).length
  const openHistory = () => { setView('history'); setFilter('all'); setSeenHistoryIds(new Set(historyIds)) }
  const counts = bucketCounts(scoped)
  // Two populations, deliberately. The chips count what the tab is showing (`scoped`); the batch bar above
  // each group counts the whole playlist (`bundles`), because a Downloads tab that has already dropped every
  // finished row would otherwise draw a bar reading "0 filed" on a batch that is half filed.
  const groups = groupRows(scoped, live.playlists, opts, filter, bundles).map(g => ({ ...g, rows: g.rows.map(r => whyOpen.has(r.id) && r.action?.kind === 'why' ? { ...r, action: { ...r.action, label: 'Hide why' } } : r) }))
  // Taken from the rows actually on screen, not from every failed request: whatever the view is filtered
  // down to is what "Retry all" retries, and only the rows carrying a retry button can be re-queued at all
  // -- a rejected or skipped track has nothing for the worker to try again, so it is not in the count.
  const retryableIds = groups.flatMap(g => g.rows).filter(r => r.action?.kind === 'retry').map(r => r.id)
  // The rows on screen, narrowed by the same exported predicate `groupRows` filters with -- not a second
  // spelling of it, which is how the sentence came to describe all 55 failures beside a button offering to
  // retry the 12 the chip had left. One predicate, so the two cannot drift apart again.
  const visible = scoped.filter(b => matchesFilter(b.request, filter))
  const summary = failedSummary(visible)
  const failMessage = (err: unknown) => err instanceof ApiError ? err.message : "That didn't work. Try again."
  const run = (p: Promise<unknown>) => p.then(() => setActionError(null)).catch(err => setActionError(failMessage(err)))
  const retryAll = () => {
    setRetryingAll(true)
    api.retryFailed(retryableIds)
      .then(async ({ retried }) => {
        // A 200 that re-queued nothing is still a press that did nothing: say so rather than leave the
        // owner watching an unchanged list.
        setActionError(retried.length === 0 ? 'Nothing could be retried — this list may be out of date.' : null)
        await live.refresh()
      })
      .catch(err => setActionError(failMessage(err)))
      .finally(() => setRetryingAll(false))
  }
  // Nothing on screen needs the clip once it has stopped, so the element does not keep it. Clearing the
  // attribute and re-running the load drops the decoded buffer; the next press re-resolves the sample
  // anyway, because the route signs a fresh URL every time. `source` goes with it, so the abort this
  // provokes has nobody to blame.
  const release = (el: HTMLAudioElement) => { source.current = null
    if (!el.hasAttribute('src')) return   // nothing was ever pressed, so there is nothing to let go of
    el.pause(); el.removeAttribute('src'); el.load() }
  // Detaching the element does not stop it -- a removed <audio> keeps playing its audio -- and `App`
  // swaps this whole page out for Library or Settings. Without this, switching tabs mid-sample leaves a
  // clip running with nothing on screen to stop it, and its buffer held for as long as the app is open.
  useEffect(() => { const el = audio.current; return () => { if (el) release(el) } }, [])
  // Pressing Stop, choosing a candidate and Esc all end the same way.
  const stop = () => { const el = audio.current; if (!el) return; el.currentTime = 0; release(el); setPlaying(null); setClock(null) }
  // Pressed, and still nothing to play. Keyed on `playing` as well, so a second press while the first is
  // still resolving starts the four seconds over rather than inheriting them.
  const waiting = playing != null && clock?.id !== playing
  const [slow, setSlow] = useState(false)
  useEffect(() => {
    if (!waiting) { setSlow(false); return }
    const timer = window.setTimeout(() => setSlow(true), 4000)
    return () => window.clearTimeout(timer)
  }, [playing, waiting])
  // Esc stops whatever is playing, from anywhere on the page -- the one candidate running is never more
  // than a keystroke away, however far the card has scrolled.
  useEffect(() => {
    if (playing == null) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') stop() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [playing])
  const onPlay = (cid: number) => {
    const el = audio.current
    if (!el) return
    el.pause()
    setClock(null)
    setFailed(null)
    // A second press on the one that is playing is a stop, and a stop rewinds: the next press should
    // start the sample over rather than resume its last two seconds.
    if (playing === cid) { el.currentTime = 0; release(el); setPlaying(null); return }
    // Assigning `src` re-runs the element's load, even with the same string -- which is what makes a
    // replay resolve a fresh signature instead of chasing the expired one.
    source.current = cid
    el.src = candidatePreviewUrl(cid)
    setPlaying(cid)
    // Pausing the element -- or repointing its `src` -- rejects a `play()` that has not settled yet, with
    // an AbortError. Switching candidates does both, so this lands for the candidate left behind, one
    // microtask after the next one has been made the playing id. Clearing `playing` unconditionally there
    // would wipe out the new one: its sample would go on playing with its button back on Play, and no
    // Stop anywhere -- the very state the unmount cleanup exists to prevent.
    el.play()?.catch(() => setPlaying(p => (p === cid ? null : p)))
  }
  const onAction = (kind: RowAction['kind'], id: number, path?: string) => {
    if (kind === 'why') setWhyOpen(s => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n })
    else if (kind === 'reveal' && path) run(api.reveal(path))
    else if (kind === 'retry') run(api.retry(id).then(() => live.refresh()))
    else if (kind === 'cancel') run(api.cancel(id))
    else if (kind === 'remove') run(api.removeRequest(id).then(() => live.dropBundle(id)))
  }
  return (
    <>
      <PasteBar onSubmit={async url => {
        const submission = await api.submit(url)
        await live.refresh()
        return submission.summary
      }} />
      {!live.connected && live.lastSeen && <Banner tone="amber" text={`Reconnecting to Flackey… Last update ${live.lastSeen.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}. Showing the last known queue.`} />}
      <div className="download-views" role="group" aria-label="Download view">
        <button className="chip" aria-pressed={view === 'active'} onClick={() => { setView('active'); setFilter('all') }}>Downloads</button>
        <button className="chip" aria-pressed={view === 'history'} onClick={openHistory}>History{newInHistory > 0 && <span className="count amber" aria-hidden="true">{newInHistory}</span>}</button>
        {view !== 'history' && <button className="chip" aria-pressed={view === 'failed'} onClick={() => { setView('failed'); setFilter('all') }}>Failed <span className="count red">{bundles.filter(isFailed).length}</span></button>}
      </div>
      {live.upgradeActivity && <Banner tone="amber" text={live.upgradeActivity} />}
      {bundles.length > 0 && view !== 'failed' && <FilterBar filter={filter} counts={counts} onFilter={setFilter} onClearFailed={() => run(api.clearFailed())} view={view} />}
      {/* Shown for the whole tab, not only when something is retryable: a tab badged "Failed 45" whose
          rows are all rejected needs a disabled "Retry all 0" to answer why, where an absent button
          just looks like the feature is missing.
          The sentence beside it is the rest of that answer. The badge counts every failure and the button
          counts the ones the worker will take back, so the two numbers disagree by design -- and until this
          line was here, nothing on screen said so. It is built from the rows in view, like the ids are, so
          a filtered-down tab explains the tab the owner is actually looking at. `.filterbar` is 38px of
          single-line chrome elsewhere; the wide modifier lets this one grow to hold a sentence. */}
      {view === 'failed' && scoped.length > 0 && (
        <div className="filterbar wide stacked">
          <FilterBar filter={filter} counts={counts} onFilter={setFilter} onClearFailed={() => run(api.clearFailed())} view="failed" bare />
          <div className="filterbar-row">
          {summary && <p className="failed-summary">{summary}</p>}
          <button className="btn-secondary" disabled={retryingAll || retryableIds.length === 0} onClick={retryAll}>
            {retryingAll ? 'Retrying…' : `Retry all ${retryableIds.length}`}
          </button>
          </div>
        </div>
      )}
      <div className="scroll">
        {actionError && <Banner tone="red" text={actionError} action={{ label: 'Dismiss', onClick: () => setActionError(null) }} />}
        {groups.length === 0 && bundles.length === 0 && <div className="empty">Paste a link above to start digging.</div>}
        {groups.length === 0 && bundles.length > 0 && <div className="empty">{view === 'failed' ? 'No failed downloads.' : view === 'history' ? 'No completed downloads yet.' : 'No downloads in progress. Finished tracks and failures move to History.'}</div>}
        {/* Choosing unmounts the whole row, candidates and Stop button with it, so the clip has to be
            stopped at press time or it plays on with nothing on screen able to end it. */}
        {groups.map(g => <Group key={g.key} g={g} view={view} whyOpen={whyOpen} onAction={onAction} onChoose={(rid, cid) => { stop(); run(api.choose(rid, cid)) }} onTryNow={ids => ids.forEach(id => run(api.retry(id)))}
          player={{ playing, clock, slow, failed, noSample }} onPlay={onPlay} />)}
      </div>
      {/* The page's one player. `preload="none"` and no `src` until a play is pressed, because the route
          resolves the sample against Deezer on every request -- and none again the moment it stops.
          A load that fails here is a press that did not work, never a verdict on the candidate: whether it
          has a sample at all was settled before the card was drawn. So the error says so on that card and
          leaves its button live. The clock is kept on `ended` and dropped on the next press, so the rail
          finishes full and fades out full rather than draining as it goes. */}
      <audio ref={audio} preload="none"
        onTimeUpdate={e => { const id = source.current; if (id == null) return
          const el = e.currentTarget
          setClock({ id, at: el.currentTime, of: Number.isFinite(el.duration) && el.duration > 0 ? el.duration : SAMPLE_SECONDS }) }}
        onEnded={() => { const el = audio.current; if (el) release(el); setPlaying(null) }}
        onError={() => { const id = source.current; if (id == null) return
          setFailed(id); setPlaying(p => (p === id ? null : p)); setClock(c => (c?.id === id ? null : c)) }} />
    </>
  )
}
