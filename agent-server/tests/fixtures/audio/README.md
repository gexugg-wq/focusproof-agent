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
