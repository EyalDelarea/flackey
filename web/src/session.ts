/* The launch token. Every `/api/` request must carry it (src/flackey/web/guard.py). The window that
   opens the page hands it over in the fragment -- `#t=...`, which the browser never sends anywhere --
   and in browser mode it is the link `flackey start` printed. It is kept in sessionStorage so a reload
   still has it, and taken out of the address bar so it is not copied along with the address. */
export const TOKEN_KEY = 'flackey-token'

function storage(): Storage | null {
  try { return window.sessionStorage } catch { return null }
}

/* `t` from the fragment, percent-decoded and nothing else: URLSearchParams would also apply form rules
   and read a `+` as a space, which is a different token. */
function fragmentToken(): string | null {
  for (const part of window.location.hash.replace(/^#/, '').split('&')) {
    if (!part.startsWith('t=')) continue
    try { return decodeURIComponent(part.slice(2)) || null } catch { return null }
  }
  return null
}

export function adoptToken(): void {
  const token = fragmentToken()
  if (!token) return
  try {
    const s = storage()
    if (s) s.setItem(TOKEN_KEY, token)
    else memory = token
  } catch { memory = token /* storage refused: kept for this page only */ }
  window.history.replaceState(window.history.state, '', window.location.pathname + window.location.search)
}

let memory: string | null = null

export function apiToken(): string | null {
  try {
    const s = storage()
    return s ? s.getItem(TOKEN_KEY) : memory
  } catch { return memory }
}
