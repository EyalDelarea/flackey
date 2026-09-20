"""The public half of the update signing key.

Committed rather than injected from a secret: it ships in every copy of the app either way, so
swapping it should take a visible commit. The private half is the FLACKEY_UPDATE_SIGNING_KEY
Actions secret; losing it only stops updates verifying until a new public key is committed.
"""

from __future__ import annotations

PUBLIC_KEY_HEX = "69eb3b6e92ae54944ff48e0b7c5d01de8d6e95a8201bcfb3c3eacb5defb4c7ba"
