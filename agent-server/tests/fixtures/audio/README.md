# Synthetic audio fixtures

These files contain gzip-compressed, base64-encoded mathematical silence only.
They contain no captured speech, user audio, transcript, path, or credential.

They were generated with the pinned container image
`jrottenberg/ffmpeg:6.1-alpine@sha256:4641478865a2387bb1d180dd9263e7226dab887c0789e02fa077fe919ef543df`.
The valid sources use `anullsrc`, mono audio, and durations of 0.2 seconds.
`multitrack.webm` maps two independent `anullsrc` streams, `too-long.mp3` is 121
seconds of silence, `zero.wav` uses `-t 0`, and `truncated.webm` is the first 64
bytes of `valid.webm`. Each output was piped through `gzip -9 | base64`.

Runtime tests decode only into request-scoped temporary directories. The encoded
fixtures are test-only and must never be used as production upload fallbacks.

`chromium-complete.webm.gz.b64` is a browser-produced, no-user-speech fixture.
On 2026-08-30, Playwright Chromium 149.0.7827.55 recorded 1.7 seconds from
Chromium's official fake microphone with `MediaRecorder.start()` and no
timeslice or `requestData()`. The fake input was the existing mathematical
silence fixture. Chromium emitted one `audio/webm;codecs=opus` chunk; MediaInfo
reports one Opus audio track, no video, a 1.620 second General and Audio
Duration, and no truncation flag. The raw WebM SHA-256 is
`f0fcf8a8f9f1ab4043d31ae22dc28011f31bea195b012198b3f3e148d2d697d6`.
It was piped through `gzip -9 | base64`; no generator or raw recording is stored
as a separate Git artifact.

`chromium-streaming.webm.gz.b64` is a browser-produced, no-user-speech
compatibility fixture. On 2026-08-30, Playwright Chromium 149.0.7827.55 recorded
one second from Chromium's official fake microphone as
`audio/webm;codecs=opus`, with a 100 ms MediaRecorder timeslice. The fake input
was a low-amplitude 440 Hz mathematical tone at 48 kHz, generated only for
fixture construction with Ubuntu ffmpeg 6.1.1; production inspection never
executes ffmpeg. The raw WebM SHA-256 was
`46b10fa72e126ee22e8610c76b5e1c26a4434de4f90fce32b07729d84522d313`.
It has one Opus audio track, no video, 16 packets, no container or audio
Duration/Cues, and MediaInfo reports `IsTruncated=Yes`. The browser output was
piped through `gzip -9 | base64`; neither the uncompressed recording nor the
one-off generator is stored as a separate Git artifact.
Production must reject this timeslice/seekless fixture fail-closed.
