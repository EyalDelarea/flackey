"""The public half of the update signing key.

Committed rather than injected from a secret: it ships in every copy of the app either way, so
swapping it should take a visible commit. The private half is the FLACKEY_UPDATE_SIGNING_KEY
Actions secret; losing it only stops updates verifying until a new public key is committed.

`NEXT_PUBLIC_KEY_HEX` is the rotation slot. Empty today. To retire a key: put the next key's public
half here and ship a release signed with the current key; once that release is the oldest one people
still update from, sign with the next key and move it up to `PUBLIC_KEY_HEX`. A copy that never got
the release carrying the next key cannot verify anything the next key signs, and it does not fall back
to anything: a packaged copy must verify (`web/update.py` `must_verify`), so it refuses both the
seamless update and the installer and points the owner at the release page to download it by hand.
There is no way to revoke a key from here other than shipping a release that drops it.
"""

from __future__ import annotations

PUBLIC_KEY_HEX = "69eb3b6e92ae54944ff48e0b7c5d01de8d6e95a8201bcfb3c3eacb5defb4c7ba"
NEXT_PUBLIC_KEY_HEX = ""
