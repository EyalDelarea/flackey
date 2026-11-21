import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, api } from '../../api'
import type { RecheckRun } from '../../api'
import { plural } from '../../presentation'

const POLL_MS = 2000

/** "Check all files again": today's quality check over every library file, one at a time in the
    background. Opt-in and read-only -- the server never moves or replaces a file for it. */
export function useLibraryRecheck(onFinished: () => void) {
  const [run, setRun] = useState<RecheckRun | null>(null)
  const [error, setError] = useState<string | null>(null)
  const finished = useRef(onFinished)
  finished.current = onFinished

  // A run started before this page opened (or in another window) is picked up where it is
  // -- only while it is still going: a finished run was already reported, and Dismiss stays dismissed.
  useEffect(() => { api.recheckStatus().then(r => { if (r.state === 'running') setRun(r) }).catch(() => undefined) }, [])

  const running = run?.state === 'running'
  useEffect(() => {
    if (!running) return
    const t = setInterval(() => {
      api.recheckStatus().then(next => {
        setRun(next)
        if (next.state !== 'running') finished.current()
      }).catch(() => undefined)
    }, POLL_MS)
    return () => clearInterval(t)
  }, [running])

  const start = useCallback(() => {
    setError(null)
    api.recheckLibrary().then(setRun)
      .catch(err => setError(err instanceof ApiError ? err.message : "Couldn't start checking the library. Try again."))
  }, [])
  const stop = useCallback(() => {
    api.stopRecheck().then(r => { setRun(r); finished.current() }).catch(() => undefined)
  }, [])
  const dismiss = useCallback(() => setRun(r => r && { ...r, state: 'idle' }), [])

  let text: string | null = null
  if (run?.state === 'running') {
    text = `Checking every file again with today's check: ${run.done} of ${run.total}`
      + (run.flagged ? ` · ${plural(run.flagged, 'file')} to look at` : '') + '. Nothing is moved or changed.'
  } else if (run?.state === 'done' || run?.state === 'stopped') {
    const head = run.state === 'done' ? `Checked ${plural(run.done, 'file')} again.` : `Stopped after ${plural(run.done, 'file')}.`
    text = `${head} ${run.flagged ? `${plural(run.flagged, 'file')} did not pass cleanly and ${run.flagged === 1 ? 'is' : 'are'} marked “Check flagged”. Click a track to see why.` : 'Nothing new to worry about.'}`
  } else if (run?.state === 'error') {
    text = `Checking the library stopped after ${plural(run.done, 'file')} because of an error. Try again.`
  }
  return { run, running, text, error, start, stop, dismiss, clearError: () => setError(null) }
}
