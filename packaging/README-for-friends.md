# Installing Flackey on a Mac

You paste a link, it finds the track properly, tags it, and files it where Rekordbox will see it.
The installer is not signed with an Apple Developer ID, so macOS will refuse to open it until you
tell it otherwise — the steps below are the whole of that. The same steps,
with the download button, are at [eyaldelarea.github.io/flackey](https://eyaldelarea.github.io/flackey/).
On Windows, read [README-windows.md](README-windows.md) instead.

## Before you start

**An Apple Silicon Mac on macOS 12 or newer.** Flackey is arm64 only. On an Intel Mac it will not launch at all.

**Nothing else.** The audio tools Flackey uses (ffmpeg and chromaprint) are inside the app; their licences are in the bundle under `Contents/Frameworks/bin/licenses`.

## Installing it

1. Download `Flackey.pkg` from the [releases page](https://github.com/EyalDelarea/flackey/releases) and open it.
2. Follow the macOS Installer steps. It puts `Flackey.app` in `/Applications`.
3. Open Flackey from Applications and connect Telegram, Soulseek, or both.

The installer is not signed or notarized with an Apple Developer account. If macOS says it cannot
check `Flackey.pkg` for malicious software, open **System Settings › Privacy & Security**, scroll down
and click **Open Anyway**. On macOS 14 and earlier you can instead control-click `Flackey.pkg`, choose
Open, and confirm. Do this only for a `Flackey.pkg` you downloaded from the releases page above. To check
you have the real file, compare its checksum with the `Flackey.pkg.sha256` file next to it on the release:

```
shasum -a 256 Flackey.pkg
```

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
| Your music | `~/Music/DJ Library` — one folder per artist by default (Settings › Library › Folder layout), playlists in `Playlists/` |
| Settings and database | `~/Library/Application Support/Flackey` |
| The log | `~/Library/Application Support/Flackey/flackey.log` |

If something goes wrong, use **Report a bug** (in the sidebar, or Settings › Help). It opens an email to
the developer with your description and a zip of the log and system details, with personal information
redacted. If the app will not start at all, send that log file instead — it is where any crash on
startup ends up, since the app has no console to print to.

## Updating

Flackey checks for new versions (Settings › Updates; the check can be turned off). When one is out,
**Update** downloads it, checks its signature, and swaps it in; Flackey closes and reopens on the new
version.

## Sharing on Soulseek

Soulseek is give and take: your DJ Library is shared read-only, and many users refuse to send to
anyone who shares nothing. For people to reach you, one port has to be open on your router. Flackey
asks the router to open it every time it starts and then checks from outside whether that worked.
Settings › Advanced › Sharing shows the answer (the row is there once Soulseek is connected). If it says the port is closed, forward TCP 50300 to this Mac on
your router (the panel shows the addresses), or, if you are on a VPN, in the VPN's settings.
Downloading works either way; sharing back is what needs the port.

## Known rough edges

- Not signed with an Apple Developer ID or notarized, so macOS asks for the Open Anyway step above.
- Apple Silicon only.
- Closing the window quits the app.
