import { useEffect, useState } from 'react'
import { ApiError, api } from '../../api'
import type { Live } from '../../live'
import { bucketCounts, groupRows } from '../../presentation'
import type { Bucket, RowAction } from '../../presentation'
import Banner from '../Banner'
import FilterBar from './FilterBar'
import Group from './Group'
import PasteBar from './PasteBar'

const FAILED_STATES = ['rejected', 'not_found', 'error', 'cancelled']
const TERMINAL_STATES = ['done', 'duplicate', ...FAILED_STATES]

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
  const [view, setView] = useState<'active' | 'history' | 'failed'>('active')
  // What History has already shown: a fresh id that lands in a terminal state while the owner is looking
  // elsewhere stays counted until they open History, so a finished batch is never silently absorbed.
  const [seenHistoryIds, setSeenHistoryIds] = useState<Set<number>>(new Set())
  const soulseekConnected = live.health?.lossless?.enabled && live.health.lossless.provider?.status === 'ok'
  const opts = { libraryRoot: live.settings?.library_root ?? '', telegramAuthorized: live.health?.telegram_authorized ?? true, soulseekConnected, now, whyOpen: false, fetchProgress: live.fetchProgress }
  const bundles = [...live.bundles.values()]
  const scoped = bundles.filter(b => view === 'active'
    ? ['queued', 'identifying', 'awaiting_review', 'fetching', 'verifying', 'filing'].includes(b.request.state)
    : view === 'failed' ? FAILED_STATES.includes(b.request.state)
    : TERMINAL_STATES.includes(b.request.state))
  const historyIds = bundles.filter(b => TERMINAL_STATES.includes(b.request.state)).map(b => b.request.id)
  const newInHistory = view === 'history' ? 0 : historyIds.filter(id => !seenHistoryIds.has(id)).length
  const openHistory = () => { setView('history'); setFilter('all'); setSeenHistoryIds(new Set(historyIds)) }
  const counts = bucketCounts(scoped)
  const groups = groupRows(scoped, live.playlists, opts, filter).map(g => ({ ...g, rows: g.rows.map(r => whyOpen.has(r.id) && r.action?.kind === 'why' ? { ...r, action: { ...r.action, label: 'Hide why' } } : r) }))
  const run = (p: Promise<unknown>) => p.then(() => setActionError(null)).catch(err => setActionError(err instanceof ApiError ? err.message : "That didn't work. Try again."))
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
        {view !== 'history' && <button className="chip" aria-pressed={view === 'failed'} onClick={() => { setView('failed'); setFilter('failed') }}>Failed <span className="count red">{bundles.filter(b => FAILED_STATES.includes(b.request.state)).length}</span></button>}
      </div>
      {live.upgradeActivity && <Banner tone="amber" text={live.upgradeActivity} />}
      {bundles.length > 0 && view !== 'failed' && <FilterBar filter={filter} counts={counts} onFilter={setFilter} onClearFailed={() => run(api.clearFailed())} view={view} />}
      <div className="scroll">
        {actionError && <Banner tone="red" text={actionError} action={{ label: 'Dismiss', onClick: () => setActionError(null) }} />}
        {groups.length === 0 && bundles.length === 0 && <div className="empty">Paste a link above to start digging.</div>}
        {groups.length === 0 && bundles.length > 0 && <div className="empty">{view === 'failed' ? 'No failed downloads.' : view === 'history' ? 'No completed downloads yet.' : 'No downloads in progress. Finished tracks and failures move to History.'}</div>}
        {groups.map(g => <Group key={g.key} g={g} whyOpen={whyOpen} onAction={onAction} onChoose={(rid, cid) => run(api.choose(rid, cid))} onTryNow={ids => ids.forEach(id => run(api.retry(id)))} />)}
      </div>
    </>
  )
}
