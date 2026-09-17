# Security Policy

## Reporting a vulnerability

Please **do not** open a public issue for a security vulnerability. Instead,
use GitHub's private reporting: go to the **Security** tab of this repo →
**Report a vulnerability**. That opens a private advisory only you and the
maintainer can see.

Include what you'd include in any good bug report: what you found, how to
reproduce it, and the impact you'd expect.

## Supported versions

Flackey ships one rolling release. Only the latest published version gets
security fixes — there are no maintained older branches.

## Scope

Flackey runs locally on your own Mac (or your own Docker host) and talks to
Telegram, Deezer, Beatport, and (optionally) Soulseek using your own
credentials. Things worth reporting: anything that could leak your Telegram
session, Soulseek password, or library data to someone who shouldn't have it;
remote code execution via a crafted link, filename, or matched track's
metadata; or a packaged build's baked-in Telegram API keys being exposed
beyond what `docs/RELEASING.md`'s build process intends.
