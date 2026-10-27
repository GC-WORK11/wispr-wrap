# Privacy

Sign-Flow runs on your phone and your laptop, over your own network. It has no account and no server of its own.

## Camera

Camera video never leaves the phone. Hand tracking runs in the phone browser, and only hand landmark points are sent to your laptop.

## Share training samples (beta)

This setting is **off by default**. It is in Settings.

**If you turn it on**, each take you record in Teach is also saved on your phone, in a queue that holds at most 16 takes (the oldest is dropped first). Each saved take contains only:

- the name you gave the sign
- up to 64 frames of hand landmark points (x, y, z for each of 42 points)
- the time offset of each frame within the take, in milliseconds
- a random id created on your phone when you turned the setting on

**It never contains** camera video, photos, audio, your face or body landmarks, typed text, what your signs are bound to, your laptop's name, IP address, location, contacts, or any account or device identifier.

**Nothing is uploaded in this release.** Upload requires a server address that is not set, and with no address the app makes no network request for this feature. A future release that turns upload on will say so in the app before anything is sent.

## Deleting the queue

Turn **Share training samples (beta)** off. This deletes the saved takes and the random id immediately. Clearing the site data for Sign-Flow in your phone browser also deletes them.

## If upload is enabled in the future

Samples would go to the intake service in `cloud/contribute/`. It accepts only the fields listed above, rejects anything else, and does not store your IP address or request headers. Samples would be used only to train a shared sign-to-text model.
