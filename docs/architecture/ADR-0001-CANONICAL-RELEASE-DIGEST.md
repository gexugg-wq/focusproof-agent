# ADR-0001: Versioned Canonical Release Digest

Status: Accepted by AI0 for AI4C.3 Task 4 fix round 1
Date: 2026-07-28

## Context

Two clean staging builds must represent the same release even when a build tool emits nondeterministic bytes that do not change the deployed application. Docker image `.Id` values remain useful forensic evidence, but byte equality of those IDs is not the release-equivalence contract. Next.js 15.5.18 generates random preview metadata even though FocusProof does not use Draft Mode or Server Actions. Supplying keys during the image build would put deployment/operator secret material inside the build boundary and is prohibited.

## Decision

AI4C.3 defines `focusproof.canonical-release.v1` as the release-equivalence namespace. Two independent clean builds pass only when the canonical digest for each staging image is identical. OCI image IDs are recorded for diagnosis and are not acceptance identities.

The v1 digest commits to:

- the explicit OCI platform;
- every Dockerfile stage base reference and pinned SHA-256 digest;
- the runtime path;
- relevant OCI runtime configuration, including command, entrypoint, environment, user, working directory, labels, exposed ports, volumes, stop signal, and health check;
- every entry in the flattened runtime filesystem, including path, type, mode, uid, gid, file size and bytes digest, or symlink/hardlink target.

Filesystem traversal is ordered canonically. Tar entry ordering and mtimes are not inputs because the release process already fixes the release epoch and ordering. No file or directory is excluded.

For Next.js 15.5.18 only, v1 replaces the hexadecimal values at these exact paths in `/app/.next/prerender-manifest.json` with fixed same-length placeholders before hashing:

- `preview.previewModeId`;
- `preview.previewModeSigningKey`;
- `preview.previewModeEncryptionKey`.

All other bytes remain significant, including whitespace, object order, routes, code, dependencies, OIDC, OpenHands, and every other JSON field. Missing, malformed, duplicated, or ambiguous allowlisted fields fail closed. Any change to the artifact fields, normalization rules, or allowlist requires a new namespace/version.

Round 2 evidence identified two build-artifact sources rather than additional canonical entropy: npm/V8 compile caches in the runtime image, and Next's server-reference encryption key even though both Server Action maps are empty. Runtime build caches are removed and rejected by the canonicalizer. A build postprocessor validates the exact server-reference schema and requires both node and edge action maps to be empty before replacing that unused key with a fixed public 32-byte base64 placeholder. Any action entry or schema change fails the build. The server-reference manifest remains fully hashed and is not added to the canonical allowlist.

Deployment, user, and operator secrets must never enter image layers, build arguments, image history, logs, or the canonical artifact. FocusProof therefore removes the former Next build-key injection, cache seeding, and all related Compose/environment/documentation plumbing. Static source tests prove the product does not use Draft Mode or Server Actions.

Round 4 treats the remaining Next.js 15.5.18 manifest ordering and bundle drift as build nondeterminism, not canonical entropy. The frontend build uses the framework-supported `experimental.cpus: 1` and `experimental.webpackBuildWorker: false` settings. After a successful build, a fail-closed postprocessor validates and recursively key-sorts only these exact artifacts:

- `/app/.next/app-build-manifest.json`;
- `/app/.next/app-path-routes-manifest.json`;
- `/app/.next/server/app-paths-manifest.json`;
- `/app/.next/server/pages-manifest.json`.

Arrays retain their emitted order. A missing path, symlink, non-file, malformed JSON, unexpected schema, or unexpected value type fails the build before any allowlisted manifest is written. No other JSON is matched. Compiled JavaScript, client-reference manifests, chunks, and server pages are never rewritten. These transformations produce deterministic release bytes and do not expand the v1 canonical digest allowlist.

## Acceptance

Each of two independent Git-index archive contexts is built with clean, no-cache, pulled bases. For both rounds, the gate records OCI image IDs, computes canonical digests from complete flattened runtime filesystems and inspected OCI configuration, starts the real Compose stack, runs browser Authorization Code + PKCE through the Next BFF and FastAPI, and proves official OpenHands SDK 1.31.0 restart/restore continuity. The two canonical digests per image must match exactly; image-ID mismatch alone is diagnostic.

## Consequences

The gate is strict about all release content while acknowledging one narrowly measured framework entropy source. Adding a normalization without a namespace change is an acceptance failure. The canonicalizer never writes or logs canonical content; it returns only a SHA-256 digest.
