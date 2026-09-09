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
  const opts = { libraryRoot: live.settings?.library_root ?? '', telegramAuthorized: live.health?.telegram_authorized ?? true, now, whyOpen: false, fetchProgress: live.fetchProgress }
  const bundles = [...live.bundles.values()]
  const counts = bucketCounts(bundles)
  const groups = groupRows(bundles, live.playlists, opts, filter).map(g => ({ ...g, rows: g.rows.map(r => whyOpen.has(r.id) && r.action?.kind === 'why' ? { ...r, action: { ...r.action, label: 'Hide why' } } : r) }))
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
      <PasteBar onSubmit={async url => (await api.submit(url)).summary} inset={inset} />
      {bundles.length > 0 && <FilterBar filter={filter} counts={counts} onFilter={setFilter} onClearFailed={() => run(api.clearFailed())} />}
      <div className="scroll">
        {actionError && <Banner tone="red" text={actionError} action={{ label: 'Dismiss', onClick: () => setActionError(null) }} />}
        {groups.length === 0 && bundles.length === 0 && <div className="empty">Paste a link above to start digging.</div>}
        {groups.length === 0 && bundles.length > 0 && <div className="empty">Nothing here.</div>}
        {groups.map(g => <Group key={g.key} g={g} whyOpen={whyOpen} onAction={onAction} onChoose={(rid, cid) => run(api.choose(rid, cid))} onTryNow={ids => ids.forEach(id => run(api.retry(id)))} />)}
      </div>
    </>
  )
}
