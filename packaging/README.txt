FLACKEY — BETA
==============

Flackey is an early beta for Apple Silicon Macs running macOS 12 or newer.
It has an ad-hoc signature, but it is not signed or notarized with an Apple
Developer account. macOS therefore blocks the downloaded app by default.

HOW TO OPEN FLACKEY
-------------------

1. Leave Flackey.app in this folder, or move it to Applications.

2. Open Terminal (Applications > Utilities > Terminal).

3. Type the following, including the space at the end:

       xattr -dr com.apple.quarantine 

4. Drag Flackey.app from Finder onto the Terminal window. Terminal will add
   the app's exact path. Press Return.

5. Open Flackey.app normally.

Only use the command above for this Flackey.app downloaded from the official
release page:

https://github.com/EyalDelarea/flackey/releases

The ad-hoc signature protects the integrity of the app bundle after it is
built. It cannot establish a trusted developer identity; that requires a paid
Apple Developer account and notarization.

More information: https://eyaldelarea.github.io/flackey/
