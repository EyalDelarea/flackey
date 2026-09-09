# Soulseek as an audio source for flackey (research, 2026-09-06)

Question: can the Soulseek network (https://www.slsknet.org/) be added as an audio source next to
the Telegram/Deezer bot, how does it work, would it deliver better audio formats, and what are the
implications? Research only, no code. Three background agents each covered one slice; their full
reports are appended as Appendix A (protocol and tooling), B (audio quality and matching), and
C (implications and codebase fit). Every claim below points to the appendix section that cites
the primary source. Items the agents could not confirm from a primary source are marked
UNVERIFIED there and here.

## 1. Answer in short

- **Feasible.** Soulseek is a documented protocol with two live, headless-capable clients that a
  Python asyncio app can drive: `aioslsk` (in-process Python library) and `slskd` (separate daemon
  with a REST API). The existing `Source` protocol (`search`/`fetch`) fits it without change
  (A §4, C §B1).
- **Better formats, yes, but per track and unverified until downloaded.** FLAC 16-bit and 24-bit,
  WAV and AIFF circulate on the network; the current path delivers Deezer's 320 kbps MP3 tier,
  and Deezer's own ceiling is 16/44.1 FLAC. Availability is per uploader, and fake lossless files
  are a well-known problem that the existing spectral check already catches in the common case
  (B §1-3).
- **The real costs are not technical.** Soulseek's rules bar scripts that do not implement the
  full client feature set including upload, and peers penalise accounts that share nothing. Sharing
  means uploading the owner's copyrighted library to strangers and exposing the Mac's public IP to
  every peer by protocol design (C §A1-A4).
- **Three engineering gaps** if it goes ahead: parsing raw filenames into clean candidates, a
  worker state for "queued behind a peer for an unbounded time", and a fallback/ordering policy
  between the two sources (C §B2, §B4).

## 2. How Soulseek works (A §1-3)

- One central server handles login, peer discovery and search relay. Everything else, including
  search results, file lists and transfers, goes over direct TCP connections between peers. A
  distributed parent/child tree forwards searches beyond a client's direct connections.
- Logging in with a new username creates the account; there is no registration step. Idle
  usernames are recycled after 30 days. Only one login per username at a time; a second login
  kicks the first (C §A3).
- Each client advertises a listening port. Without a reachable port every peer interaction falls
  back to server-relayed "indirect" connections, which are slower and fail if the other side is
  also unreachable. `aioslsk` has optional UPnP; `slskd` has none and documents manual port
  forwarding.
- Search is fire-and-forget: the client sends a query string and then receives an arbitrary
  number of independent responses from peers over time. There is no completion signal. `slskd`'s
  default is to stop 15 s after the last response or at 100 peers / 10,000 files. No server-side
  rate limit on ordinary searches was found (UNVERIFIED). Wildcard and exclusion syntax is a
  Nicotine+ client-side convention, not a protocol feature.
- Every result carries the filename, size, extension and a per-file attribute list (bitrate,
  duration, VBR, sample rate, bit depth), plus per-peer fields: free upload slot, upload speed,
  queue length. MP3 results typically carry bitrate/duration/VBR; FLAC/WAV results carry
  duration/sample rate/bit depth and almost never a bitrate (B §1).
- Download flow: queue the upload with the peer, wait for the peer's transfer request, then a
  separate file connection streams the bytes. Queue position can be polled. The wait is unbounded
  and depends entirely on the peer staying online and its slot/priority configuration
  (UNVERIFIED as to typical duration; no source states a bound). Resume from a byte offset is
  supported. There is no folder-download message; a folder is browsed and each file requested.

## 3. Client options (A §4)

| Client | Shape | Fit for flackey |
|---|---|---|
| `aioslsk` | Python asyncio library, `pip install`, GPL-3.0, PyPI 1.6.3 (2026-01), pushed 2026-08 | Best fit: same process and event loop, no GTK, optional UPnP, can declare a share directory, ships a mock server for tests |
| `slskd` | C#/.NET daemon, REST API with API key, Docker image and native macOS binaries, AGPL-3.0, v0.26.0 (2026-07) | Solid fallback if an always-on external service over HTTP is preferred; needs a second process/container, no UPnP |
| Nicotine+ | GTK desktop client; "headless" mode is a stdin text console and still needs GTK | Not an API; do not embed |
| sockseek (ex slsk-batchdl) | CLI batch downloader with experimental daemon API | Does not share files at all; useful as prior art for matching heuristics only |
| museek+ | C++ daemon | Dead since 2022 |

## 4. Audio quality versus the current path (B §1-3)

- Current source: the Telegram bot returns Deezer's 320 kbps MP3 tier with a Deezer id and ISRC.
  Deezer's HiFi tier is 16-bit/44.1 kHz FLAC and that is Deezer's ceiling.
- On Soulseek, FLAC (16 and 24-bit), WAV, AIFF, APE and WavPack are all live format classes per
  the protocol's attribute table. The lossless share of the network is UNVERIFIED; no measured data
  exists. Soulseek's own 2015 dev note warns that 24/96 files are often vinyl rips, so higher sample
  rate does not imply a better master.
- For DJ use: Rekordbox software plays FLAC; CDJ-3000 and XDJ-1000MK2-class hardware play FLAC;
  CDJ-2000/NXS2 do not. A verified 320 MP3 is close to transparent; the lossless gain is mostly
  headroom for processing and newer booth hardware.
- Fake lossless (MP3 transcoded into a FLAC container) is a widely reported complaint
  (anecdotal, UNVERIFIED as to prevalence). `verify.py` already decodes to PCM and rejects a
  lossless container whose spectrum cuts off below 20 kHz, which is exactly this case, so no
  change is needed for the common fake. Boundary cases remain: a genuine 320 MP3 re-wrapped as
  FLAC can sit near the threshold, and "fake 24-bit" (16-bit padded to 24) is not detectable by
  a cutoff check. No surveyed tool solves either.

## 5. Matching a known track to a result (B §4)

Two production tools solve this problem and document their heuristics:

- **sockseek**: strip "feat." and bracketed text before searching, try progressively looser
  queries (artist+title, then title only), hard-filter on duration within 3 s, then rank
  survivors by a format preference list. Its README states that its defaults "favor recall over
  precision", so a wrong file can be accepted when the right one is absent.
- **soularr** (Lidarr to slskd bridge): fuzzy filename match ratio of 0.8, an ordered format list
  (`flac 24/192, flac 16/44.1, flac, mp3 320, mp3`), and filters on peer upload speed and queue
  length.

For flackey this maps to: duration pre-filter against the Beatport/Deezer duration before
spending a download slot, rank by format preference plus peer health, and keep the post-download
spectral verification as the final gate. Coverage of edits, bootlegs and unofficial remixes is
plausibly better on Soulseek because unlicensed material cannot appear on Deezer or Beatport at
all; this is reasoned opinion, not measured (B §5).

## 6. Implications (C §A1-A4)

- **Rules.** slsknet.org's rules page allows only files you are legally permitted to share, bars
  "automated clients (robot/bot) ... or scripts otherwise failing to implement the full range of
  Soulseek features", and states access "may be revoked at any time, for any reason". The protocol
  document's preamble asks implementers to use existing clients rather than write their own.
  Practically, a client that shares and behaves like a normal peer is tolerated; a zero-share
  download-only bot is exactly the profile the rule targets (opinion, C §A1).
- **Sharing norms.** No server-side leech ban exists (official FAQ). But `slskd` puts users
  sharing 0 files into a `leechers` group by default (priority 99, 1 slot, 100 KiB/s), and
  Nicotine+ ships a Leech Detector plugin that messages non-sharers; stricter community forks
  auto-ban. A zero-share account gets degraded service over time (A §5).
- **Legal.** Downloading and uploading are distinct exposures; sharing any folder turns the Mac
  into a distribution point for its contents. Soulseek offers no shield. This is general
  background, not legal advice (C §A2).
- **Privacy.** Peers receive the client's public IP by protocol design; username and share list
  are visible to anyone. A VPN breaks direct connections unless it forwards a port (C §A2, §A4).
- **Operational.** Needs a forwarded listening port for good results, an always-on process,
  upload bandwidth, a second credential store, and (with `slskd`) its own share index that must
  be rescanned after each new file. The central server is a single volunteer-run service with no
  published uptime data (UNVERIFIED) (C §A3).

## 7. Codebase fit (C §B1-B6)

Already in place, no change needed:

- `Source` protocol in `src/flackey/source/base.py` is source-agnostic.
- `verify.py`, `tag.py` (Vorbis comments and artwork for FLAC) and `library.py` handle FLAC, WAV
  and AIFF end to end.
- The `candidates` table already has a `source` column; per-candidate provenance works today.

Gaps:

1. **Candidate fields.** `deezer_id` and `isrc` are always `None` for Soulseek. Dedupe still
   works through the Beatport catalog ISRC. The fallback catalog id in `worker.py` hashes
   `source_ref`, so `source_ref` must become a stable username+filename pair rather than a
   re-clickable token.
2. **Filename parsing.** Soulseek returns raw paths, not artist/title. Turning them into a clean
   `Candidate` is new logic; `match.py` scoring is reusable unchanged once the inputs are clean.
3. **Queued downloads.** The worker's 90 s fetch timeout, 3 attempts and 30 to 120 s backoff
   were built for a bot that answers in seconds. A Soulseek fetch can legitimately sit queued for
   an unbounded time, and the worker processes one request at a time, so a slow peer blocks the
   whole queue. This needs a new request state or policy, not a config tweak.
4. **Source ordering.** `app.py` wires exactly one `Source` into `Worker`. A small composite
   `Source` that tries one then the other keeps the worker untouched.
5. **Docker.** With `aioslsk`, one extra exposed port on the existing image. With `slskd`, a new
   compose file, a second container and two more ports, one needing router forwarding. The repo
   has no compose file today.
6. **Upgrades.** Today a duplicate match keeps the existing file. Replacing an existing 320 MP3
   with a later FLAC needs a Lidarr-style "upgrade until cutoff" model that does not exist yet.

Rough sizes: `SoulseekSource` M-L, filename parsing M, queued-state handling M, composite source
S, config S, Docker S (aioslsk) or M (slskd), verify/tag/library none.

## 8. Decisions the owner has to make before any code

1. Share or not, and what: nothing, a curated legal subset, or the DJ library. This is the crux and
   has no technical answer.
2. Source order: Soulseek first for lossless with Deezer fallback, or Deezer first with Soulseek
   only on not-found.
3. How "found but queued" is handled: wait with a new state, cap and fall back, or surface to the
   review inbox.
4. Whether to backfill existing MP3s with FLACs later.
5. Client: `aioslsk` in-process (recommended by the tooling report) or `slskd` sidecar.
6. Dedicated Soulseek account (fresh accounts look like leechers) or reuse a personal one (only
   one login at a time).

## 9. Where the agents disagreed or could not confirm

- Appendix C marks the search-result duration field UNVERIFIED; Appendices A and B confirm it
  from the protocol document (attribute code 1). Treat it as confirmed but optional per peer.
- Server ports differ between clients' defaults (2416 vs 2271); the protocol does not mandate one.
- Some secondary pages (AlphaTheta support, LabelWorx, Servarr wiki) blocked direct fetch and were
  relayed via search snippets; Appendix B flags each.

---

# Appendix A: protocol and tooling (agent report)

## A. Soulseek network & headless client research (2026)

Scope: pure research for a personal Python 3.12 asyncio app (macOS + Docker) that currently
sources audio from a Telegram bot and is evaluating Soulseek as an additional source. Needs:
search by artist/title, inspect result metadata, download one file to a local directory.

All claims are sourced from primary documents (protocol doc, official slsknet.org pages, and the
actual source/README/config of each client project). Anything not directly verifiable from a
primary source is marked **UNVERIFIED**.

Primary sources used:
- SLSKPROTOCOL.md (Nicotine+, canonical reverse-engineered wire protocol) — https://raw.githubusercontent.com/nicotine-plus/nicotine-plus/master/doc/SLSKPROTOCOL.md (page states "Last updated on August 27, 2026")
- Official network Rules — https://www.slsknet.org/news/node/681
- Official FAQ — https://www.slsknet.org/news/faq-page
- slskd README — https://raw.githubusercontent.com/slskd/slskd/master/README.md
- slskd config docs — https://raw.githubusercontent.com/slskd/slskd/master/docs/config.md
- slskd source (Search/Transfers controllers, SearchRequest DTO) — github.com/slskd/slskd
- aioslsk README — https://raw.githubusercontent.com/JurgenR/aioslsk/main/README.rst
- aioslsk docs (Usage, Settings) — https://aioslsk.readthedocs.io/en/latest/
- Nicotine+ source (`pynicotine/__init__.py`, `headless/application.py`, `cli.py`, `search.py`, `plugins/leech_detector/*`, `doc/DEPENDENCIES.md`, `doc/DOWNLOADS.md`) — github.com/nicotine-plus/nicotine-plus
- sockseek (formerly slsk-batchdl / sldl) README and docs/api.md — github.com/fiso64/sockseek
- GitHub REST API (`/repos/<owner>/<repo>`, `/releases/latest`) for each project, queried live

---

### 1. Network architecture

- **Central server.** All clients connect via TCP to a central login/coordination server. Default
  hostname/port differs per client's own defaults (the protocol itself does not mandate a port):
  aioslsk defaults to `server.slsknet.org:2416` (`network.server.port` = `2416`), while slskd's
  documented default server port is `2271` (`vps.slsknet.org`, per `docs/config.md`). These are
  per-client configuration defaults, not a single canonical protocol constant — the mismatch is
  real and not explained by any source found. [SLSKPROTOCOL.md; aioslsk SETTINGS.html; slskd config.md]
- **Login.** Server message code 1 (`Login`). Documented "Data Order" for the send side is exactly:
  (1) *username* (string), (2) *password* (string), (3) *major version* (uint32), (4) *hash*
  (string) — "MD5 hex digest of concatenated username and password" — (5) *minor version*
  (uint32). On success the server replies with a MOTD greeting, the client's own IP address, a
  second hash field ("MD5 hex digest of the password string"), and an `is supporter` boolean; on
  failure it returns a rejection reason string (documented table includes `INVALIDUSERNAME`,
  `INVALIDPASS`, `INVALIDVERSION`, `TOOMANYUSERS`, and `SVRPRIVATE` — "Server does not accept
  registrations" — which by contrapositive implies ordinary public servers **do** accept
  registrations through the login flow). [SLSKPROTOCOL.md, "Login" / "Data Order" section,
  Login Rejection Reasons table]
- **Account creation on first login (username not previously registered).** Not stated as a single
  explicit FAQ sentence, but directly confirmed by Nicotine+'s own headless onboarding code:
  `pynicotine/headless/application.py`'s `on_setup()` logs "To create a new Soulseek account,
  fill in your desired username and password. If you already have an account, fill in your
  existing login details," and `on_invalid_password()` logs "User %s already exists, and the
  password you entered is invalid." — i.e. the very first successful login with a new username/
  password pair *is* account creation; there is no separate registration message in the protocol.
  [pynicotine/headless/application.py, github.com/nicotine-plus/nicotine-plus]
- **Username lifecycle.** Official FAQ states an idle/unused username is recycled (freed for
  re-registration) after 30 days of inactivity. [slsknet.org FAQ, "username" question]
- **Peer-to-peer (P) connections.** After the server helps two clients discover each other's
  IP/port (`GetPeerAddress`, Server Code 3, or `ConnectToPeer`, Server Code 18), clients open a
  direct TCP connection to each other for search-result delivery, file-list browsing, transfer
  negotiation, and user info — the central server is not in the data path for these messages.
  [SLSKPROTOCOL.md, "Peer Connection Message Order" and "ConnectToPeer" sections]
- **NAT traversal / firewalled peers.** The protocol defines both an unsolicited **direct**
  connection attempt (`PeerInit`, Peer Init Code 0x01) and, if that fails (peer is behind NAT/
  firewall), an **indirect** connection: the initiating peer asks the server to relay a
  `ConnectToPeer` message to the target, which then connects back and sends `PierceFireWall`
  (Peer Init Code 0x00) carrying the original request token. This is a documented fallback, not
  UPnP — UPnP is a separate, optional convenience so that a peer can *avoid* being firewalled at
  all. [SLSKPROTOCOL.md, "Establishing a Connection" / Peer Init Message Codes]
- **UPnP.** Official FAQ: "Both the original client and SoulseekQt use a protocol named UPnP to
  configure your router to recognize the listening port, but some routers (most notably Apple
  routers) don't support UPnP. Although not very common, it's also possible that your Internet
  service provider has your connection set up in such a way that you can't accept incoming TCP
  connections." [slsknet.org FAQ, port-forwarding question] Client support varies: aioslsk ships
  UPnP support (`network.upnp.enabled` = `false` by default, but implemented via the
  `async-upnp-client` dependency, with `search_timeout`/`lease_duration`/`check_interval`
  settings). [aioslsk README dependency list; aioslsk SETTINGS.html] slskd has **no UPnP
  implementation** — confirmed by a full-text grep of its 1462-line `docs/config.md` returning
  zero UPnP mentions; slskd instead documents manual port-forwarding: "As with any other Soulseek
  client, configuring the listen port and port forwarding ensures full connectivity..."
  [slskd docs/config.md, "Listen IP Address and Port" section]
- **Listening port requirement.** Every client must open and advertise a TCP listening port to the
  server (`SetWaitPort`, Server Code 2) so other peers can connect to it directly; a client with no
  reachable listening port falls back entirely to indirect (server-relayed) connections for every
  peer interaction, which is slower and not guaranteed to work if the *other* peer is also
  unreachable. Default listening ports are again client-specific: aioslsk `network.listening.port`
  = `61000` (clear) / `61001` (obfuscated); slskd default listening port = `50300`.
  [SLSKPROTOCOL.md, SetWaitPort section; aioslsk SETTINGS.html; slskd README/config.md]
- **Distributed network (search relay).** Beyond direct peer search (searching a specific user) and
  server-mediated global search, Soulseek propagates search queries through a tree of
  "distributed" connections: each client can become a parent to a number of children and forwards
  received `Distributed Search` messages down the tree, while reporting its `BranchLevel` and
  `BranchRoot` back up. The server suggests candidate parents via `PossibleParents` (Server Code
  102). This is how a single global search reaches far more peers than a client is directly
  connected to. [SLSKPROTOCOL.md, "Distributed Messages" section: Ping, Search, BranchLevel,
  BranchRoot, ChildDepth, EmbeddedMessage; PossibleParents]
- **Client-requirements policy (network rule, not protocol mechanic).** The official Rules page
  states: "Spammers, automated clients (robot/bot), combinations of such, or scripts otherwise
  failing to implement the full range of Soulseek® features are not allowed to connect to the
  Soulseek® service," and separately: "Alternative clients for platforms other than Microsoft®
  Windows® that implement the full range of features of the Soulseek® Network (including chat,
  search, wishlist, download, upload, and respect / recognition of privileges) are tolerated."
  [slsknet.org Rules, https://www.slsknet.org/news/node/681] The protocol doc echoes this from the
  tooling side: "Please use existing client implementations when possible instead of implementing
  your own... The risk of introducing bugs that have a negative effect on the network is also
  high." [SLSKPROTOCOL.md preamble] **This is a real design constraint for this project**, not a
  footnote — see Section 5 and the Bottom line.
- **Multiple clients per IP.** Rules page: "Users are allowed to connect up to a maximum 5 clients
  from the same IP." [slsknet.org Rules]
- **Reverse engineering.** The Rules page prohibits reverse-engineering the protocol/client
  further (already done and documented by the Nicotine+ project, which is the tolerated source
  used throughout this research). [slsknet.org Rules]

### 2. Search

- **Issuing a search.** A client sends `FileSearch` (Server Code 26) with a unique integer token
  and a plain query string; the server relays the query to the network at large (regular searches
  go through the distributed tree; a `WishlistSearch`, Server Code 103, is a separate message type
  for saved/recurring searches). [SLSKPROTOCOL.md, FileSearch and WishlistSearch sections]
- **Results arrive asynchronously, out-of-band, from many peers.** Each peer that has matching
  shared files opens (or reuses) a **peer** connection directly to the searcher and sends a
  `FileSearchResponse` (Peer Code 9) — a zlib-compressed payload — carrying that peer's username,
  the search token, and its list of matching files. Results are not routed back through the
  server and are not ordered or batched by any central authority; the caller must listen for an
  arbitrary number of independent inbound peer messages over time. [SLSKPROTOCOL.md,
  FileSearchResponse section]
- **How long to wait for results (result window).** The protocol itself has no fixed search
  "duration" or completion signal — a search token simply stays valid until the client stops
  listening for it. The most concrete, quantified answer found is slskd's own default behavior:
  its `SearchRequest` DTO documents `SearchTimeout = 15` (seconds) with the remark **"The timeout
  duration is from the time of the last response"** — i.e. an idle-timeout heuristic (15s of
  silence ends the search), not a fixed total window — plus `ResponseLimit = 100` (stop after 100
  peer responses) and `FileLimit = 10000` (stop after 10,000 total files seen).
  [slskd SearchRequest.cs DTO, github.com/slskd/slskd/blob/master/src/slskd/Search/API/DTO/SearchRequest.cs]
  aioslsk leaves this entirely to the caller: `searches.send.request_timeout` defaults to `0`
  ("keep indefinitely" — results keep arriving/accumulating until the app discards the search), and
  its own usage docs simply `await asyncio.sleep(N)` after issuing a search before reading results,
  i.e. the library imposes no default window at all. [aioslsk SETTINGS.html; aioslsk USAGE.html]
- **Per-file result fields.** `FileSearchResponse` (Peer Code 9) documents, per file: a code byte,
  the filename (full remote path), file size (as a 64-bit value), file extension, and an attribute
  list. Attribute types are enumerated (`FileAttribute` codes) covering bitrate, duration, VBR
  flag, sample rate, and bit depth — with a documented "File Attribute Combinations" table showing
  that different client families (e.g. legacy MP3-only clients vs. modern clients supporting
  FLAC/lossless) send different subsets of these attributes, so **not every result will carry
  bitrate/sample-rate/bit-depth data** — absence of an attribute is normal, not an error.
  [SLSKPROTOCOL.md, FileSearchResponse: File Attribute Types / File Attribute Combinations tables]
- **Per-peer result fields.** The same `FileSearchResponse` message carries peer-level fields
  alongside the file list: a free-upload-slot boolean (`slotfree`), the peer's current upload
  speed, and its queue length (how many uploads are already queued on that peer) — letting a
  caller triage which results are likely to start downloading quickly versus queue behind other
  users. [SLSKPROTOCOL.md, FileSearchResponse section] Related: `UserInfoResponse` (Peer Code 16)
  separately exposes a `slotsfree` field (a general "does this peer have any free upload slots"
  signal, independent of any specific search). [SLSKPROTOCOL.md, UserInfoResponse section]
- **"No free slots."** There is no dedicated protocol message literally named "no free slots."
  What exists concretely: `slotfree=false` inside a peer's `FileSearchResponse` (this peer has no
  currently free upload slot for you), the `slotsfree` field of `UserInfoResponse`, and — at
  download-request time — the documented Transfer Rejection Reasons table, whose literal
  "In Use" strings are: `Banned`, `Cancelled`, `Complete`, `File not shared.`, `File read error.`,
  `Pending shutdown.`, `Queued`, `Too many files`, `Too many megabytes` (verbatim from the table;
  `Banned`'s row notes "SoulseekQt uses 'File not shared.' instead"). `Queued` is the literal
  string a peer with no free upload slot sends back instead of starting the transfer immediately.
  There is no literal "no free slots" string — "no free upload slots" is inferred from
  `slotfree`/`slotsfree` plus a `Queued` rejection, not a single named protocol condition.
  [SLSKPROTOCOL.md, FileSearchResponse, UserInfoResponse, "Transfer Rejection Reasons" table]
- **Search rate-limiting / throttling — mixed picture, largely UNVERIFIED for outbound search.**
  Three distinct, non-overlapping mechanisms were found, and none of them is a documented
  server-side cap on ordinary outbound `FileSearch` frequency:
  1. `WishlistInterval` (Server Code 104) — but this applies **only** to wishlist searches, which
     the server paces at "almost always 12 minutes, or 2 minutes for privileged users."
     [SLSKPROTOCOL.md, WishlistInterval section] This does not govern regular `FileSearch` calls.
  2. slskd's REST API serializes *its own* search-creation calls with a `SemaphoreSlim(1,1)`
     (`SearchRequestLimiter`), returning HTTP 429 "Only one concurrent operation is permitted.
     Wait until the previous request completes" if two are POSTed concurrently. This is an
     application-level (single in-flight request) safeguard in slskd's own controller, not a
     network-level throttle. [slskd SearchesController.cs, `Post()` method]
  3. slskd's incoming-search throttling config (concurrency limits, circuit breaker, response-file
     limits) governs *other users' searches hitting your own shares*, i.e. protects your outbound
     upload bandwidth from being scanned too aggressively — it does not throttle searches *you*
     issue. [slskd docs/config.md, "Throttling" section]
  aioslsk's docs separately warn: "The server has an anti-DDOS mechanism, be careful when
  connecting and disconnecting too quickly or you will get banned" — but this is about connection
  churn (rapid connect/disconnect cycles), not search-issue frequency. [aioslsk USAGE.html]
  **UNVERIFIED: any hard server-side rate limit on how often a logged-in client may issue ordinary
  (non-wishlist) `FileSearch` requests.** No primary source found states a number or window for this.
- **Wildcard / exclusion search syntax — client-side convention, not a wire-protocol feature, and
  it IS transmitted over the wire.** `SLSKPROTOCOL.md` documents `FileSearch` as carrying a token
  plus a plain query string; it defines no special meaning for any punctuation inside that string.
  The `-word` (exclude) / `*erm` (partial-word) / `"exact phrase"` syntax is implemented client-side
  by Nicotine+ in `pynicotine/search.py`'s `_sanitize_search_term()`, which builds an
  `included_words` / `excluded_words` list used to **locally filter/rank the results Nicotine+
  itself receives**. Tracing the actual control flow: the `quotation_char` branch strips quotes and
  `continue`s (only the de-quoted inner text goes out); but the `partial_char` (`*erm`) and
  `excluded_char` (`-word`) branches do **not** `continue` — they fall through to a final
  `search_term_words_transmitted.append(word)` at the bottom of the loop, appending the *original,
  unmodified* token (prefix included). This means `*erm` and `-word` are sent **literally, prefix
  and all**, in the outgoing query string. There is no protocol-level guarantee any other client
  honors these characters as wildcard/exclusion markers — a receiving peer's server-side/local
  matching logic decides what a literal `-` or `*` character means to it, if anything.
  [pynicotine/search.py, `_sanitize_search_term()`, github.com/nicotine-plus/nicotine-plus; cross-
  checked against SLSKPROTOCOL.md FileSearch, which specifies no such syntax]

### 3. Download

- **Request flow (documented end-to-end in the protocol doc's own worked example).** The
  documented "Example Flow for Downloading a File" is, in order: (1) requester sends `QueueUpload`
  (Peer Code 43) to the file's owner with the exact remote filename; (2) the owner either accepts
  and later sends `TransferRequest` (Peer Code 40) back to the requester to initiate the actual
  transfer, or responds with `UploadDenied` (Peer Code 50) / `UploadFailed` (Peer Code 46) if it
  cannot service the request; (3) the requester replies with `TransferResponse` (Peer Code 41)
  accepting or rejecting the incoming transfer offer; (4) a new **file** connection (distinct from
  the peer connection) is opened for the byte stream itself, beginning with `FileTransferInit`
  and, for resumed downloads, a `FileOffset` message. [SLSKPROTOCOL.md, "Example Flow for
  Downloading a File" section, and the individual message definitions for QueueUpload,
  TransferRequest, TransferResponse, UploadDenied, UploadFailed]
- **Queue position.** `PlaceInQueueRequest` (Peer Code 51) / `PlaceInQueueResponse` (Peer Code 44)
  let the downloader ask, at any time, "where am I in this peer's upload queue" and get back an
  integer position. [SLSKPROTOCOL.md, PlaceInQueueRequest/Response sections] slskd exposes this
  directly over HTTP as `GET downloads/{username}/{id}/position`.
  [slskd TransfersController.cs, github.com/slskd/slskd/blob/master/src/slskd/Transfers/API/Controllers/TransfersController.cs]
- **How long a queued download may wait — UNVERIFIED / no fixed bound.** Nothing in the protocol
  doc or any client doc specifies a maximum queue wait time. It is structurally unbounded and
  depends entirely on: whether the uploading peer stays online, how many upload slots that peer's
  *own* client is configured with, that peer's queue-priority rules (e.g. slskd's `default` group
  gives priority 1 / 10 slots by default, vs. its `leechers` group giving priority 99 / 1 slot —
  see Section 5), and how many other people are ahead in that specific peer's queue. There is no
  protocol-level timeout or guarantee of eventual service. [Inference from SLSKPROTOCOL.md
  PlaceInQueueResponse + Transfer Rejection Reasons table; slskd docs/config.md Groups section;
  no source states a bound]
- **"No free slots" at download time.** Distinct from the search-time `slotfree` flag: at the
  moment a `TransferRequest` would otherwise begin, the peer can instead return one of the literal
  Transfer Rejection Reasons strings — `Banned`, `Cancelled`, `Complete`, `File not shared.`,
  `File read error.`, `Pending shutdown.`, `Queued`, `Too many files`, `Too many megabytes` —
  practically, when all of a peer's configured upload slots are in use, new requests get `Queued`
  rather than started immediately, and `PlaceInQueueResponse` reports where in line the request
  sits. [SLSKPROTOCOL.md, "Transfer Rejection Reasons" table; PlaceInQueueResponse]
- **Peer must be online — reasonable inference, not a literal doc quote.** No source states this
  sentence directly, but it follows necessarily from the connection model: `QueueUpload`/
  `TransferRequest`/the file connection all require a live TCP peer (or server-relayed indirect)
  connection to that specific user; if the user's client is not connected to the server at all,
  there is no address to connect to and no way to queue, negotiate, or transfer a file. This is an
  architectural inference from the connection-establishment sections of SLSKPROTOCOL.md, not a
  quoted statement. [Inference; see SLSKPROTOCOL.md "Establishing a Connection", ConnectToPeer]
- **Speed characteristics.** Determined entirely by the uploading peer's own bandwidth/throttle
  settings and how many concurrent uploads it is running — the protocol has no notion of a
  guaranteed or negotiated transfer rate; `FileSearchResponse`'s reported "upload speed" field is
  the peer's self-reported/observed rate, not a contract. [SLSKPROTOCOL.md, FileSearchResponse]
  slskd itself documents that other peers' clients often burst rather than sustain: its Timeouts
  section explicitly designs around clients that go "full speed for 5 seconds, send nothing for 25
  seconds," configuring `slsk-inactivity-timeout`/`slsk-transfer-timeout` to tolerate this bursty
  behavior rather than treat idle gaps as failure. [slskd docs/config.md, "Timeouts" section]
- **Partial / resume support.** The file connection's `FileOffset` message lets a downloader
  request a transfer starting at a given byte offset, enabling resume of a previously
  partially-downloaded file. The doc also flags a known interoperability bug: the original
  "Soulseek NS" client mishandles offsets beyond 2GB. This is whole-file-offset resume, not
  arbitrary byte-range/chunked parallel downloading — it resumes one sequential stream from where
  it left off, not multi-range fetching. [SLSKPROTOCOL.md, "File Connection Message Codes" /
  FileTransferInit / FileOffset section, with the >2GB Soulseek NS note]
- **Folder downloads.** The official FAQ explicitly explains SoulseekQt does **not** auto-download
  entire folders the way the old Soulseek NS client did: "When trying to download the files in a
  folder, the client has no knowledge of what files exist in the same folder other than the ones
  that were returned as search results... we've decided to take a what-you-see-is-what-you-get
  approach with SoulseekQt... To manually download everything in the folder, use the 'Browse
  Folder' context menu option," which first browses (lists) the peer's shared folder contents, then
  queues each listed file individually. So "download a folder" is really "browse peer's shared
  folder to enumerate files, then request each file" — there's no single "download this whole
  folder" protocol message. [slsknet.org FAQ, "download entire folder" question]

### 4. Headless client options (2026 maturity assessment)

All version/date/license facts below were pulled live via the GitHub REST API
(`/repos/<owner>/<repo>` and `/repos/<owner>/<repo>/releases/latest`) on 2026-09-06, plus each
project's own README/docs.

| Project | Language | Install | macOS | Docker | HTTP/Python API | License | Last activity |
|---|---|---|---|---|---|---|---|
| **slskd** | C# / .NET | Docker image, or native binary release, or build from source | Native `osx-arm64`/`osx-x64` zips in GitHub Releases (confirmed asset list for v0.26.0) | First-class (documented `docker run`/compose, ports 5030 web/5031 https/50300 Soulseek) | REST API with `X-API-Key` header auth; no official Python SDK, but trivially callable from any asyncio HTTP client (httpx/aiohttp) | AGPL-3.0-only | repo pushed 2026-09-05; latest tagged release **0.26.0** @ 2026-07-19 |
| **aioslsk** | Python (asyncio) | `pip install aioslsk` | Pure Python, no native deps beyond `async-upnp-client`/`mutagen`/`aiofiles` — runs anywhere Python 3.10-3.14 runs, including macOS | Trivial (plain Python process in any base image) | Native Python asyncio library (`SoulSeekClient`), no HTTP layer needed | GPL-3.0-or-later | repo pushed 2026-08-31; PyPI **1.6.3** @ 2026-01-18 |
| **Nicotine+** | Python + GTK (GTK4≥4.6.9 or GTK3≥3.24.24, PyGObject) | pip/pipx, OS packages, or GitHub release installers; Homebrew formula `nicotine-plus` on macOS | Apple Silicon installer (macOS 14+) and Intel installer (macOS 13+) via GitHub Releases, plus Homebrew | Has a `-n/--headless` CLI flag ("start the program in headless mode (no GUI)") and an `--isolated` flag explicitly described as useful "for e.g. Docker containers" — but `doc/DEPENDENCIES.md` still lists GTK4/GTK3 + PyGObject as **required** (no headless exemption found via grep), so a "headless" container still needs GTK installed | Headless mode is a **stdin/stdout text console** (`pynicotine/cli.py`'s `CLIInputProcessor` reads `/command args` lines from stdin), not a programmatic library or HTTP API — there is no import-and-call surface for a Python app to embed | GPL-3.0 | repo pushed 2026-09-06 ("today"); latest tagged release **3.3.10** @ 2025-03-10 (dev trunk is `3.4.0.dev1`, unreleased) |
| **sockseek** (formerly `slsk-batchdl`, then `sldl`) | C# / .NET | Native binary release, or Docker | Native `osx-arm64`/`osx-x64` tarballs in GitHub Releases (confirmed for v3.0.5) | Documented Docker usage | Has an experimental daemon mode with an HTTP+SignalR API (own docs call it explicitly "has not yet been tested much"), C#/.NET client-first (`Sockseek.Api.SockseekApiClient`), OpenAPI spec exists but **no first-class Python client** | AGPL-3.0 | repo pushed 2026-09-05 (renamed from `slsk-batchdl`, HTTP 301 confirmed); latest release **v3.0.5** @ 2026-08-10 |
| **museek+** (`eLvErDe/museek-plus`) | C++ | source build | — | — | Old daemon+client split architecture, but effectively abandoned | GPL | **repo last pushed 2022-03-08 — dead, excluded from recommendation** |

Additional notes:
- **sockseek explicitly does not implement sharing** — its own README states it "does not share
  yet, please also run Nicotine+/slskd [alongside it]" for that purpose.
  [sockseek README, github.com/fiso64/sockseek] Given Section 5's finding that not sharing carries
  real, documented consequences in this ecosystem (community norms, and slskd's/Nicotine+'s
  built-in throttling/shaming of non-sharers), running sockseek *alone* means operating exactly
  the kind of "search-and-grab, no upload" client the network's own Rules page targets when it
  bars "scripts otherwise failing to implement the full range of Soulseek® features."
  [slsknet.org Rules] It also has strong file-matching/quality-filtering logic (bitrate/length/
  format preference conditions) that is useful **prior art to read**, independent of whether the
  binary itself is adopted.
- **aioslsk ships a mock server for offline testing** (`tests/e2e/mock/server`, default port 2416)
  — useful for integration-testing this project's Soulseek code without touching the real network.
  [aioslsk README]
- **UNVERIFIED:** whether aioslsk implements any leech-detection/anti-leech throttling feature
  analogous to slskd's `leechers` group or Nicotine+'s bundled plugin — not found in its README,
  USAGE, or SETTINGS docs, and not specifically checked against its full source tree.

### Recommendation

**The deciding constraint is whether this app's Soulseek client needs to share files — and per
Section 5, effectively yes:** the official Rules page bars "scripts otherwise failing to
implement the full range of Soulseek® features," the protocol doc's own preamble discourages
bespoke clients, and the two actively-maintained ecosystems (slskd, Nicotine+) both ship
default tooling that materially penalizes or shames non-sharing accounts. That constraint rules
out sockseek as a standalone solution (it doesn't share) and reframes the choice as **slskd vs.
aioslsk**, both of which can share.

Given that, **aioslsk is the better fit for this specific app** (Python 3.12 asyncio, macOS +
Docker, no separate always-on service desired): it is a native asyncio library, requires no GTK
or second process, has built-in UPnP support (default off, but present via `async-upnp-client`) —
which slskd lacks entirely (zero UPnP mentions in its config docs, manual port-forwarding only) —
is actively pushed (2026-08-31) and versioned on PyPI (1.6.3, 2026-01-18), and its
`SharesSettings`/`DirectoryShareMode` let this app declare and serve a share directory to satisfy
the "full range of features" expectation with a few lines of config rather than a second daemon.

**Fall back to slskd** if this app would rather treat Soulseek as an external, always-on service
reached over plain HTTP (simplest possible integration surface, most mature/most active project
by release cadence, first-class Docker image, native macOS binaries) and is fine running and
maintaining a second process/container, accepting AGPL-3.0 and manual port-forwarding (no UPnP)
as trade-offs.

Do not use Nicotine+ programmatically — its headless mode is a human-facing text console, not an
API, and it still requires GTK/PyGObject installed even in "headless" mode. Do not use sockseek
alone if any sharing/network-citizenship requirement matters. Do not use museek+ (dead since 2022).

### 5. Sharing

- **No protocol/server-level requirement to share.** The official FAQ directly answers "I am tired
  of people that don't share files. Why isn't there an auto-ban feature?" with an explanation that
  boils down to: no automatic enforcement exists; non-sharing is a social problem, not a
  server-enforced one: "We realize that Soulseek has a reputation for mean, pedantic users that
  have a great list of 'dos and don'ts' in their userinfo." [slsknet.org FAQ, "auto-ban" question]
- **But it is a documented expectation, not a non-issue.** The Rules page requires alternative
  clients to implement "the full range of features of the Soulseek® Network (including chat,
  search, wishlist, download, **upload**, and respect / recognition of privileges)" to be
  tolerated at all, and separately bars "scripts... failing to implement the full range of
  Soulseek® features." [slsknet.org Rules] This is a rules-level (community-tolerance) requirement
  aimed squarely at bots that only search/download, even though it isn't mechanically enforced by
  the server.
- **slskd enforces a real, quantified penalty for non-sharers by default**, via its built-in
  `leechers` group: documented default thresholds trigger membership at `files: 1, directories: 1`
  shared (i.e. sharing zero files/folders), and the default `leechers` group config gives such
  users **upload priority 99** (worst), **1 upload slot**, and a **100 KiB/s** speed limit, plus
  queued/daily/weekly limits — versus the `default` group's priority 1 / 10 slots / 50,000 KiB/s,
  and the `privileged` group's priority 0 (best) / unlimited. This is throttling of *outgoing*
  service to non-sharers, configured out of the box. [slskd docs/config.md, "Groups / Built-In
  Groups" section]
- **Nicotine+ ships an official "Leech Detector" plugin** (bundled, not third-party) that watches
  completed uploads *you* make to others, checks the recipient's declared shared file/folder
  counts, and — if below threshold (defaults: `num_files: 1`, `num_folders: 1`) — sends them a
  private chat message (default text: "Please consider sharing more files if you would like to
  download from me again. Thanks :)") and records them in a `detected_leechers` list. It never
  bans; it nudges/shames. [pynicotine/plugins/leech_detector/__init__.py and PLUGININFO,
  github.com/nicotine-plus/nicotine-plus]
- **Net effect for a zero-share client:** no login rejection, no server ban — but (a) if other
  peers run slskd, this app will typically be placed in their `leechers` group by default and get
  1 slot / 100 KiB/s service instead of normal service; (b) if other peers run Nicotine+ with the
  default plugin enabled, this app's account will receive a scripted "please share more" message
  after downloads and be tracked as a known leecher locally on that peer's machine; (c)
  community/manual enforcement (userinfo call-outs, ignoring, occasional manual IP bans per the
  FAQ's harassment-handling answer) remains possible but is human-driven, not automatic.
  [Synthesis of slskd docs/config.md Groups section; pynicotine leech_detector source; slsknet.org
  FAQ auto-ban and harassment questions]
- **Practical implication for this app:** sharing a small, legitimate folder (even just what it
  downloads) is cheap insurance against being deprioritized by the two most common client-side
  enforcement mechanisms in the ecosystem it would be talking to, and aligns with the Rules page's
  "full range of features" tolerance clause. Both aioslsk (`SharesSettings`,
  `DirectoryShareMode.EVERYONE/FRIENDS/USERS`) and slskd (`Shares` directory config with
  aliasing/exclusion) make declaring at least one shared directory straightforward.
  [aioslsk USAGE.html; slskd docs/config.md "Shares" section]

### Bottom line

- Soulseek is a single central server for login/discovery/search-relay plus direct peer-to-peer
  connections for everything else (search results, file lists, transfers); a distributed
  parent/child tree extends search reach beyond a client's direct connections. [SLSKPROTOCOL.md]
- Logging in with a brand-new username/password **is** account creation — there is no separate
  registration step, confirmed by Nicotine+'s own onboarding code and consistent with the
  protocol's `SVRPRIVATE` rejection semantics. [pynicotine/headless/application.py; SLSKPROTOCOL.md]
- Search is asynchronous and multi-peer with no protocol-defined completion signal; the most
  concrete, quantified answer for "how long to wait" comes from slskd's own default (15s idle
  timeout since the last response, 100-peer/10,000-file caps), not from the wire protocol itself.
  [slskd SearchRequest.cs] No server-side rate limit on ordinary outbound search frequency was
  found anywhere — **UNVERIFIED**.
- Downloads flow through `QueueUpload` → `TransferRequest`/`TransferResponse` → a separate file
  connection, with queue position queryable but **no bound on how long a queued download may
  wait** — it depends entirely on the uploading peer staying online and its own slot/priority
  configuration. **UNVERIFIED / unbounded by design.** [SLSKPROTOCOL.md]
- The network's own Rules page and the protocol doc's preamble both explicitly discourage bare,
  feature-incomplete automated clients ("scripts otherwise failing to implement the full range of
  Soulseek® features are not allowed to connect"; "please use existing client implementations
  when possible instead of implementing your own") — this is a real design constraint, not a
  minor footnote: it argues for driving a full-featured client (or library) that also shares,
  rather than hand-rolling a bare search-and-download socket client. [slsknet.org Rules;
  SLSKPROTOCOL.md preamble]
- Sharing is not server-enforced (confirmed: official FAQ explicitly says there's no auto-ban
  feature), but two of the ecosystem's live, actively-maintained clients ship real default
  consequences for non-sharers: slskd throttles a `leechers` group to 1 slot/100 KiB/s by default,
  and Nicotine+ bundles a "Leech Detector" plugin that messages under-sharers after they download.
  Zero-share operation is technically possible but will be de-facto penalized by peers running the
  two most common tools. [slsknet.org FAQ; slskd docs/config.md; pynicotine leech_detector source]
- For this project (Python 3.12 asyncio, macOS + Docker, needs search/inspect/download-one-file,
  and per the constraint above should share at least something): **aioslsk** is the best fit —
  pure-Python asyncio library, no GTK/second process, built-in UPnP, actively developed (pushed
  2026-08-31, PyPI 1.6.3), with straightforward share-directory config to stay a "good citizen."
  **slskd** is the strongest fallback if a standalone always-on HTTP service (native macOS
  binaries + first-class Docker image, more mature/more active release cadence) is preferred over
  in-process embedding, accepting AGPL-3.0 and no UPnP (manual port-forwarding) as trade-offs.
  Nicotine+ headless mode is a text console, not a usable API, and still requires GTK/PyGObject
  even "headless." sockseek doesn't share on its own. museek+ is dead (last pushed 2022-03-08).

---

# Appendix B: audio quality and matching (agent report)

## B. Soulseek as an audio source for flackey — research findings

Scope: would adding Soulseek as a source get better audio than the current Deezer-via-Telegram-bot 320 kbps MP3 path, and how reliably could a known track (identified via yt-dlp metadata, matched on Beatport) be matched to a Soulseek search result. Research only — no code changes proposed here.

---

### 1. What formats exist on Soulseek in practice

Primary source: the Soulseek protocol as documented by the Nicotine+ project (the most actively maintained open-source client, whose maintainers reverse-engineered and now co-maintain the protocol spec with the official client authors) — `SLSKPROTOCOL.md`, master branch: https://github.com/nicotine-plus/nicotine-plus/blob/master/doc/SLSKPROTOCOL.md

- **File Attribute Types** (numeric codes carried in every search result), quoted verbatim from the doc's "File Attribute Types" table:
  - `0` = Bitrate (kbps)
  - `1` = Duration (seconds)
  - `2` = VBR (0 or 1)
  - `3` = Encoder — marked `OBSOLETE`
  - `4` = Sample Rate (Hz)
  - `5` = Bit Depth (bits)
- **Which attributes are actually populated, by format** — quoted from the "File Attribute Combinations" section:
  - Soulseek NS, SoulseekQt (≤2015-2-21), Nicotine+ (lossy formats), Museek+, SoulSeeX, slskd (lossy formats): `{0: bitrate, 1: duration, 2: VBR}`
  - SoulseekQt (2015-6-12+): `{0: bitrate, 1: duration}` for MP3/OGG/WMA/M4A, and `{1: duration, 4: sample rate, 5: bit depth}` for FLAC/WAV/APE (no bitrate field at all for lossless)
  - WV (WavPack, hybrid lossy/lossless): `{0: bitrate, 1: duration, 4: sample rate, 5: bit depth}`
  - Nicotine+ (lossless formats), slskd (lossless formats): `{1: duration, 4: sample rate, 5: bit depth}`
  - **Practical implication**: a search result for a FLAC almost never carries a bitrate field — sample rate + bit depth is what's available instead, and MP3 results normally lack sample rate/bit depth. Any matcher must branch its quality-preference logic on file extension, not assume one attribute set for all formats.
- **Per-result peer/session metrics** (not audio attributes, but returned alongside every result and usable for ranking), from the `FileSearchResponse` (Peer Code 9) message layout in the same doc:
  - `slotfree` (bool) — whether the peer has a free upload slot right now
  - `avgspeed` (uint32) — the peer's average upload speed
  - `queue length` (uint32) — how many downloads are already queued ahead of you on that peer
  - These are reported once per response (per peer), not per file, so they rank *peers/sources* for a given match, not the track's audio quality itself.
- **Official Soulseek dev commentary on metadata reliability** (a rare first-party source, from the official Soulseek news blog, dated 2015‑02‑12): https://www.slsknet.org/news/node/2945 — "Useful TagLib audio attributes." Key points quoted/paraphrased:
  - Bitrate display was deliberately *not* added for lossless formats ("was deemed unnecessary for uncompressed formats") — consistent with the protocol table above.
  - Sample rate/bit depth on a lossless file is a signal of *source* quality, not just container: "CD quality = 16-bit/44.1 kHz; vinyl rips often use higher values like 24/96 or 24/192" — i.e., a 24-bit/96kHz FLAC is not automatically "better," it may just be an upsampled vinyl rip or a genuinely higher-resolution vinyl transfer; sample rate alone doesn't prove real extra content.
  - The MP3 "VBR quality" value (0–99) was flagged as unreliable across encoders: "has nothing to do with the source input" and is only meaningful when comparing files from the same encoder — i.e., don't trust it as a cross-encoder quality signal.
- **Formats actually seen in practice**: FLAC (16-bit and 24-bit), 320 CBR MP3, V0/VBR MP3, WAV, AIFF, APE, and WavPack (WV) are all named as live format classes in the protocol's own attribute-combination table above — this is a primary-source confirmation that these formats circulate on the network today (the doc is actively maintained against the live protocol), not just anecdote.
- **Share of lossless vs. lossy on the network as a whole**: **UNVERIFIED**. No reputable current quantitative study was found. Academic P2P measurement papers on Soulseek (e.g., work referenced via https://www.researchgate.net/publication/228750854_Public_Domain_P2P_File-Sharing_Networks_Measurements_and_Modeling) are from the mid-2000s, predate FLAC's mainstream adoption on the network, and don't break results down by audio format. Community/blog sources (e.g., https://houdinimagazine.net/july/the-rundown-soulseek) describe a culture of vinyl rips, deleted Bandcamp tapes, and archival sharing that *skews toward* lossless/rare content, but this is anecdotal/opinion, not measured data — flagged as such below.

### 2. Compare with the current source (Deezer 320 MP3 via the Telegram bot)

- **Deezer's own tiers**, from official Deezer support docs: https://support.deezer.com/hc/en-gb/articles/115004588345-High-Fidelity-HiFi-on-Deezer
  - HiFi tier: "Audio is encoded with 16-Bit/44.1 kHz FLAC to deliver the very best listening quality" — i.e., real lossless (CD-equivalent) exists on Deezer's platform, but only in the HiFi tier, and it's the *ceiling* — no 24-bit/hi-res tier exists ("The highest audio quality available to HiFi listeners is 16-bit FLAC").
  - Standard tier: "High Quality (320 kb/s)" MP3 — this is what the Telegram bot is apparently sourcing from (a 320 kbps MP3, matching the bot's advertised delivery format), not the HiFi FLAC tier.
  - So Deezer itself already has a strictly better format (16/44.1 FLAC) than what the current pipeline receives; the bottleneck is the Telegram bot's own source tier/re-encode, not Deezer's catalog ceiling.
- **What 320 MP3 vs. FLAC/AIFF/WAV means for DJ hardware**, official/primary sources:
  - Rekordbox (software) supports FLAC, ALAC, WAV, AIFF, MP3, AAC — confirmed via AlphaTheta/Pioneer's own support-article titles and system-requirements pages surfaced under support.alphatheta.com and rekordbox.com (e.g. https://support.alphatheta.com/en-US/articles/20841184201881, https://rekordbox.com/en/support/system.php).
  - CDJ **hardware** support for FLAC/ALAC is newer and model-dependent: the CDJ-3000 is described as "the first CDJ to support FLAC and Apple Lossless natively," with WAV/AIFF/FLAC/ALAC supported up to 88.2/96 kHz, 16- or 24-bit (per spec aggregator https://boothready.app/players/cdj-3000, cross-checked against Pioneer's format-support article at https://support.pioneerdj.com/hc/en-us/articles/4408217704857-Which-file-formats-can-I-play, which 301-redirects to https://support.alphatheta.com/en-us/articles/4408217704857). Older CDJ-2000/CDJ-2000NXS2 units do **not** support FLAC/ALAC — only MP3, AAC, WAV, AIFF. The XDJ-1000MK2 supports FLAC/ALAC up to 48kHz/24-bit.
  - **Practical implication for this app**: a FLAC delivers no audible benefit on older CDJ-2000-class club hardware (it simply won't load, or the DJ would need to pre-convert to WAV/AIFF), but is directly usable on CDJ-3000/XDJ-class hardware and in Rekordbox software/laptop sets. Since this is a personal library feeding Rekordbox (per project memory: "Rekordbox analyzes" BPM/key), FLAC is a real, usable upgrade rather than a purely academic one — provided the booth hardware is FLAC-capable.
  - General DJ-press consensus (secondary, but converging across independent outlets — DJ TechTools https://djtechtools.com/2017/11/08/format-djs-buy-music-djs-guide-mp3-flac-wav/, Beatportal https://www.beatportal.com/articles/798615-what-is-the-best-audio-format-for-djs, and others): a genuine 320 kbps MP3 is very close to transparent on most systems, and the practical gap between MP3-320 and lossless mostly matters on very high-end club systems or for headroom/processing (EQ, loops, time-stretching) rather than raw perceived fidelity. One recurring point worth flagging (opinion, but repeated independently): "a fake WAV/FLAC sounds worse than a real 320 kbps MP3" — i.e., format alone is not quality; a verified-genuine 320 MP3 beats an unverified "lossless" file.

### 3. Fake/transcoded files on Soulseek

- **How common**: **UNVERIFIED / anecdotal only.** No formal prevalence study exists. Blog-level sources describe it as a widely-known, recurring complaint rather than a rare edge case:
  - "one of the loudest complaints is about fake lossless files and sketchy hi-fi claims" — https://houdinimagazine.net/july/the-rundown-soulseek (opinion/community reputation, not measured)
  - "Many files downloaded from Soulseek cannot be trusted, with supposedly lossless or 320kbps files often turning out to be upconverted low-bitrate ... rips" — https://vibesdj.io/dj-tools/audio-quality-checker (vendor blog for a DJ audio-quality-checker tool; treat as opinion/marketing, not data)
  - https://miseryconfusion.com/blog/2025/07/09/dont-get-fooled-by-fake-lossless-files-again/ — independent blog making the same claim; again anecdotal.
- **How the existing spectral-cutoff check would catch it**: the flackey verification module already implements exactly the right kind of check for this, and it is format-agnostic (I read `/Users/delarea/Desktop/code/flackey/src/flackey/verify.py` directly, not a web source, to confirm this):
  - `spectral_cutoff_hz()` decodes the middle 60 s of audio to PCM regardless of container/codec, computes power in 250 Hz bands from 8 kHz up, and finds the highest frequency where level drops ≥20 dB within 500 Hz (`CLIFF_DB = 20`, `CLIFF_SEARCH_FROM_HZ = 8_000`) — the signature of a lossy encoder's brick-wall lowpass, since "natural music rolls off gradually... and never makes such a step" (comment in the source).
  - `verify()` applies `MIN_MP3_CUTOFF = 18_000` Hz for MP3 and `MIN_LOSSLESS_CUTOFF = 20_000` Hz for FLAC/WAV/AIFF, and explicitly reports `"lossless container but cutoff {cutoff} Hz: lossy source"` when a FLAC/WAV/AIFF fails the lossless threshold — this is precisely the "FLAC transcoded from MP3 shows a 16–19 kHz cutoff" case the task asked about, and it is already handled today, before adding Soulseek as a source.
  - Because the check works on decoded audio rather than trusting self-reported container/bitrate/sample-rate metadata, it is inherently more robust than metadata-based filters (the kind Soulseek search results themselves expose, see §1) — which is important because those metadata fields (bitrate, sample rate) are exactly what a faker/re-encoder controls and can spoof.
- **What dedicated fake-lossless tools do, for comparison**:
  - `FakeFLac-Lossless-audio-checker` (https://github.com/Haki-22/FakeFLac-Lossless-audio-checker): compares the file's spectrogram against a spectrogram of a simulated lossy re-encode of the same file, rather than using one fixed kHz threshold; explicitly notes "there is no absolute way to do this without the original audio file" — i.e., no free/open tool claims certainty, only heuristics, which matches the flackey approach of picking a defensible fixed cutoff rather than claiming perfect detection.
  - `penthy` (https://github.com/gioypi/penthy): a CNN trained on 128×128 px spectrogram crops of the 16.2–22 kHz band, ~90% claimed accuracy distinguishing FLAC-from-MP3 vs. genuine lossless, but explicitly documents its blind spot: **"MP3 files with high bitrates (320kbps) may contain enough high frequencies to fool penthy,"** and it does not claim to detect upsampling or transcodes from non-MP3 sources. Project is marked discontinued. This is a useful data point: even ML approaches struggle exactly where flackey's fixed 18 kHz/20 kHz thresholds are also weakest (a genuine 320 kbps MP3 re-packaged as "FLAC" without further degradation can sit right at the boundary) — no tool, including flackey's, fully solves this class of fake.
- **Gap not covered by spectral-cutoff checks (flagging for completeness, not proposing a fix)**: "fake 24-bit"/fake hi-res files — a 16-bit source zero-padded or dithered into a 24-bit container — is a bit-depth fake, not a frequency-domain fake, and a cutoff check alone won't catch it. **UNVERIFIED**: I did not find a primary-source, well-established open-source technique for this specific check (some audiophile-forum discussion of inspecting the statistical distribution of the least-significant bits exists, but nothing rising to a citable, reputable standard was found).

### 4. Matching a known track to a Soulseek result

Two real-world tools automate exactly this "known track → Soulseek result" matching problem and document their heuristics publicly. Primary sources: their own READMEs, fetched directly.

**slsk-batchdl** (now renamed `sockseek`; formerly `slsk-batchdl`) — https://github.com/fiso64/slsk-batchdl / README at https://github.com/fiso64/sockseek

- **Query construction / string cleanup options** (from the README's Search Options and Tips sections):
  - `--remove-ft` — "Remove 'feat.' and everything after before searching"
  - `--remove-brackets` — "Remove square-bracketed text from track titles before search"
  - `--regex <regex>` — arbitrary regex stripping from title/artist/album (`T:`/`A:`/`L:` prefixes scope it), with a documented example for stripping YouTube-playlist noise: `--regex "[\[\(].*?[\]\)]|(?i:lyrics)|(?i:official)"` — directly analogous to stripping "(Original Mix)"/"(Extended Mix)"/"Official Video" from a yt-dlp-derived title.
  - `--extract-artist` — parse "Artist - Title" style strings.
  - `--artist-maybe-wrong` — "Performs an additional search without the artist name. Useful for sources like SoundCloud where the 'artist' could just be an uploader" — directly relevant since yt-dlp-derived "artist" metadata from YouTube can likewise be an uploader/channel name rather than the true credited artist.
  - `-d`/`--desperate` — "Tries harder to find the desired track by searching for the artist/album/title only, then filtering (slower search)" — i.e., broaden the query, then filter client-side rather than relying on the search engine to be smart.
  - General guidance: "It's always best to provide the least input necessary to uniquely identify an album or song" — i.e., prefer title-only or a minimal query over an over-specified one that returns zero results.
- **Filename/folder conventions** are implicit in the tool's design rather than explicitly documented (the README doesn't publish a canonical filename grammar), but its parsing options (`--extract-artist`, `--parse-title <template>` with `{artist} - {title}` placeholders) confirm the common convention is `Artist - Title` (optionally with track numbers/mix names), and its album-mode logic (folder-level track-count and quality checks, see below) confirms uploads are typically organized as one folder per release.
- **Ranking heuristics** — a documented two-tier system, quoted from the README:
  - Required conditions **filter** candidates; defaults: `format = mp3,flac,ogg,m4a,opus,wav,aac,alac` and `length-tol = 3` (seconds) — i.e., duration must match within 3 seconds when both source and file duration are known, otherwise the file is not even considered.
  - Preferred conditions **rank** (never filter) the survivors; defaults: `pref-format = mp3`, `pref-length-tol = 3`, `pref-min-bitrate = 200`, `pref-max-bitrate = 2500`, `pref-max-samplerate = 48000`, `pref-strict-title = true`, `pref-strict-album = true`.
  - Explicit statement of the tool's philosophy: **"The default settings favor recall over precision: when the correct file is available in results, it will almost always be ranked first. The tradeoff is that if it's absent and something else loosely passes the filters, that something else gets downloaded."** This is an important design warning for an automated pipeline: without a hard post-download verification step (which flackey already has via `verify.py` and Beatport tag-matching), a permissive ranking system can silently accept the wrong file.
  - Reliability caveat about metadata itself, quoted: "because the standard Soulseek client does not broadcast the bitrate, enabling `--strict-conditions` and setting a `--min-bitrate` will make [it] ignore all files shared by users with the standard client" — confirms §1's point that not all attributes are populated by all peers/clients, and being too strict on them silently shrinks the candidate pool.
  - Album/folder ranking: folders are ranked by "quality coverage" (e.g., "A folder with 9 FLAC files and 1 MP3 is preferred over a mostly-MP3 folder"), with `--strict-album-quality` to require every file in a folder to pass.
  - Peer-quality signals actually used: `--fails-to-downrank`/`--fails-to-ignore` (penalize/ban unreliable uploaders after N failures) and `--fast-search-min-up-speed` (minimum upload speed, default 1, used only in fast-search mode). **Notably, despite the protocol exposing `queue length` and `slotfree` per §1, this tool's public option list does not document any ranking or filtering on queue length or free-slot status** — a gap relative to what the protocol makes available for free.

**soularr** (https://github.com/mrusse/soularr) — bridges Lidarr (a music-library manager) to `slskd` (a Soulseek daemon), i.e., solves the same "known track from a metadata source → best Soulseek result" problem flackey would face, for whole albums. From its README/example config (fetched directly):

- `minimum_filename_match_ratio = 0.8` — **the closest documented analogue to "how do you match a known track to a Soulseek filename"**: a fuzzy string-similarity ratio (0–1) between the expected (Lidarr-sourced) track name and the candidate Soulseek filename; results below the ratio are rejected.
- `allowed_filetypes = flac 24/192,flac 16/44.1,flac,mp3 320,mp3` — an explicit, ordered format-and-quality preference list (most to least preferred), directly usable as a model for ranking Soulseek results against a Deezer-320/Beatport-verified baseline.
- `minimum_peer_upload_speed` (bits/sec, default 0) and `maximum_peer_queue` (default 50) — **this tool does explicitly filter on the peer metrics from §1** (upload speed and queue length), unlike slsk-batchdl's public options.
- `title_blacklist` / `search_blacklist` — case-insensitive word-blacklists to strip junk from titles and from the outgoing search query respectively (the closest documented analogue to stripping "(Original Mix)"/"feat."/promo tags before searching).
- `ignored_users` — a manual uploader blocklist, a cruder analogue to slsk-batchdl's automatic `--fails-to-downrank`.
- `accepted_formats` / `accepted_countries` / `use_most_common_tracknum` — release-level matching against MusicBrainz-sourced metadata (Lidarr's backing database), directly analogous to flackey matching against Beatport metadata (label/catalogue/tracklist) before accepting a file.

**Synthesis for flackey's use case**: the combination that emerges from both tools — (a) strip "feat."/parenthetical mix-and-official-video noise before querying, (b) try progressively looser queries (title+artist → title only) if the strict query returns nothing, (c) hard-filter candidates on duration match against the already-known Beatport/Deezer duration with a small tolerance (~3s, matching slsk-batchdl's default `length-tol`), (d) rank survivors by an explicit format/quality preference list (mirroring soularr's `allowed_filetypes` ordering) plus peer health (queue length, free slot, upload speed — both protocol fields are real and at least one production tool, soularr, uses them), and (e) never skip the existing post-download spectral verification, since both tools' own docs warn that ranking is a recall-favoring heuristic, not a guarantee.

### 5. Coverage for electronic/DJ music (Beatport-only releases, promos, edits, bootlegs)

- **Beatport's own exclusivity rule** (via LabelWorx, a Beatport-affiliated distributor's support docs — direct fetch of https://support.label-worx.com/hc/en-us/articles/10138410390162 was blocked by a 403, so this is relayed via a search-engine snippet of that page rather than a direct read; treat the exact wording as approximate): a release is *not* considered "exclusive" to Beatport if it's available for sale as a download anywhere else (including Qobuz, Bandcamp, etc.), **but pure streaming-only platforms are explicitly carved out as an exception** — meaning a Beatport-exclusive track can still legitimately appear as a *stream* on Deezer/Spotify without breaking exclusivity. This cuts against a naive assumption that "Beatport-exclusive" automatically means "absent from Deezer."
- Where Deezer coverage genuinely breaks down is a different category: **unofficial, unlicensed material** — bootlegs, uncleared mashups, unofficial edits, white-label promos, and vinyl-only pressings. These generally cannot appear on *any* licensed platform (neither Deezer nor Beatport) because there's no rights holder to clear the release — this is a structural/legal reason, not a curation choice, so it plausibly explains why such material is more likely to surface only on P2P/community sources.
- **Reputation of Soulseek among DJs/collectors** (opinion/reputation-based sources, clearly marked as such):
  - "Soulseek is presented as an exclusive underground network for serious music enthusiasts... attracts 'freaks, diggers, archivists, and ghosts'" and "users discover full discographies, unreleased demos, rare bootlegs, deleted Bandcamp tapes, cassette rips, and out-of-print... CDs" — https://houdinimagazine.net/july/the-rundown-soulseek (music webzine, opinion piece, not a data source).
  - The community's reciprocity culture ("You must share to gain access to good stuff. No one respects a leech") is also documented there — relevant because it implies rarer/DJ-oriented content is gated behind having something to share, not freely searchable by a bot with nothing to offer, which is an operational risk for an automated pipeline (see also the tools' own advice to run a "separate Soulseek account" and to actually share files, per slsk-batchdl's README note: "Sockseek does not share your music folders yet... please also share your collection with a regular client").
  - No Resident Advisor article specifically on Soulseek's DJ reputation was found; **UNVERIFIED** for that specific outlet.
- **Net read (opinion, mine, based on the above)**: plausible that Soulseek has *better* coverage than Deezer specifically for the "edits, remixes, bootlegs not on Beatport/Deezer" tail the user described, precisely because that content is unlicensed and can't legally sit on either commercial platform — but this is inference from the exclusivity/licensing mechanics above, not a measured comparison, and should be marked as reasoned opinion rather than fact.

### 6. Duplicates and library hygiene (existing Deezer MP3 + a later-found FLAC)

- **slsk-batchdl/sockseek**: default behavior is to **skip already-downloaded tracks** (`--no-skip-existing` is the flag to disable this, implying skip-on-exists is the default), matching existing files by `--skip-mode-output-dir name|tag|index` (default `index`). It does **not** automatically re-fetch a better version by default. It offers an explicit opt-in path to keep upgrading: `--skip-check-pref-cond` — quoted from its own wishlist example config — "keep searching for a flac version of ... [an album] even after an mp3 version has been downloaded, make it check the local version" via this flag. **UNVERIFIED**: the README does not document whether this then deletes/overwrites the old lower-quality file or simply adds a second copy alongside it — I could not find explicit wording either way.
- **soularr / Lidarr** (the album-manager side of that pipeline) uses a different, more mature model: Lidarr's quality-profile system has an explicit cutoff ("Upgrade Until") and an "Upgrades Allowed" toggle; per search-engine-relayed documentation of https://wiki.servarr.com/lidarr/faq (direct fetch returned only a JS shell, so this is relayed rather than directly quoted — flagging as lower-confidence sourcing, though this cutoff/upgrade mechanism is widely documented as standard behavior shared across the whole *arr family — Sonarr/Radarr/Lidarr): once a lower-quality file is below the profile's cutoff, the app keeps treating the item as "wanted" (soularr's own `search_source = cutoff_unmet` config option directly exposes this Lidarr concept) and **replaces** the file in place with a better one when found, rather than accumulating duplicates.
- **Takeaway for flackey**: the more disciplined pattern (Lidarr's) is upgrade-in-place with an explicit quality cutoff and a "still wanted until cutoff met" state, not simply keeping both copies; the simpler pattern (sockseek) is opt-in re-search with no documented automatic cleanup of the old file, which would need to be handled by whatever calls it.

---

### Bottom line

- **Yes, Soulseek can deliver strictly better audio than the current path in principle**: the protocol confirms FLAC (16- and 24-bit), WAV, AIFF, and WavPack circulate alongside MP3, and FLAC 24-bit exceeds even Deezer's own ceiling (Deezer HiFi tops out at 16-bit/44.1 kHz FLAC per Deezer's own support docs) — but this is a *ceiling*, not a guarantee for any given track; availability is per-track and per-uploader, unlike Deezer's uniform, licensed 320/HiFi catalog.
- **The quality upside only matters if the DJ's hardware and workflow can use it**: FLAC is fully supported by Rekordbox software but only by newer CDJ hardware (CDJ-3000/XDJ-1000MK2-class); older CDJ-2000-class club units cannot play FLAC at all — so the format upgrade is real for a Rekordbox/laptop workflow but conditional in a booth with older CDJs.
- **How reliably it can be matched automatically**: reasonably reliably, based on patterns two real production tools already use — hard-filter on duration match (±3s is the standard default in slsk-batchdl), fuzzy-match filename against the known title (soularr's `minimum_filename_match_ratio = 0.8` is the closest documented precedent), strip "feat."/parenthetical mix tags before querying, and fall back to looser queries (title-only, artist-optional) when the strict query returns nothing. Both tools' own documentation warns their default ranking "favors recall over precision" — i.e., matching alone is not proof of correctness.
- **The existing verification layer is already most of what's needed for the "fake lossless" risk**: flackey's `spectral_cutoff_hz()`/`verify()` in `verify.py` is format-agnostic (works on decoded PCM, not on self-reported container/bitrate metadata) and already flags a FLAC/WAV/AIFF whose real content cuts off below 20 kHz as "lossless container but ... lossy source" — this is precisely the fake-FLAC-from-MP3 case the task asked about, and no code change is required to catch the common case.
- **What the verification/matching layer should add, specifically for a Soulseek source** (research-derived, not implemented here):
  - A duration cross-check against the already-known Beatport/Deezer duration (small tolerance, ~3s) *before* spending a download slot, mirroring slsk-batchdl's `length-tol`.
  - Use of the protocol's peer-quality fields (`slotfree`, `avgspeed`, `queue length`) to prefer healthier sources — soularr does this (`minimum_peer_upload_speed`, `maximum_peer_queue`); slsk-batchdl's public options notably do not rank on queue length/free slot despite the protocol exposing them, so this would be a genuine improvement over the more popular tool's default behavior.
  - Explicit awareness that MP3-format results on Soulseek typically report bitrate/VBR while FLAC/WAV results typically report only sample-rate/bit-depth (near-never bitrate) — any quality-preference logic needs a format-conditional attribute schema, not one shared shape.
  - No built-in defense against a "fake 24-bit" (bit-depth-only) fake was found in any tool researched — this remains an open gap for both flackey's current checker and every public tool surveyed (**UNVERIFIED** whether any reputable open-source technique for it exists at all).
  - A duplicate/upgrade policy decision: flackey would need to decide explicitly between the two documented models found — "search again if a better format shows up later, delete/replace the old file" (Lidarr's cutoff/upgrade model) versus "leave the existing file alone unless manually told to re-search" (sockseek's default) — neither tool's docs describe a fully automatic, safe in-place replace-and-verify flow, so this would need custom logic either way.
- **Coverage for the DJ-specific tail (edits/remixes/bootlegs) is plausibly better on Soulseek** than on Deezer/Beatport, because unlicensed/uncleared material structurally cannot appear on either commercial platform — but this is reasoned opinion from Beatport's own exclusivity rules plus community reputation sources, not a measured comparison; mark as **opinion, moderately well-supported**.
- **Overall**: Soulseek is a plausible *second-tier fallback source* for tracks the Deezer-backed Telegram bot can't find (exactly the edits/remixes/bootlegs the user flagged), with real format upside (lossless, sometimes 24-bit) conditional on booth hardware — but it trades Deezer's uniform licensed-catalog reliability for a noisier, unlabeled, uncurated pool that needs the duration/filename matching heuristics in §4 on the way in and the existing spectral verification (plus the still-open fake-bit-depth gap) on the way out.

---

# Appendix C: implications and codebase fit (agent report)

## C. Soulseek as a flackey audio source — Part 3: Implications & Codebase Fit

Scope note: two sibling reports cover the wire protocol/tooling comparison (slskd vs
aioslsk) and audio-quality/matching mechanics. This report covers **A. implications**
(rules, legal, operational, privacy) and **B. codebase fit** (how it would slot into
flackey as it exists today). Everything under Part A is sourced from primary
pages (slsknet.org, Nicotine+'s own repo, slskd's own docs/repo) unless marked
`UNVERIFIED` or explicitly flagged as opinion/inference. Everything under Part B is
read directly from the files named in the task, with line numbers.

---

#### Part A — Implications

#### A1. Rules and norms

Soulseek's own rules page states the acceptable-use bar plainly:

> "You should only share and download files which you are legally allowed to or
> have otherwise received permission to share." — Rules, slsknet.org
> (https://www.slsknet.org/rules.html)

and on enforcement:

> "Access to the SoulSeek server is a privilege; not a right. It may be revoked at
> any time, for any reason. Server or chatroom abuse will result in your connection
> being closed and your ISP notified."
> — Rules, slsknet.org (https://www.slsknet.org/rules.html)

The rules also explicitly ban "automated clients (robot/bot), combinations of such,
or scripts otherwise failing to implement the full range of Soulseek features" from
connecting to the server (same page). This is directly relevant: flackey would
be an automated client. slskd and aioslsk are widely used non-official clients and
the community broadly tolerates headless/automated Soulseek clients (slskd has
thousands of GitHub stars and forum threads about running it 24/7), but the letter
of the rule as written targets bots/scripts that don't implement "the full range of
Soulseek features" (i.e., that don't share) — see the sharing discussion below.
**Opinion:** a client that shares real files and behaves like a normal peer (search,
browse, upload) is very unlikely to trip this rule in practice; a client with zero
shares that only ever downloads is the profile the rule (and the community) singles
out.

On the sharing/leeching norm specifically, Soulseek's own site has a page explaining
why it deliberately does **not** implement an automatic ban for non-sharers:

> "There are a good number of reasons why people do not share files. First and
> foremost being that they do not realize that many people are upset by
> 'non-sharers'." — "I am tired of people that don't share files. Why isn't there an
> auto-ban feature?", slsknet.org (https://www.slsknet.org/news/node/756)

So there is no server-side leech ban — but the *client-side* ecosystem fills that
gap. Nicotine+ (the most popular open-source Soulseek client) ships an official
"Leech Detector" plugin in its own repository:

> Default thresholds are 1 minimum shared file and 1 minimum shared folder; on an
> upload from a user below that threshold, the plugin sends a private message
> (default: "Please consider sharing more files if you would like to download from
> me again. Thanks :)"), can open a chat tab, and logs the user — it does **not**
> auto-ban by default, it messages/tracks.
> — `pynicotine/plugins/leech_detector/__init__.py`, nicotine-plus/nicotine-plus
> (https://github.com/nicotine-plus/nicotine-plus/blob/master/pynicotine/plugins/leech_detector/__init__.py)

Community-made *stricter* forks exist and are popular (auto-ban/auto-ignore
variants: "DELEECH", "Ban-SoulseekLeechers", "Anti-Leecher-for-Nicotine", etc. — all
third-party GitHub projects, not official). **Practical implication:** running with
zero shares will get flackey's Soulseek identity messaged, and on a meaningful
minority of peers (those running an anti-leech plugin) auto-banned/ignored, which
degrades download success over time as more peers refuse it.

The developer's own framing of enforcement (SoulseekQt) treats even manual bans as
"unsharing" rather than a punitive ban:

> "instead of banning a user, you simply unshared your files from that user" — "The
> Unsharing", slsknet.org (https://www.slsknet.org/news/node/156)

**The tension the task asked to flag:** sharing "only the DJ library" to satisfy
this norm means *uploading the owner's copyrighted, Beatport-verified 320
kbps/FLAC library to strangers on the internet* — i.e., becoming a distribution
point for commercial music, which is a categorically different legal exposure than
downloading. Sharing nothing avoids that exposure but accepts worse peer
reciprocity (messages, some bans) and — per the rule text above — sits closer to
what the acceptable-use rule was written against. There is no middle ground Soulseek
itself offers other than "share something" vs "share nothing"; ratio/quota systems
some torrent trackers use don't exist here.

#### A2. Legal

**Primary-source facts:**
- Soulseek's rules require only sharing/downloading what you're "legally allowed to
  or have otherwise received permission to share" (A1, rules.html).
- The rules also warn: "The use of this server is forbidden for individuals
  involved in any kind of illegal activities. Legal action may be taken against
  those who partake in such activities." (rules.html)
- Soulseek positions itself (per the same rules page, per WebSearch summary of that
  page) as intended to support independent/public-domain artists, not as sanctioned
  infringement — it does not claim any legal shield for downloading copyrighted
  commercial tracks.

**Not covered by any primary source found, general/opinion, NOT legal advice:**
- A Soulseek client (Nicotine+, slskd, aioslsk-based) is a normal P2P peer: by
  protocol design (confirmed by aioslsk's protocol docs, A3) it both downloads
  *and* serves uploads to other peers whenever something is shared. Downloading
  copyrighted commercial audio without authorization and *uploading/redistributing*
  it to other peers are legally distinct acts in most jurisdictions (distribution
  carries materially higher exposure than personal downloading in most established
  case law and enforcement patterns), and running a shared folder converts the
  Mac into an active distribution node for whatever is in it. This is general
  background knowledge, not sourced to a primary legal document, and is not legal
  advice.
- Any peer that connects to fetch a file — or that the server hands your address to
  for a search reply/browse — learns your public IP address, by protocol design
  (A4). A P2P network is not anonymous by default.
- VPN considerations only matter to the app insofar as they interact with slskd's
  need for a stable, forwardable listening port (A3/B5): running slskd behind a VPN
  typically breaks direct peer connections unless the VPN forwards a port back to
  the host, which is a nontrivial operational addition (slskd has an open feature
  request specifically for this, see A3). This report does not weigh in on whether
  a VPN is advisable — that's a legal/risk decision for the owner, informed by the
  above, not a technical recommendation this research makes.

#### A3. Operational

- **Listening port / NAT:** the Soulseek protocol needs a reachable listening port
  for optimal peer connectivity. Per aioslsk's protocol documentation, a client
  without a reachable listening port falls back to indirect (server-relayed)
  connection setup, which the docs describe as a fallback that most clients race
  against a direct attempt — i.e., direct (forwarded-port) connections are
  preferred and indirect ones are the degraded path
  (https://aioslsk.readthedocs.io/en/latest/SOULSEEK.html). slskd's own config docs
  describe listen-port configuration explicitly (default HTTP UI port 5030/5031,
  separate Soulseek listen port default 50300 per slskd's docker.md) and slskd has
  an open GitHub issue asking to support configuring the listen port at runtime
  specifically "to support VPN use cases"
  (https://github.com/slskd/slskd/issues/1432), which corroborates that
  port-forwarding/UPnP is an unsolved friction point for non-trivial network setups
  (VPN, CGNAT, etc.).
- **Always-on:** slskd is designed to run as a long-lived daemon (Docker or native
  binary) with a web UI and REST API, matching flackey's own "must stay running
  on the Mac" model (README.md:37-42 — `crate start` runs until Ctrl-C). Confirmed
  via GitHub Releases API that slskd v0.26.0 ships native macOS binaries for both
  architectures — `slskd-0.26.0-osx-arm64.zip` and `slskd-0.26.0-osx-x64.zip`
  (https://github.com/slskd/slskd/releases, fetched via
  `api.github.com/repos/slskd/slskd/releases/latest`) — so macOS is supported
  without Docker, in addition to slskd's own Docker image
  (https://hub.docker.com/r/slskd/slskd).
- **Share-scanning / disk usage:** slskd's config docs (docs/config.md) confirm it
  builds and maintains its own share index/cache for whatever directories are
  configured, "cache can be stored in memory or on disk, with configurable worker
  threads for scanning" — this is a second, independent index of (part of)
  flackey's library, separate from flackey's own SQLite `tracks` table
  (`src/flackey/store.py:42-49`). If the owner points slskd at the DJ library
  folder to share it, every new file flackey writes needs slskd's share cache
  to be refreshed (rescanned) before other peers can see/download it — an
  additional moving part flackey's worker doesn't currently manage.
- **Account/password storage:** slskd stores its own web-UI credentials
  (default `slskd`/`slskd`, meant to be changed) and the Soulseek network
  username/password in its own YAML/env config, separate from flackey's `.env`
  (`src/flackey/config.py:7-20`, which currently only holds Telegram
  credentials and paths). slskd's docs note it masks passwords when serializing
  config to JSON/YAML, but the plaintext still lives on disk somewhere
  (https://github.com/slskd/slskd/blob/master/docs/config.md).
- **Server outages:** no primary-source uptime/SLA data found for the central
  Soulseek server (it's a single, volunteer/community-run infrastructure, unlike
  Deezer's commercial CDN behind `@DeezerMusicBot`) — `UNVERIFIED` as to actual
  historical downtime, but structurally it is a smaller, less redundant
  operation than Deezer's, and flackey's own error-handling section already
  budgets for "source unavailable" (design spec §9, `docs/superpowers/specs/2026-09-03-flackey-design.md:329-331`).
- **Single-account/single-login limitation — confirmed, not UNVERIFIED.** aioslsk's
  protocol documentation states the server's behavior on a duplicate login
  explicitly: if a peer with that username is already connected, the server sends
  it a `Kicked (Code 41)` message and disconnects it
  (https://aioslsk.readthedocs.io/en/latest/SOULSEEK.html). So only one client can
  be logged in under a given Soulseek username at a time — if the owner already
  runs Nicotine+/another Soulseek client under the same account elsewhere, starting
  slskd (or aioslsk) under that same username would silently kick the other
  session, and vice versa. Using a second, dedicated Soulseek account for
  flackey avoids this but then has zero reputation/history on the network,
  which interacts with A1 (a fresh, unshared account is exactly the leech profile
  peers are suspicious of).
- **Running Telegram + Soulseek from the same machine:** no protocol conflict —
  they're independent network stacks. The practical interaction is inside
  flackey's own process model: `app.py` currently wires one `Source`
  (`DeezerBotSource`) into one `Worker` (`src/flackey/app.py:59,67`); adding
  Soulseek means either a second source object tried in sequence inside the worker,
  or a second `Worker`-like pipeline, both of which are today a single-`asyncio`-
  loop, single-process design (see B4).

#### A4. Privacy/security

- **Visible to all peers by design:** the Soulseek protocol's own connection-setup
  flow (per aioslsk's documented protocol) requires the server to hand out a peer's
  IP/port to whoever is trying to connect (`GetPeerAddress`,
  https://aioslsk.readthedocs.io/en/latest/SOULSEEK.html) — this is inherent to how
  search results are turned into direct file transfers, not a bug or an edge case.
  Username, and (once shared) the file list under it, are visible to anyone who
  searches or browses. This is corroborated by general secondary sources (VPN
  advisory blogs) but the IP-exchange mechanism itself is documented at the
  protocol level by aioslsk, which is the more authoritative source of the two.
- **slskd web UI / API exposure:** slskd's own config docs are explicit that its
  REST API key should not be used without HTTPS ("using API key authentication
  without HTTPS is **NOT RECOMMENDED**", docs/config.md) and that default web UI
  creds (`slskd`/`slskd`) must be changed. This matters directly for flackey:
  flackey's own `Settings.web_host` defaults to loopback specifically *because*
  "the JSON API is unauthenticated and must never bind 0.0.0.0 outside a container"
  (`src/flackey/config.py:17-19`); slskd's API is authenticated but the same
  discipline (don't expose it outside loopback/the container network without TLS)
  would need to be carried over if slskd is added as a sidecar, since its port
  (5030/5031) would be a second HTTP surface next to flackey's own 8765.
- **Credential handling:** the Soulseek account password lives in slskd's config,
  a second secret store outside flackey's existing `.env`
  (`src/flackey/config.py:1-20`), which currently holds only Telegram secrets
  under `pydantic-settings`. Whether the owner wants a second `.env`-style file, a
  section merged into the existing one, or Docker secrets is an open decision (see
  B6).

---

#### Part B — Codebase fit

#### B1. Current `Source` protocol, and which `Candidate`/`Query` fields Soulseek could and couldn't fill

The `Source` protocol is a two-method `Protocol` (structural typing, not
inheritance) at `src/flackey/source/base.py:25-30`:

```python
class Source(Protocol):
    name: str
    async def search(self, query: Query) -> list[Candidate]: ...
    async def fetch(self, cand: Candidate, dest_dir: Path) -> Path: ...
```

plus three exception types (`SourceNotFound`, `SourceTimeout`,
`SourceUnauthorized`) a source is expected to raise (`base.py:9-22`). This is
already source-agnostic by design — the spec explicitly calls this out: "Direct
Deezer subscription client (the source interface is designed for it)" is listed as
deferred-but-anticipated (`docs/superpowers/specs/2026-09-03-flackey-design.md:41`),
and the module table says `source` and `catalog` "are the two modules expected to
break due to external changes, so they contain no business logic" (same file,
line 176-178). A Soulseek source fits this shape mechanically without protocol
changes.

`Query` (`src/flackey/models.py:57-71`) has `raw`, `artist`, `title`,
`version`, `duration_s`, and a `search_text()` helper. A Soulseek search would use
`search_text()` unchanged (it already falls back to `self.raw` when
artist/title aren't known, `models.py:71`), exactly like `DeezerBotSource.search`
does today (`source/deezer_bot.py:95`).

`Candidate` (`models.py:74-88`) fields, one by one, for a Soulseek result:

| Field | Deezer bot fills it with | Soulseek fit |
|---|---|---|
| `source` | `"deezer_bot"` (`deezer_bot.py:51`) | trivial: `"soulseek"` |
| `source_ref` | the button's callback data, used later to re-click it (`deezer_bot.py:51,126,136`) | a Soulseek search result has no persistent server-side handle to "click" later — `source_ref` would have to be something like `f"{username}\x00{filename}"` (the pair slskd/aioslsk actually need to start a download), a real change in what `source_ref` *means* for this source (see below) |
| `artist` / `title` | parsed from the bot's button label `"<n>. <Artist> - <Title>"`, then overwritten by the real Deezer API metadata in `_enrich` (`deezer_bot.py:47-52, 79-90`) | Soulseek gives only a **raw filename and folder path** (e.g. `Astral Projection - Into The Void (Original Mix).flac` inside some arbitrary shared folder) — there is no structured artist/title from the network itself; flackey would have to parse it, which is a genuinely new piece of logic (closest existing analog is `identify.parse_text`/`parse_version`, used today only on the *inbound request* text, not on a source's results) |
| `mix_name` | Deezer's `title_version` (`deezer_bot.py:89`) | same problem as title: has to be regex-parsed out of a filename, not handed to you |
| `duration_s` | Deezer API (`deezer_bot.py:84`) | Soulseek search responses do include a duration field in the protocol (per general Soulseek protocol knowledge — `UNVERIFIED` against this task's own source list, since duration reporting depends on the peer's client correctly filling it) |
| `deezer_id` | the Deezer numeric id (`deezer_bot.py:52`) | **no equivalent** — Soulseek has no per-file catalog id; this field would simply stay `None` for Soulseek candidates |
| `isrc` | Deezer API (`deezer_bot.py:84`) | **no equivalent** — Soulseek never exposes ISRC; always `None` |
| `rank`, `score`, `catalog_track_id`, `id`, `request_id` | bookkeeping, source-independent | unaffected either way |

**The two fields the task specifically flagged, `deezer_id` and `isrc`, would both
be permanently `None` for Soulseek candidates.** That matters in three concrete
places already read from the code:

1. **Duplicate detection** (`library.py:59-67`, `find_duplicate`): tries ISRC
   first (`catalog.isrc if catalog else None or cand.isrc`), and only falls back to
   normalized artist/title/mix/duration matching when ISRC is absent. Since
   `catalog` still comes from Beatport (unaffected by which audio source fetched
   the file) and Beatport supplies its own ISRC when the track exists there, dedupe
   would generally still work *through the Beatport catalog match*, not through the
   candidate. It only degrades to the weaker duration-based path in the same
   situation it already can today: no Beatport entry found — see
   `_fallback_catalog` below.
2. **The `_fallback_catalog` synthesized-catalog path**
   (`worker.py:42-47`): when nothing on Beatport matches, the worker fabricates a
   `CatalogTrack` from the candidate itself:
   ```python
   fallback_id = -(cand.deezer_id or zlib.crc32(cand.source_ref.encode()) or 1)
   ```
   (`worker.py:44`) — this already tolerates `deezer_id is None` by falling back to
   `crc32(source_ref)`. So a Soulseek candidate (with `deezer_id=None`) would flow
   through this exact same line correctly *as long as `source_ref` is a stable,
   unique string* — which is exactly why `source_ref`'s meaning matters (row
   above): it needs to be something durable enough to hash, not a one-shot
   ephemeral token.
3. **Re-matching Beatport for the actual chosen version**
   (`_catalog_for`, `worker.py:129-139`, using `candidate_query`/`same_version`
   from `match.py:41-60`) relies on `cand.artist`/`cand.title`/`mix_name` being
   *reasonably clean strings*, since `candidate_query` builds a fresh `Query` from
   them (`match.py:53-56`). If Soulseek's filename-parsed artist/title is noisy,
   this re-match (and the original `score_candidate` in B2) degrades accordingly —
   this is really a B2/B3 matching-quality question wearing a B1 hat.

#### B2. `decide`/`match.py` — would it work against filename-derived candidates?

`decide()` (`match.py:105-135`) and `score_candidate()` (`match.py:82-102`) operate
purely on the `Candidate` and `Query` dataclasses — nothing in the scoring math is
Deezer-specific, so mechanically it would run unmodified against Soulseek
candidates. The real risk is input quality, not the algorithm:

- `score_candidate` compares `cand.artist`/`_candidate_title(cand)` (which itself
  calls `identify.parse_version` on `cand.title`, `match.py:48-50`) against the
  Beatport catalog artist/title using `rapidfuzz.fuzz.token_set_ratio`
  (`match.py:93-94`), weighted 25/25 of the 100-point score. Deezer's bot hands back
  clean, pre-split artist/title strings (`deezer_bot.py:47-52`, then Deezer-API-
  verified in `_enrich`), so this comparison is already working against
  high-quality strings today. A Soulseek `Candidate` built from a raw filename
  (which may carry catalog numbers, bracketed uploader tags, "VA -", track
  numbers, file-format hints, etc.) would need its own cleanup step, functionally
  equivalent to what `identify.py` already does for inbound YouTube titles/free
  text — i.e., a **Soulseek-specific candidate-parsing function is new code**, not
  something `match.py` provides.
- `_version_points` (`match.py:63-67`) and `candidate_version`
  (`match.py:41-45`) depend on `mix_name` or a parseable version suffix in
  `title` — same dependency on clean parsing.
- `_duration_points` (`match.py:70-79`) is format-agnostic and would work as-is
  *if* Soulseek exposes duration for the result (see B1 table, marked
  `UNVERIFIED` here since duration reliability across arbitrary peers' shared
  files/clients wasn't independently confirmed in this pass).
- The ISRC override path (`score_candidate`, `match.py:99`:
  `isrc = bool(catalog and catalog.isrc and cand.isrc and catalog.isrc == cand.isrc)`)
  simply never fires for Soulseek candidates (`cand.isrc` is always `None`), so
  those candidates can never get an automatic 100% ISRC-override match — they're
  capped at whatever the fuzzy-matching signals sum to, same ceiling every
  Deezer-bot Candidate without an ISRC already has today (Deezer only supplies
  ISRC when its own metadata call succeeds, `deezer_bot.py:79-90`, so this isn't a
  new code path, just one that would be hit more often).

**Conclusion for B2:** `match.py`'s scoring logic is reusable unmodified; the gap
is entirely upstream of it — turning a Soulseek filename into a plausible
`Candidate(artist=..., title=..., mix_name=...)` is new, non-trivial parsing work
that doesn't exist anywhere in the current codebase (this is presumably also core
subject matter for the sibling "audio-quality/matching" report).

#### B3. Verify layer and tag/library handling of FLAC

`verify.py` already treats `flac`, `wav`, `aiff` as `LOSSLESS`
(`verify.py:15`) with a 20 kHz cutoff floor (`MIN_LOSSLESS_CUTOFF = 20_000`,
`verify.py:14`), applied uniformly regardless of source
(`verify()`, `verify.py:115-131`, has no source-awareness at all — it only reads
the file `probe()` returns via `ffprobe`, `verify.py:48-60`). This is
**source-independent by construction**: a FLAC from Soulseek and a hypothetical
FLAC from anywhere else would go through the identical `probe → bitrate check
(skipped for lossless) → spectral_cutoff_hz → verdict` pipeline
(`verify.py:115-131`). Whether the 20 kHz-cutoff rule is *sufficient* to catch
"lossless container around a lossy source" — the well-known Soulseek failure mode
of transcoded/upsampled fake FLACs — is exactly the kind of question the spectral
cutoff check was designed for (design spec §6:264-283 explicitly calls this out:
"Lossless files must show content above 20 kHz or be flagged as suspicious (a
lossless container around a lossy source) and rejected"). Nothing in `verify.py`
needs to change to *apply* that rule to Soulseek FLACs; whether the specific 20 kHz
threshold and single-cliff heuristic (`spectral_cutoff_hz`, `verify.py:88-103`) is
robust enough against the wider variety of fake-FLAC encodes actually found in the
wild on Soulseek (vs. the presumably more uniform fakes seen from a Deezer-sourced
MP3 pipeline) is an audio-quality question outside this report's scope — likely
covered by the sibling report.

`tag.py` already has a full FLAC write path, separate from the ID3 path:
`_write_flac()` (`tag.py:90-107`) writes Vorbis comments (`TITLE`, `ARTIST`,
`ALBUM`, `ALBUMARTIST`, `GENRE`, `LABEL`/`ORGANIZATION`, `CATALOGNUMBER`, `DATE`,
`ISRC`, `BPM`, `INITIALKEY`, `MIXNAME`, `COMMENT`) via `mutagen.flac.FLAC`, and
embeds artwork as a `mutagen.flac.Picture` (`tag.py:101-106`), matching the ID3
`APIC` embedding used for MP3/WAV/AIFF (`tag.py:82-83`). `write_tags()`
(`tag.py:113-122`) already dispatches on file extension (`ID3_EXTS` vs `.flac`),
and `read_tags()` (`tag.py:137-167`) has a matching FLAC read branch. **No changes
needed in `tag.py`** to tag a Soulseek-sourced FLAC — this path is already
complete and exercised (comment: `# Vorbis comments for FLAC` is explicit in the
design spec, §7:287).

`library.py`'s `final_path()` (`library.py:23-27`) builds the destination purely
from `catalog.artist`/`catalog.display_title` and the passed-in `ext` string
(`f"{sanitize(catalog.artist)} - {sanitize(catalog.display_title)}.{ext.lstrip('.')}"`),
with no format allowlist of its own — whatever extension `verify()`/the source
handed back is used verbatim. So `library.py` needs **no change** for FLAC/WAV
files from a new source; it already works generically today (WAV/AIFF already flow
through it for the existing Deezer-bot source per `EXT_BY_MIME`,
`source/deezer_bot.py:21`, even though Deezer itself is MP3-only in practice).

#### B4. Where a second source slots in

- **Config** (`config.py:7-42`): `Settings` is a flat `pydantic-settings`
  `BaseSettings` reading one `.env`. Adding Soulseek needs new fields — network
  username/password (or a path to slskd's own config/API key + base URL if slskd
  is run as a sidecar service rather than in-process), a `soulseek_enabled` flag,
  and (if slskd) something like `SLSKD_URL`/`SLSKD_API_KEY`. This is additive and
  fits the existing `BaseSettings` pattern with no restructuring — same
  `.env`-driven approach used for Telegram creds today.
- **`app.py` wiring** (`app.py:59-67`): currently one `DeezerBotSource` object is
  built and passed straight into `Worker(...)` as its single `source` argument
  (`app.py:59, 67`; `Worker.__init__`, `worker.py:51-59`, takes exactly one
  `source: Source`). Adding a second source means either (a) a small
  composite/chain `Source` implementation that itself satisfies the `Source`
  protocol and internally tries Soulseek then Deezer (or vice versa) — the least
  invasive option, since `Worker` itself never needs to know two sources exist —
  or (b) changing `Worker` to accept a list/ordered sequence of sources and
  encoding the "try X then Y" policy inside `worker.py` (`_process`,
  `worker.py:141-218`) instead. Given `worker.py` already has fairly involved
  per-attempt state handling (`_retry_or_fail`, chosen-candidate persistence,
  parked/awaiting-review flow), a composite `Source` wrapper is the smaller,
  more surgical change and keeps `worker.py` source-agnostic exactly as the design
  spec's module table intends (`docs/superpowers/specs/2026-09-03-flackey-design.md:165-166`:
  "Abstract `Source` interface... v1 implementation `DeezerBotSource`").
- **Store/DB, "does the requests table record the source?"**: no. Looking at the
  actual schema (`store.py:23-64`), `requests` has no `source` column at all
  (`store.py:23-31`); `candidates` **already has** `source TEXT NOT NULL`
  (`store.py:33-35`, confirmed matching `Candidate.source`, `models.py:76`) —
  because a single request can already collect candidates from multiple logical
  sources conceptually (the Deezer bot's own menu even distinguishes Deezer vs.
  SoundCloud vs. VK internally, `docs/source-bot-protocol.md:15-16`, though today
  flackey only ever keeps `dz_track:` ones, `source/deezer_bot.py:16`). So
  **per-candidate source tracking already exists**; a Soulseek candidate would
  just write `source="soulseek"` into that existing column, no migration needed
  there. What's *not* tracked anywhere is which source a **filed track**
  ultimately came through — `tracks` (`store.py:42-49`) has no `source` column,
  only `catalog_track_id`/`request_id`, so "how many of my library files came from
  Soulseek vs. Deezer" would need to be derived by joining `tracks.request_id` ->
  `requests.chosen_candidate_id` -> `candidates.source`, or a new `tracks.source`
  column would need to be added if that's a stat the owner wants directly (Stats
  UI section, design spec §3.5:122-123, currently has no source breakdown).
- **Retry/timeout semantics**: the worker's current timeout model is tuned around
  Deezer-bot's fast, synchronous behavior — `DeezerBotSource` uses a 30 s search
  timeout and 90 s fetch timeout (`source/deezer_bot.py:71,
  20/44-45 in docs/source-bot-protocol.md`), and `worker._retry_or_fail`
  (`worker.py:117-127`) retries up to `MAX_ATTEMPTS = 3` with a short
  `RETRY_BACKOFF_S = (30, 120)` (`worker.py:25-26`) — designed for "the bot is
  briefly unresponsive," not for "the file is real but queued behind other
  peers' uploads for hours," which is normal Soulseek behavior (a peer's own
  upload slot/queue can make a download sit for minutes to hours before it even
  starts transferring — `UNVERIFIED` in this pass against a primary timing source,
  but this is well-established Soulseek behavior referenced throughout its own
  community documentation). As written, a Soulseek `fetch()` that legitimately
  takes 20 minutes queued behind a slow peer would either need a much longer
  `fetch_timeout` than Deezer's 90 s, or the worker's retry/backoff model would
  need a genuinely different state (something like "queued at source, check back
  later" rather than "failed, retry from scratch in 30-120 s") since blindly
  retrying a slow-queued Soulseek transfer every couple of minutes would likely
  just restart the queue position with a different (or same) peer rather than
  waiting it out. This is a real design gap, not just a config-value tweak:
  `RequestState` (`models.py:15-27`) has no state that maps to "waiting in a
  remote peer's upload queue," only `FETCHING` (immediate) and the terminal/error
  states.
- **`worker.run_forever`'s single-request-at-a-time loop** (`worker.py:70-78`,
  `self.store.next_queued()` -> `self.process(req.id)`, one at a time, no
  concurrency) compounds the above: if a Soulseek fetch can legitimately sit
  queued for a long time, processing requests strictly serially means every other
  queued request waits behind it too, unlike Deezer's bot which answers in
  seconds. This is worth flagging even though it's arguably part of "effort" (B6)
  rather than a hard blocker.

#### B5. Docker

The existing `Dockerfile` (repo root) builds a single Python 3.12-slim image with
`ffmpeg`, running `crate start` as the sole process, exposing only `8765`
(`Dockerfile:1-16`). There is **no `docker-compose.yml`/compose file in the repo
today** — only the single Dockerfile plus manual `docker run` instructions in
`README.md:93-105`.

- **If slskd is chosen** (separate always-on REST service): it needs to run as its
  own container (or native macOS process) alongside flackey's — the current
  single-Dockerfile setup has no multi-service orchestration at all, so this is a
  net-new piece of infrastructure: either a `docker-compose.yml` (currently
  absent) defining both `flackey` and `slskd` services sharing a Docker
  network (so flackey's HTTP client can reach `http://slskd:5030`), or running
  slskd natively on the Mac outside Docker (its own macOS binaries exist, see A3)
  while flackey stays containerized or not. Either way, new ports need
  exposing/mapping for slskd specifically: its web UI (5030/5031) and its
  Soulseek listening port (default 50300, must be forwarded on the router for good
  connectivity per A3) — none of which the current `EXPOSE 8765` /
  `-p 8765:8765` setup (`Dockerfile:15`, `README.md:97`) touches.
- **If aioslsk is chosen** (in-process Python library, not a separate service):
  it adds a dependency to `pyproject.toml` (alongside `telethon`, `aiogram`, etc.,
  `pyproject.toml:6-19`) and runs inside the same asyncio loop as everything else
  — no Docker/compose changes needed beyond exposing the Soulseek listening port
  from the existing single container (`EXPOSE 8765` would need a second
  `EXPOSE <port>` and a second `-p` mapping in the `docker run` command,
  `Dockerfile:15`, `README.md:97`), and forwarding that port on the router exactly
  as slskd would need. This avoids the multi-container/orchestration question
  entirely and is the smaller Docker change of the two options — a meaningful
  point in aioslsk's favor architecturally, independent of whatever the sibling
  protocol-comparison report concludes about API completeness/maturity.

#### B6. Effort estimate and owner decisions

**Rough T-shirt sizes** (assuming the sibling reports' protocol/matching work is
available, this is scoped to the codebase-integration slice only):

| Piece | Size | Why |
|---|---|---|
| New `SoulseekSource` implementing `Source` (via slskd REST client or aioslsk) | M–L | Straightforward protocol shape, but fetch semantics (queueing, see B4) are genuinely different from the synchronous Deezer-bot model |
| Filename → `Candidate(artist, title, mix_name)` parsing | M | New logic, no existing analog to lift from; quality directly drives match.py scoring (B2) |
| Composite/chain `Source` (try Soulseek then Deezer, or vice versa) | S | Small wrapper satisfying the same `Source` protocol; `Worker` needs no changes |
| Config additions (`config.py`, `.env`) | S | Additive fields on existing `BaseSettings` |
| New `RequestState`/worker handling for "queued at a remote peer" | M | Needs a real design decision (new state? longer timeout? both?), not just a config tweak — see B4 |
| `tracks.source` column + migration (only if source-level stats are wanted) | S | Optional; not needed for correctness, only for the Stats UI breakdown |
| Docker/compose for slskd sidecar | M | Net-new compose file, network wiring, two extra ports | 
| Docker/compose for aioslsk (in-process) | S | One more exposed port on the existing image |
| `verify.py`/`tag.py`/`library.py` changes | None | Already source-agnostic and already handle FLAC end to end (B3) |

**Decisions the owner needs to make before implementation, not this research:**

1. **Share or not**, and if so, share only the DJ library, a curated subset, or
   something else — this is the crux of the A1 tension (community norms vs.
   uploading copyrighted material to strangers) and has no technical answer.
2. **Source order/policy**: lossless-first (try Soulseek for FLAC, fall back to
   Deezer-bot MP3 320 on miss) vs. fallback-only (Deezer first, Soulseek only when
   Deezer's bot returns not-found) vs. owner-choice-per-request. This determines
   whether the composite source in B4 is "prefer higher quality" or "prefer
   speed/reliability."
3. **How to handle "found but queued"**: accept long waits with a new
   worker/request state, cap wait time and fall back to Deezer, or surface it to
   the owner as a review-inbox-style notification ("queued behind a slow peer,
   check back") — a genuine product decision, not just an engineering one.
4. **Upgrade existing MP3s to FLAC** if Soulseek later finds a lossless version of
   something already in the library filed as MP3 320 — not something the current
   `find_duplicate`/dedupe model (`library.py:59-67`) supports; today a duplicate
   match short-circuits and keeps the existing file (`worker.py:193-196`,
   `_mark_duplicate`), it never replaces it.
5. **Which client** (slskd vs. aioslsk) — primarily the sibling report's call, but
   it has a real Docker/ops consequence documented in B5 (sidecar service +
   compose file vs. one more exposed port on the existing image) that this report
   surfaces as a codebase-fit input to that decision.
6. **Separate Soulseek account or reuse an existing personal one** — interacts
   directly with A3's single-login-per-username finding and A1's "fresh account
   looks like a leecher" problem.

---

#### Bottom line

- Soulseek's own rules require sharing/downloading only what you're legally
  entitled to, explicitly ban bot/script clients that don't implement full
  Soulseek behavior (i.e., that don't share), and offer no server-side leech ban —
  but the client ecosystem (Nicotine+'s own Leech Detector plugin, plus stricter
  community forks) fills that gap with messages and, on some peers, real bans for
  zero-share accounts.
- Sharing "just the DJ library" to satisfy that norm means uploading the owner's
  verified, tagged, copyrighted commercial audio to strangers — a materially
  different legal posture than downloading, and one Soulseek's site doesn't
  shield in any way; this is the owner's call to make, not a technical default.
- Soulseek exposes your IP to peers by protocol design (confirmed via aioslsk's
  documented `GetPeerAddress` flow) and only allows one login per username at a
  time (confirmed: duplicate logins get `Kicked (Code 41)`) — both are hard
  protocol facts, not client quirks, and both interact with whether the owner
  reuses a personal account or wants a VPN.
- The `Source` protocol (`search`/`fetch` on `Query`/`Candidate`) is already
  source-agnostic by design and needs no changes to accept a Soulseek
  implementation; `verify.py`, `tag.py`, and `library.py` already fully support
  lossless FLAC (Vorbis comments, embedded artwork, generic path building) end to
  end with zero source-awareness.
- The real gaps are: (1) `deezer_id`/`isrc` will always be `None` for Soulseek
  candidates, which is tolerated by existing fallback code
  (`worker.py:44`'s `crc32(source_ref)` path) but pushes `source_ref` to mean
  something new (a stable username+filename pair, not a re-clickable token); (2)
  turning a raw Soulseek filename into a clean `Candidate(artist, title,
  mix_name)` is genuinely new parsing logic with no existing analog; (3) Soulseek
  downloads can legitimately queue for a long time, which the current
  30/90-second-timeout, 3-attempt, 30-120s-backoff retry model
  (`worker.py:25-26,117-127`) was not designed for and would need a real new
  `RequestState`/policy, not a config tweak.
- Docker impact differs sharply by client choice: an in-process library
  (aioslsk-style) only needs one more exposed port on the existing single
  container; a separate always-on REST service (slskd-style) needs a net-new
  multi-container setup (the repo currently has no `docker-compose.yml` at all)
  plus two more exposed ports, one of which (the Soulseek listening port) also
  needs router-level port forwarding for good connectivity.
- Before any implementation, the owner needs to decide: share or not (and how
  much); lossless-first vs. fallback-only source ordering; how "found but queued"
  requests are surfaced/waited on; whether to backfill existing MP3s with
  Soulseek FLACs later (not supported by today's dedupe-keeps-existing-file
  logic); which client to run; and whether to reuse an existing personal
  Soulseek account or create a dedicated one for flackey.
