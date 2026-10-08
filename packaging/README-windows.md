# Installing Flackey on Windows

Flackey for Windows needs 64-bit Windows 10 or 11. On an ARM PC (a Snapdragon
laptop, say) it runs through Windows' built-in x64 emulation.

## Installing it

1. Download `Flackey-Setup.exe` from the [releases page](https://github.com/EyalDelarea/flackey/releases).
2. Open it. Windows will probably show a blue **"Windows protected your PC"** box. Click **More info**,
   then **Run anyway**.
3. Click through the installer. It installs just for you, so it does not ask for an administrator password.
4. Flackey opens when the installer finishes. Afterwards it is in the Start menu.

**Why the warning?** The installer is not code-signed, and Windows SmartScreen warns about any
download it has not seen many times before. Only click Run anyway for a `Flackey-Setup.exe` you downloaded
from the releases page above. To check you have the real file, compare its checksum with the
`Flackey-Setup.exe.sha256` file next to it on the release:

```
certutil -hashfile Flackey-Setup.exe SHA256
```

## Where things go

| | |
|---|---|
| The app | `%LOCALAPPDATA%\Programs\Flackey` |
| Your music | `Music\DJ Library` in your user folder — one folder per artist by default (Settings › Library › Folder layout) |
| Settings and database | `%APPDATA%\Flackey` |
| The log | `%APPDATA%\Flackey\flackey.log` |

If something goes wrong, use **Report a bug** (in the sidebar, or Settings › Help): it opens an email to the
developer with a redacted zip of the log and system details. If the app will not start, send that log file.

## Updating and uninstalling

When a new version is out, **Update** in the app downloads the new `Flackey-Setup.exe`, checks its signature, and runs it. Click
through the installer again. Flackey closes while it installs, and the installer's last page starts it
again.

To uninstall, find Flackey under **Settings › Apps** (**Installed apps** on Windows 11, **Apps &
features** on Windows 10) and choose Uninstall. This removes the app only. Your
settings stay in `%APPDATA%\Flackey` and your music stays in `DJ Library`. Delete those folders yourself
if you want them gone too.

## Sharing on Soulseek

This works the same as on a Mac (see [README-for-friends.md](README-for-friends.md#sharing-on-soulseek)).
The first time Soulseek starts, Windows Firewall may ask whether to allow it. Allow it on private
networks, or other people cannot reach your shared folder.
