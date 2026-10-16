Responses for `tests/test_tag_lookup.py`, in the shape `api.deezer.com` (`/track/{id}`, `/album/{id}`) and
`api.discogs.com` (`/database/search`, `/releases/{id}`) return them, trimmed to the fields Flackey reads plus
a few it must ignore (Discogs `images`). The tests serve them through respx; nothing touches the network.

The label names, catalogue numbers and years are test values chosen to exercise the matching rules
(original vs remaster, a compilation edit, a remix in the title, "Pt." vs "Part"). Don't read them as
facts about these releases.
