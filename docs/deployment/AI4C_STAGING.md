# AI4C staging stack

The staging stack runs one FastAPI worker, one Next production server, a
fixed-version Keycloak container, and a private PostgreSQL database. The
application uses the official OpenHands SDK runtime: PostgreSQL stores product
projections, while the `focusproof-openhands-data` named volume stores native
conversation state. Backend and frontend application containers run non-root;
the backend is intentionally a single Uvicorn worker.

## Inputs and security boundary

Start from `.env.example`, copied to a temporary operator-owned file outside
version control. That file contains paths and public configuration only: never
put a password, JWT, private key, JWKS content, or provider API key value in it.
Do not commit the operator file or any referenced secret file.

The following three inputs are **published port numbers**, not addresses:

- `FOCUSPROOF_STAGING_BACKEND_HOST_PORT`
- `FOCUSPROOF_STAGING_FRONTEND_HOST_PORT`
- `FOCUSPROOF_STAGING_OIDC_HOST_PORT`

Each must be an ASCII decimal integer in `1..65535`; it is not host:port.
Compose itself fixes each application publication to `127.0.0.1`, so an
operator cannot use these variables to expose an application on `0.0.0.0`.
PostgreSQL has no published host port and remains on the private Compose
network.

The `NEXT_PUBLIC_OIDC_ISSUER`, `NEXT_PUBLIC_OIDC_CLIENT_ID`,
`NEXT_PUBLIC_OIDC_AUDIENCE`, and `NEXT_PUBLIC_OIDC_REDIRECT_URI` inputs are
public, non-secret Next build inputs. Compose injects them as frontend image
build args before `next build`; they are not runtime secret settings. They must
match the local Keycloak realm and selected loopback ports. For the supplied
staging realm that means:

```text
NEXT_PUBLIC_OIDC_ISSUER=https://127.0.0.1:<OIDC published port>/realms/focusproof
NEXT_PUBLIC_OIDC_CLIENT_ID=focusproof-staging
NEXT_PUBLIC_OIDC_AUDIENCE=focusproof-api
NEXT_PUBLIC_OIDC_REDIRECT_URI=http://127.0.0.1:<frontend published port>/
```

File-path inputs never hold the file contents in the environment. The
PostgreSQL password and OIDC fingerprint-key paths refer to secret material.
`FOCUSPROOF_STAGING_OIDC_REALM_FILE` is the realm import JSON path.
`FOCUSPROOF_STAGING_OIDC_ADMIN_PASSWORD_FILE` and
`FOCUSPROOF_STAGING_OIDC_TEST_USER_PASSWORD_FILE` are separate password-file
paths. `FOCUSPROOF_STAGING_OIDC_TLS_CERT_FILE`,
`FOCUSPROOF_STAGING_OIDC_TLS_KEY_FILE`, and
`FOCUSPROOF_STAGING_OIDC_CA_FILE` are the TLS/CA file paths. Compose supplies
these files to the containers through read-only Docker secrets; keep all
operator-owned secret files outside the repository.

## Canonical release reproducibility

The two-round gate follows [ADR-0001](../architecture/ADR-0001-CANONICAL-RELEASE-DIGEST.md). It records OCI image IDs for diagnosis and accepts a release only when both versioned canonical release digests match. The digest covers platform, pinned base digests, runtime configuration, runtime path, and every filesystem entry byte/mode/owner/link target. Only the three explicitly named Next.js 15.5.18 preview entropy values are normalized; every other byte remains significant. The build consumes no deployment or operator secret.

The frontend runtime image removes npm logs and Node compile caches after dependency installation. After `next build`, a repository script accepts only the exact empty Server Action manifest schema (`node` and `edge` maps both empty) and assigns a fixed public, non-secret, valid-length placeholder to the otherwise random unused encryption key. A non-empty action map or schema drift fails the image build. The complete resulting manifest remains part of the canonical digest.

## Keycloak, TLS, and OIDC

The Compose file pins Keycloak to a fixed `26.3.2` image digest. It starts with
`start --import-realm`; `FOCUSPROOF_STAGING_OIDC_REALM_FILE` is mounted as the
realm import JSON for realm `focusproof`. The Keycloak bootstrap admin username
is `focusproof-staging-admin`. The staging E2E realm test user `learner` gets
its password only from `FOCUSPROOF_STAGING_OIDC_TEST_USER_PASSWORD_FILE`; the
distinct admin-password file is supplied through
`FOCUSPROOF_STAGING_OIDC_ADMIN_PASSWORD_FILE`. Both are mounted through their
corresponding Docker secrets. Do not place either password in an environment
value.

The TLS certificate and key paths provide Keycloak HTTPS material. The CA file
is mounted into the backend so it can trust the container-internal Keycloak
HTTPS endpoint. A real browser E2E run or a host browser visiting local
Keycloak must trust this local CA, or use a controlled test environment that
configures the same trust chain. The staging E2E imports that CA into a
temporary Chromium/NSS profile created under Playwright test output, launches a
persistent Chromium context with that controlled profile, and removes it when
the test ends. It never ignores certificate errors. Do not disable TLS verification,
ignore certificate errors, or replace this HTTPS flow with HTTP.

The backend validates access tokens with the issuer, audience, JWKS URI, and RS256.
The JWKS is Keycloak's runtime public endpoint on the private Compose network;
an operator does not generate or mount a signing JWKS file. Real acceptance uses
a browser OIDC Authorization Code flow with Code+PKCE, then the authenticated
BFF/API learning flow. It is not Python hand-crafted Bearer tokens or a
JWKS-only probe.

## Start, readiness, persistence, and shutdown

From the repository root, after setting the public values and secret-file paths
in the operator-owned file:

```sh
docker compose --env-file /path/to/staging.paths \
  -f deploy/compose.staging.yml up --build -d --wait
curl --fail http://127.0.0.1:18080/ready
```

For a non-default backend published port, use that numeric port in the loopback
ready URL. Compose waits for PostgreSQL, runs the one-shot `alembic upgrade
head` migration, then starts the backend. Application startup verifies the
database revision and never runs migrations implicitly.

Normal service restarts retain `focusproof-postgres-data` and
`focusproof-openhands-data`. Stop without deleting data:

```sh
docker compose --env-file /path/to/staging.paths \
  -f deploy/compose.staging.yml down --timeout 30
```

Do not add `--volumes` outside a disposable verification run. This topology is
single-host and loopback-only; it is a staging proof, not a public deployment.
Rollback images and Compose together, and do not start an older application
against an unsupported schema. Backup and restore coordination is Task 5 and is
intentionally not defined here.
