FROM node:22-bookworm-slim@sha256:8607a9064d4a571140998ae9e52a3b3fcf9cff361d04642d5971e6cd76d39e27 AS build

WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM node:22-bookworm-slim@sha256:8607a9064d4a571140998ae9e52a3b3fcf9cff361d04642d5971e6cd76d39e27

WORKDIR /app
RUN groupadd --system --gid 10001 focusproof \
    && useradd --system --uid 10001 --gid focusproof --home-dir /app focusproof \
    && chown -R focusproof:focusproof /app
COPY --chown=focusproof:focusproof frontend/package.json frontend/package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force
COPY --from=build --chown=focusproof:focusproof /app/.next ./.next

USER focusproof
EXPOSE 3000

CMD ["node_modules/.bin/next", "start", "-p", "3000"]
