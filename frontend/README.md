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

The browser talks only to `/api/focusproof/...`. That BFF route allowlists the formal FocusProof session, evidence, answer, review, events, reviews, and health endpoints. It does not proxy debug routes.
