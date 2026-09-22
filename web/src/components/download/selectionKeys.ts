import type { KeyboardEvent } from 'react'

/** Roving focus for a single selection: arrows wrap, Home/End jump to the edges. */
export function selectionKeys(event: KeyboardEvent<HTMLElement>) {
  const keys = ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End']
  if (!keys.includes(event.key)) return
  const items = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"], [role="radio"]'))
  const index = items.indexOf(event.target as HTMLButtonElement)
  if (index < 0) return
  event.preventDefault()
  const next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1
    : (index + (event.key === 'ArrowLeft' || event.key === 'ArrowUp' ? -1 : 1) + items.length) % items.length
  items[next].focus()
  items[next].click()
}
