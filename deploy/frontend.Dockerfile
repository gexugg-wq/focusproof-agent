FROM node:22-bookworm-slim@sha256:8607a9064d4a571140998ae9e52a3b3fcf9cff361d04642d5971e6cd76d39e27 AS build

ARG SOURCE_DATE_EPOCH=1735689600
ENV SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}

WORKDIR /app
RUN --mount=type=bind,source=frontend/package.json,target=/mnt/input/package.json \
    --mount=type=bind,source=frontend/package-lock.json,target=/mnt/input/package-lock.json \
    install -m 0644 -o root -g root /mnt/input/package.json /app/package.json \
    && install -m 0644 -o root -g root /mnt/input/package-lock.json /app/package-lock.json \
    && npm ci \
    && find /app/node_modules -exec touch -h -d "@${SOURCE_DATE_EPOCH}" {} + \
    && touch -h -d "@${SOURCE_DATE_EPOCH}" /app/package.json /app/package-lock.json /app
RUN --mount=type=bind,source=frontend,target=/mnt/input/frontend \
    cp -a --no-preserve=ownership /mnt/input/frontend/. /app/ \
    && chown -R root:root /app \
    && find /app -path /app/node_modules -prune -o -exec touch -h -d "@${SOURCE_DATE_EPOCH}" {} +
ARG NEXT_PUBLIC_OIDC_ISSUER
ARG NEXT_PUBLIC_OIDC_CLIENT_ID
ARG NEXT_PUBLIC_OIDC_AUDIENCE
ARG NEXT_PUBLIC_OIDC_REDIRECT_URI
RUN node -e "const names=['NEXT_PUBLIC_OIDC_ISSUER','NEXT_PUBLIC_OIDC_CLIENT_ID','NEXT_PUBLIC_OIDC_AUDIENCE','NEXT_PUBLIC_OIDC_REDIRECT_URI']; if(names.some((name)=>!process.env[name] || process.env[name] !== process.env[name].trim())) throw new Error('Invalid public OIDC build configuration'); if(new URL(process.env.NEXT_PUBLIC_OIDC_ISSUER).protocol !== 'https:') throw new Error('Invalid public OIDC build configuration'); new URL(process.env.NEXT_PUBLIC_OIDC_REDIRECT_URI)"
RUN --mount=type=bind,source=scripts/normalize_next_empty_action_manifest.mjs,target=/mnt/input/normalize_next_empty_action_manifest.mjs \
    npm run build \
    && node /mnt/input/normalize_next_empty_action_manifest.mjs /app/.next/server/server-reference-manifest.json \
    && node /mnt/input/normalize_next_empty_action_manifest.mjs --canonicalize-build-manifests /app/.next \
    && rm -rf /app/.next/cache /app/.next/trace \
    && find /app/.next -exec touch -h -d "@${SOURCE_DATE_EPOCH}" {} + \
    && touch -h -d "@${SOURCE_DATE_EPOCH}" /app

FROM node:22-bookworm-slim@sha256:8607a9064d4a571140998ae9e52a3b3fcf9cff361d04642d5971e6cd76d39e27

ARG SOURCE_DATE_EPOCH=1735689600
ENV SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}

WORKDIR /app
RUN groupadd --system --gid 10001 focusproof \
    && useradd --system --uid 10001 --gid focusproof --home-dir /app focusproof \
    && chown -R focusproof:focusproof /app \
    && touch -h -d "@${SOURCE_DATE_EPOCH}" \
        /etc /etc/group /etc/gshadow /etc/passwd /etc/shadow
RUN --mount=type=bind,source=frontend/package.json,target=/mnt/input/package.json \
    --mount=type=bind,source=frontend/package-lock.json,target=/mnt/input/package-lock.json \
    install -m 0644 -o focusproof -g focusproof /mnt/input/package.json /app/package.json \
    && install -m 0644 -o focusproof -g focusproof /mnt/input/package-lock.json /app/package-lock.json \
    && npm ci --omit=dev \
    && npm cache clean --force \
    && rm -rf /root/.npm /tmp/node-compile-cache \
    && find /app/node_modules -exec touch -h -d "@${SOURCE_DATE_EPOCH}" {} + \
    && touch -h -d "@${SOURCE_DATE_EPOCH}" /app/package.json /app/package-lock.json /app
RUN --mount=type=bind,from=build,source=/app/.next,target=/mnt/input/next \
    mkdir -p /app/.next \
    && cp -a --no-preserve=ownership /mnt/input/next/. /app/.next/ \
    && chown -R focusproof:focusproof /app/.next \
    && find /app/.next -exec touch -h -d "@${SOURCE_DATE_EPOCH}" {} + \
    && touch -h -d "@${SOURCE_DATE_EPOCH}" /app

USER focusproof
EXPOSE 3000

CMD ["node_modules/.bin/next", "start", "-p", "3000"]
