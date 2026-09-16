import { useEffect, useState } from 'react'
import { ApiError, api } from '../../api'
import type { Live } from '../../live'
import { bucketCounts, groupRows } from '../../presentation'
import type { Bucket, RowAction } from '../../presentation'
import Banner from '../Banner'
import FilterBar from './FilterBar'
import Group from './Group'
import PasteBar from './PasteBar'

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
  const opts = { libraryRoot: live.settings?.library_root ?? '', telegramAuthorized: live.health?.telegram_authorized ?? true, now, whyOpen: false, fetchProgress: live.fetchProgress }
  const bundles = [...live.bundles.values()]
  const scoped = bundles.filter(b => view === 'active'
    ? ['queued', 'identifying', 'awaiting_review', 'fetching', 'verifying', 'filing'].includes(b.request.state)
    : view === 'failed' ? ['rejected', 'not_found', 'error', 'cancelled'].includes(b.request.state)
    : ['done', 'duplicate', 'rejected', 'not_found', 'error', 'cancelled'].includes(b.request.state))
  const counts = bucketCounts(scoped)
  const groups = groupRows(scoped, live.playlists, opts, filter).map(g => ({ ...g, rows: g.rows.map(r => whyOpen.has(r.id) && r.action?.kind === 'why' ? { ...r, action: { ...r.action, label: 'Hide why' } } : r) }))
  const run = (p: Promise<unknown>) => p.then(() => setActionError(null)).catch(err => setActionError(err instanceof ApiError ? err.message : "That didn't work. Try again."))
  const onAction = (kind: RowAction['kind'], id: number, path?: string) => {
    if (kind === 'why') setWhyOpen(s => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n })
    else if (kind === 'reveal' && path) run(api.reveal(path))
    else if (kind === 'retry') run(api.retry(id))
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
      <div className="download-views" role="group" aria-label="Download view"><button className="chip" aria-pressed={view === 'active'} onClick={() => { setView('active'); setFilter('all') }}>Downloads</button><button className="chip" aria-pressed={view === 'history'} onClick={() => { setView('history'); setFilter('all') }}>History</button>{view !== 'history' && <button className="chip" aria-pressed={view === 'failed'} onClick={() => { setView('failed'); setFilter('failed') }}>Failed <span className="count red">{bundles.filter(b => ['rejected', 'not_found', 'error', 'cancelled'].includes(b.request.state)).length}</span></button>}</div>
      {live.upgradeActivity && <Banner tone="amber" text={live.upgradeActivity} />}
      {bundles.length > 0 && view !== 'failed' && <FilterBar filter={filter} counts={counts} onFilter={setFilter} onClearFailed={() => run(api.clearFailed())} view={view} />}
      <div className="scroll">
        {actionError && <Banner tone="red" text={actionError} action={{ label: 'Dismiss', onClick: () => setActionError(null) }} />}
        {groups.length === 0 && bundles.length === 0 && <div className="empty">Paste a link above to start digging.</div>}
        {groups.length === 0 && bundles.length > 0 && <div className="empty">{view === 'failed' ? 'No failed downloads.' : view === 'history' ? 'No completed downloads yet.' : 'No downloads in progress.'}</div>}
        {groups.map(g => <Group key={g.key} g={g} whyOpen={whyOpen} onAction={onAction} onChoose={(rid, cid) => run(api.choose(rid, cid))} onTryNow={ids => ids.forEach(id => run(api.retry(id)))} />)}
      </div>
    </>
  )
}
