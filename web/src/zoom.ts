export function nextZoom(current: number, direction: 'in' | 'out' | 'reset'): number {
  const MIN = 0.7, MAX = 2.0, STEP = 0.1
  if (direction === 'reset') return 1

  let next = direction === 'in' ? current + STEP : current - STEP
  next = Math.round(next * 10) / 10

  if (next < MIN) return MIN
  if (next > MAX) return MAX
  return next
}

export function installZoom(): () => void {
  const loadZoom = () => {
    try {
      const saved = localStorage.getItem('krater.zoom')
      if (saved) {
        const zoom = parseFloat(saved)
        document.documentElement.style.zoom = zoom.toString()
      }
    } catch {}
  }

  const handleKeydown = (e: KeyboardEvent) => {
    if (!e.metaKey && !e.ctrlKey) return

    let direction: 'in' | 'out' | 'reset' | null = null
    if (e.key === '=' || e.key === '+' || e.key === 'Add') direction = 'in'
    else if (e.key === '-' || e.key === 'Subtract') direction = 'out'
    else if (e.key === '0') direction = 'reset'
    else return

    e.preventDefault()

    const current = parseFloat(document.documentElement.style.zoom || '1')
    const next = nextZoom(current, direction)
    document.documentElement.style.zoom = next.toString()

    try {
      localStorage.setItem('krater.zoom', next.toString())
    } catch {}
  }

  loadZoom()
  document.addEventListener('keydown', handleKeydown)
  return () => document.removeEventListener('keydown', handleKeydown)
}
