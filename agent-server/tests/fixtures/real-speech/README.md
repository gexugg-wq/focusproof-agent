# Real speech acceptance fixtures

Only this README belongs in Git. **Do not commit** audio recordings, derived
audio, provider responses, or candidate text in this directory.

Store the three explicitly authorized recordings outside the repository under
`/tmp/focusproof-real-speech/`:

- one Chinese recording with clearly spoken Chinese;
- one English recording with clearly spoken English;
- one mixed recording containing both Chinese and English speech.

Each file must be a small WebM/Opus, WAV/PCM, or MP3 recording within the
production 10 MiB and 120 second limits. Record its source, consent, and license
outside Git. The gate accepts the files only as three explicit absolute CLI
arguments, rejects symlinks and duplicate paths, and never searches personal
media directories.

The recordings remain local transcription inputs. The gate must not copy them
into reports, logs, databases, OpenHands events, Evidence, Review, scoring, or
Git. The real Task7 gate never writes candidates to Evidence or any other
durable product state. It does not prove the product UI's editable candidate or
Submit Evidence journey; that end-to-end user action must be demonstrated in
Task 8 before claiming the candidate became ordinary text Evidence through the
product.
