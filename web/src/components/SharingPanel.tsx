import type { SharingState } from '../api'

/** Whether other Soulseek users can open a connection back to this Mac, and -- when they cannot -- the
 *  one thing the owner can do about it.
 *
 *  Two shapes. The full panel belongs in Settings: the router instructions are three sentences long and
 *  the owner is there to fix something. The compact one belongs in the setup step, which has no room for
 *  them and nothing to re-check mid-signup, so it says what is true and points at Settings.
 *
 *  The instructions never name the protocols the app tried on its own (they succeeded or they did not,
 *  and either way the owner cannot act on the difference) -- only the port, the two addresses to type
 *  into a router page, and the one reason a forward can look right and still not work: a VPN. */
export default function SharingPanel({ state, onCheck, compact = false }: { state: SharingState | null; onCheck: () => void; compact?: boolean }) {
  const s = state
  const checking = !!s?.checking
  // A check that is running says so; a result that has not arrived is not the same as a closed port.
  const closed = !checking && s?.reachable === false
  const sentence = checking ? 'Checking whether other people can reach you…'
    : s?.reachable === true ? 'Other Soulseek users can download from you.'
    : closed ? 'Your Soulseek port is closed, so most people cannot download from you.'
    : s?.error ?? 'Not checked yet.'
  const tone = checking ? '' : s?.reachable === true ? ' ok' : closed ? ' warn' : ''
  return (
    <div className={`sharing${compact ? ' compact' : ''}`}>
      <div className={`hint-row${tone}`}>{sentence}</div>
      {closed && s && (compact
        ? <div className="hint-row">You can open it later from Settings › Sharing.</div>
        // No port number, nothing to tell them to forward -- so the instructions wait for one rather
        // than printing a sentence with a hole in it.
        : s.port !== null && <div className="sharing-how">
            To open it, forward TCP port {s.port} on your router to this Mac{s.lan_ip ? ` (${s.lan_ip})` : ''}.
            {s.gateway ? ` Your router's address is ${s.gateway}.` : ''}
            {s.public_ip
              ? ` If you use a VPN, its exit address (${s.public_ip}) is what other users see, so the forward has to be set up in the VPN app or provider instead.`
              : ' If you use a VPN, the forward has to be set up in the VPN app or provider instead.'}
          </div>)}
      {!compact && <div className="row-gap">
        <button className="btn-secondary" onClick={onCheck} disabled={checking}>{checking ? 'Checking…' : 'Check again'}</button>
      </div>}
    </div>
  )
}
