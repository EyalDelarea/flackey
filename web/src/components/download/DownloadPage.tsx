import { useEffect, useRef, useState } from 'react'
import { ApiError, api } from '../../api'
import type { Live } from '../../live'
import { bucketCounts, bucketOf, failedSummary, groupRows } from '../../presentation'
import type { Bucket, RowAction } from '../../presentation'
import type { Bundle } from '../../api'
import Banner from '../Banner'
import FilterBar from './FilterBar'
import Group from './Group'
import PasteBar from './PasteBar'

// Asked of `bucketOf` rather than spelled out here. This file used to keep its own list of the failed
// states, which is how the badge came to count four of them while the button beneath it retried two.
const isFailed = (b: Bundle) => bucketOf(b.request.state) === 'failed'
const isFinished = (b: Bundle) => isFailed(b) || bucketOf(b.request.state) === 'done'

function useNow(ms: number) {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => { const t = setInterval(() => setNow(new Date()), ms); return () => clearInterval(t) }, [ms])
  return now
}

export default function DownloadPage({ live, inset }: { live: Live; inset?: boolean }) {
  const now = useNow(1000)
  const [whyOpen, setWhyOpen] = useState<Set<number>>(new Set())
  const [actionError, setActionError] = useState<string | null>(null)
  const [filter, setFilter] = useState<Bucket | 'all'>('all')
  const [retryingAll, setRetryingAll] = useState(false)
  const [view, setView] = useState<'active' | 'history' | 'failed'>('active')
  // What History has already shown: a fresh id that lands in a terminal state while the owner is looking
  // elsewhere stays counted until they open History, so a finished batch is never silently absorbed.
  const [seenHistoryIds, setSeenHistoryIds] = useState<Set<number>>(new Set())
  const soulseekConnected = live.health?.lossless?.enabled && live.health.lossless.provider?.status === 'ok'
  const opts = { libraryRoot: live.settings?.library_root ?? '', telegramAuthorized: live.health?.telegram_authorized ?? true, soulseekConnected, now, whyOpen: false, fetchProgress: live.fetchProgress }
  const bundles = [...live.bundles.values()]
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
  const groups = groupRows(scoped, live.playlists, opts, filter).map(g => ({ ...g, rows: g.rows.map(r => whyOpen.has(r.id) && r.action?.kind === 'why' ? { ...r, action: { ...r.action, label: 'Hide why' } } : r) }))
  // Taken from the rows actually on screen, not from every failed request: whatever the view is filtered
  // down to is what "Retry all" retries, and only the rows carrying a retry button can be re-queued at all
  // -- a rejected or skipped track has nothing for the worker to try again, so it is not in the count.
  const retryableIds = groups.flatMap(g => g.rows).filter(r => r.action?.kind === 'retry').map(r => r.id)
  // Same rows, same filter, so the sentence and the button count the same population.
  const summary = failedSummary(scoped)
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
      }} inset={inset} />
      {!live.connected && live.lastSeen && <Banner tone="amber" text={`Reconnecting to Flackey… Last update ${live.lastSeen.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}. Showing the last known queue.`} />}
      <div className="download-views" role="group" aria-label="Download view">
        <button className="chip" aria-pressed={view === 'active'} onClick={() => { setView('active'); setFilter('all') }}>Downloads</button>
        <button className="chip" aria-pressed={view === 'history'} onClick={openHistory}>History{newInHistory > 0 && <span className="count amber" aria-hidden="true">{newInHistory}</span>}</button>
        {view !== 'history' && <button className="chip" aria-pressed={view === 'failed'} onClick={() => { setView('failed'); setFilter('failed') }}>Failed <span className="count red">{bundles.filter(isFailed).length}</span></button>}
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
        <div className="filterbar wide">
          {summary && <p className="failed-summary">{summary}</p>}
          <button className="btn-secondary" disabled={retryingAll || retryableIds.length === 0} onClick={retryAll}>
            {retryingAll ? 'Retrying…' : `Retry all ${retryableIds.length}`}
          </button>
        </div>
      )}
      <div className="scroll">
        {actionError && <Banner tone="red" text={actionError} action={{ label: 'Dismiss', onClick: () => setActionError(null) }} />}
        {groups.length === 0 && bundles.length === 0 && <div className="empty">Paste a link above to start digging.</div>}
        {groups.length === 0 && bundles.length > 0 && <div className="empty">{view === 'failed' ? 'No failed downloads.' : view === 'history' ? 'No completed downloads yet.' : 'No downloads in progress. Finished tracks and failures move to History.'}</div>}
        {groups.map(g => <Group key={g.key} g={g} whyOpen={whyOpen} onAction={onAction} onChoose={(rid, cid) => run(api.choose(rid, cid))} onTryNow={ids => ids.forEach(id => run(api.retry(id)))} />)}
      </div>
    </>
  )
}
