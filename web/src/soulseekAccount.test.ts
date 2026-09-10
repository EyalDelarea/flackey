import { generateAccount } from './soulseekAccount'

it('deals a name Soulseek will accept and a password that can be copied by hand', () => {
  // Soulseek's own limits on a nick: not empty, at most 30 characters, printable ASCII, no leading or
  // trailing spaces. Two words and four digits is comfortably inside all four.
  const { username, password } = generateAccount()
  expect(username).toMatch(/^[a-z]{6,20}\d{4}$/)
  expect(username.length).toBeLessThanOrEqual(30)
  // Crockford base32: no i, l, o or u, so nothing in it reads as something else off the screen.
  expect(password).toMatch(/^[0-9abcdefghjkmnpqrstvwxyz]{16}$/)
})

it('does not deal the same account twice', () => {
  // Not a strength claim -- 200 draws prove nothing about 4 million names. It catches the failure that
  // would matter: a generator wired to a constant seed, or one that forgot to draw at all.
  const names = new Set(Array.from({ length: 200 }, () => generateAccount().username))
  expect(names.size).toBeGreaterThan(190)
})

it('spreads across its word lists rather than favouring the first few', () => {
  // `% n` on a random byte is biased toward low indices whenever n does not divide 256, and the whole
  // point of the name space is that it is wide. 400 draws from 22 adjectives should not miss most of them.
  const firsts = new Set(Array.from({ length: 400 }, () => generateAccount().username.slice(0, 3)))
  expect(firsts.size).toBeGreaterThan(15)
})
