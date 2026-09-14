# Flackey — a test build

You paste a link, it finds the track properly, tags it, and files it where Rekordbox will see it.
This is an early build sent to you to try, not a finished product. It is unsigned, so macOS will
refuse to open it until you tell it otherwise — the steps below are the whole of that. The same steps,
with the download button, are at [eyaldelarea.github.io/flackey](https://eyaldelarea.github.io/flackey/).

## Before you start

**An Apple Silicon Mac.** This build is arm64 only. On an Intel Mac it will not launch at all.

**Nothing else.** The audio tools Flackey uses (ffmpeg and chromaprint) are inside the app; their licences are in the bundle under `Contents/Frameworks/bin/licenses`.

## Installing it

1. Unzip `Flackey.zip`. **Don't open the app yet** — do step 2 first.
2. Run this in Terminal, which is what stops macOS blocking it. Drag the app onto the Terminal window
   after typing the first part and it will fill in the path for you:

   ```sh
   xattr -dr com.apple.quarantine ~/Downloads/Flackey.app
   ```

   That path is wherever the app actually is. If you moved it to Applications first, it is
   `/Applications/Flackey.app` instead — and moving it there may ask for your admin password, which is
   macOS, not this app. Leaving it in Downloads is fine.

3. Now open it normally.

Step 2 is not optional and there is no way around it from the GUI on a current macOS. The app is
signed only ad-hoc — there is no Apple Developer account behind this build, so macOS cannot check who
made it and refuses by default. The command removes the "downloaded from the internet" flag that
triggers that check. Run it only for software someone you trust handed you on purpose, which is the
situation you are in.

## First run

The app opens on a setup screen, because it starts with nothing configured.

**Telegram.** One of the two ways Flackey finds audio is a Telegram bot. A build from Flackey's own
releases already carries the keys Telegram needs to know which app is talking. A build someone made
from a checkout without them asks for an API id and hash on the Telegram step, and that screen says
where to get them. You sign in with your own Telegram account by scanning a QR code, and that is all.
You can skip it and use Soulseek only.

**Soulseek.** The other source. The setup screen walks through it and downloads what it needs.

You can skip either one. With neither, the app runs but has nowhere to fetch from.

## Where things go

| | |
|---|---|
| Your music | `~/Music/DJ Library` — one folder per artist, playlists in `Playlists/` |
| Settings and database | `~/Library/Application Support/Flackey` |
| The log | `~/Library/Application Support/Flackey/flackey.log` |

If something goes wrong, that log file is the useful thing to send back — it records what the app was
doing, and it is where any crash on startup ends up, since the app has no console to print to.

## Sharing on Soulseek

Soulseek is give and take: your DJ Library is shared read-only, and many users refuse to send to
anyone who shares nothing. For people to reach you, one port has to be open on your router. Flackey
asks the router to open it every time it starts and then checks from outside whether that worked.
Settings › Sharing shows the answer. If it says the port is closed, forward TCP 50300 to this Mac on
your router (the panel shows the addresses), or, if you are on a VPN, in the VPN's settings.
Downloading works either way; sharing back is what needs the port.

## Known rough edges

- Unsigned, hence the `xattr` step.
- Apple Silicon only.
- Closing the window quits the app.
