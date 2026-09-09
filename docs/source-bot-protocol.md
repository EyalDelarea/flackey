# @DeezerMusicBot interaction protocol (captured 2026-09-03)

Captured live through the owner's account with Telethon. Used as the basis for
`source/deezer_bot.py` and its test fixtures.

## 1. Search

Send plain text to the bot, e.g. `astral projection into the void`.

Within ~2 s the bot replies with ONE message:

- text: `<query>:`
- inline keyboard rows:
  - one row per result: button text `"<n>. <Artist> - <Title>"`,
    callback data `dz_track:<deezer_track_id>:send`
    (SoundCloud results use `sc_...`, VK results `vk_...`; we only use `dz_track`)
  - row of type toggles: `Tracks ✅` (`page:1`), `Albums ☑️` (`album_page:1`),
    `Artists ☑️` (`artist_page:1`)
  - row of source toggles: `Deezer ✅` (`page:1`), `SoundCloud ☑️` (`sc_page:1`),
    `VK ☑️` (`vk_page:1`). ✅ = enabled, ☑️ = disabled. The bot remembers the
    toggles per user. If Deezer shows ☑️, click it and wait for the edited
    message.
  - row: `Close` (`delete`)

The `deezer_track_id` in the callback data is the public Deezer id. The public
API `https://api.deezer.com/track/<id>` returns title, artist, album, duration,
release_date and **isrc** for it without any login.

## 2. Fetch

Click the result button. Within ~8 s the bot sends an audio message:

- text: `links / via`
- document: mime `audio/mpeg`, attributes:
  - audio: duration 442, title `Into the Void`, performer `Astral Projection`
  - filename `Astral Projection - Into the Void.mp3`
- size 17 874 690 bytes for a 7:22 track (i.e. 320 kbps)

Download with Telethon `client.download_media(message, file=dest)`. User
accounts are not subject to the 50 MB Bot API limit.

## 3. Timing and errors

- Search reply: ~2 s. Use a 30 s timeout.
- Audio after click: ~8 s observed. Use a 90 s timeout.
- No result (captured live 2026-09-04, query `zzqx nonexistent track 48213`):
  the bot's *first* reply already carries a keyboard:
  - text: `<query>:` (same format as a successful search, e.g.
    `zzqx nonexistent track 48213:`)
  - inline keyboard:
    - row: `No results` (`pass`)
    - row of type toggles: `Tracks ✅` (`page:1`), `Albums ☑️`
      (`album_page:1`), `Artists ☑️` (`artist_page:1`)
    - row of source toggles: `Deezer ✅` (`page:1`), `SoundCloud ☑️`
      (`sc_page:1`), `VK ☑️` (`vk_page:1`)
    - row: `Close` (`delete`)
  - No `dz_track:...:send` buttons are present, so `parse_result_menu`
    returns an empty `candidates` list and `DeezerBotSource.search` correctly
    raises `SourceNotFound("no Deezer results")` via the
    `if not menu.candidates` branch. The `NO_RESULT_GRACE_S` keyboard-less-reply
    path in the same function did not trigger during this capture.
