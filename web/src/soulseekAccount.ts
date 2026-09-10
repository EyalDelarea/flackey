/** Soulseek has no sign-up form: a username is claimed by the first client that signs in with it, and from
 *  then on that name is bound to that password. So flackey can make the account for the owner -- fill both
 *  fields, one click, done -- and the only thing it owes them in return is the credentials themselves,
 *  because there is no reset. Losing the password loses the account, permanently.
 *
 *  Deliberately no `flackey-` prefix: peers see this name, and a shared prefix would mark every flackey
 *  user as running an unofficial client. These read as ordinary Soulseek names because they are. */

const ADJECTIVES = ['amber', 'brisk', 'cobalt', 'dusty', 'ember', 'fleet', 'golden', 'hazy', 'indigo',
  'jade', 'lunar', 'mellow', 'north', 'opal', 'plush', 'quiet', 'rusty', 'saffron', 'tidal', 'umber',
  'velvet', 'warm']
const NOUNS = ['acetate', 'baseline', 'crate', 'dubplate', 'echo', 'fader', 'groove', 'jog', 'kick',
  'loop', 'master', 'needle', 'offbeat', 'platter', 'riser', 'shuffle', 'tempo', 'vinyl', 'wax']
// Crockford's base32, which drops i, l, o and u -- so nothing in a password reads as something else when
// it is copied off the screen by hand, and nothing in one spells a word by accident.
const ALPHABET = '0123456789abcdefghjkmnpqrstvwxyz'
const PASSWORD_LEN = 16

/** A uniform index below `n`. Rejection sampling rather than `% n`, which is biased toward the low indices
 *  whenever `n` does not divide 256 -- a generator that quietly favours the first few words of its list has
 *  a smaller collision space than it looks like, and collisions here cost the owner a failed sign-in. */
function below(n: number): number {
  const limit = 256 - (256 % n)
  const byte = new Uint8Array(1)
  for (;;) {
    crypto.getRandomValues(byte)
    if (byte[0] < limit) return byte[0] % n
  }
}

const pick = <T,>(xs: readonly T[]): T => xs[below(xs.length)]

export interface SoulseekAccount { username: string; password: string }

/** A fresh name and password. ~4 million names, so a collision is unlikely rather than impossible; the step
 *  that uses this regenerates on a failed sign-in, which is what makes "unlikely" good enough. */
export function generateAccount(): SoulseekAccount {
  const digits = Array.from({ length: 4 }, () => below(10)).join('')
  const password = Array.from({ length: PASSWORD_LEN }, () => ALPHABET[below(ALPHABET.length)]).join('')
  return { username: `${pick(ADJECTIVES)}${pick(NOUNS)}${digits}`, password }
}
