# syntax=docker/dockerfile:1
# Build context is the repository root: docker build -f apps/web/Dockerfile .
FROM node:22-alpine AS build
RUN corepack enable
WORKDIR /repo
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml ./
COPY apps/web/package.json apps/web/
RUN pnpm install --frozen-lockfile
COPY apps/web apps/web
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL NEXT_TELEMETRY_DISABLED=1
RUN pnpm --filter @abb/web build

FROM node:22-alpine
RUN adduser -D -u 10001 abb
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0 PORT=3000
COPY --from=build --chown=abb:abb /repo/apps/web/.next/standalone ./
COPY --from=build --chown=abb:abb /repo/apps/web/.next/static ./apps/web/.next/static
USER abb
EXPOSE 3000
CMD ["node", "apps/web/server.js"]
