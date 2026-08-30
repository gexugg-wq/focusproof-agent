# FocusProof Frontend

Next.js App Router frontend for the AI3 FocusProof MVP.

## Environment

Copy `.env.example` to `.env.local` for local frontend-only work.

- `FOCUSPROOF_API_BASE_URL`: server-side BFF target, default `http://127.0.0.1:8000`.

No LLM API key is stored or read by the browser.

## Commands

```bash
cd /home/holy/web3/focusproof-agent/frontend
npm install
npm run dev
npm run lint
npm run typecheck
npm run test
npm run build
npm run test:e2e
```

The browser talks only to `/api/focusproof/...`. That BFF route allowlists the formal FocusProof session, evidence, answer, review, events, reviews, health, and streaming transcription endpoints. It does not proxy debug routes.

The unified Evidence composer optionally exposes a microphone control when the
versioned speech capability is enabled. It uses native browser microphone
permission and records at most 120 seconds, then uploads one bounded clip for
transcription. The returned transcript is a raw, editable candidate in the same
textarea; it never auto-submits or creates Evidence. The existing Submit evidence
button remains the only path that creates text Evidence. Audio/Blob/object-URL
state is component-scoped. A retryable API failure retains exactly one File for
an explicit same-mount Retry; retry reuses that clip with a fresh idempotency key
and never runs automatically. A non-retryable or unknown failure clears the
File and fails closed without Retry. Success, cancel, a new recording, or
unmount also clears the retained File and recording resources. The production
provider is DashScope `qwen3-asr-flash`; the review LLM configuration is
unrelated.
