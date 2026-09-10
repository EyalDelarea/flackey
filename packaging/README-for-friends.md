# Flackey — a test build

You paste a link, it finds the track properly, tags it, and files it where Rekordbox will see it.
This is an early build sent to you to try, not a finished product. It is unsigned, so macOS will
refuse to open it until you tell it otherwise — the steps below are the whole of that.

## Before you start

**An Apple Silicon Mac.** This build is arm64 only. On an Intel Mac it will not launch at all.

**Two helper programs.** Flackey shells out to ffmpeg and chromaprint to inspect and convert audio.
If you have [Homebrew](https://brew.sh):

```sh
brew install ffmpeg chromaprint
```

Flackey looks in `/opt/homebrew/bin` and `/usr/local/bin` by name, so it finds them even though apps
launched from Finder do not normally see your shell's PATH. Without them the app still opens and you can
look around; anything that needs them stops with "ffmpeg is not installed" rather than a stack trace, the
setup screen shows which are missing, and the log names them on startup.

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

**Telegram.** One of the two ways Flackey finds audio is a Telegram bot, and talking to Telegram
needs an API id and hash. This build does not carry any, so you will need your own: sign in at
[my.telegram.org](https://my.telegram.org) → **API development tools**, make an app (any name), and
paste the `api_id` and `api_hash` into the setup screen. They identify the software, not you.

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

## Known rough edges

- Unsigned, hence the `xattr` step.
- Apple Silicon only.
- ffmpeg and chromaprint are not bundled; you install them yourself, as above.
- Closing the window quits the app.
