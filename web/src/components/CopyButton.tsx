import { useEffect, useRef, useState } from 'react'

/** Copies one string and says so for a moment. Used for the Soulseek password, which the owner has to be
 *  able to put somewhere safe: Soulseek has no password reset, so this is not a convenience.
 *
 *  `navigator.clipboard` is absent in some contexts (and in jsdom), so a failure leaves the label alone
 *  rather than claiming a copy that did not happen -- the text is on screen either way, selectable. */
export default function CopyButton({ value, label = 'Copy' }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  useEffect(() => () => clearTimeout(timer.current), [])

  const copy = () => {
    navigator.clipboard?.writeText(value).then(() => {
      setCopied(true)
      clearTimeout(timer.current)
      timer.current = setTimeout(() => setCopied(false), 1600)
    }).catch(() => { /* nothing to say: the value is on screen and can be selected */ })
  }
  return <button className="btn-secondary" onClick={copy}>{copied ? 'Copied' : label}</button>
}
