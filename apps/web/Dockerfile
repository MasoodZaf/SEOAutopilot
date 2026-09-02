FROM node:22-alpine AS build
WORKDIR /app
RUN corepack enable
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json ./apps/web/package.json
COPY packages/contracts/package.json ./packages/contracts/package.json
RUN pnpm install --filter @seo-autopilot/web... --no-frozen-lockfile
COPY . .
# Next.js inlines NEXT_PUBLIC_* at build time; without this the app
# falls back to the seoautopilot.dev default in robots/sitemap/OG tags.
ARG NEXT_PUBLIC_APP_URL
ENV NEXT_PUBLIC_APP_URL=$NEXT_PUBLIC_APP_URL
RUN pnpm --filter @seo-autopilot/contracts build \
    && pnpm --filter @seo-autopilot/web build
FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production
RUN addgroup -S app && adduser -S app -G app
COPY --from=build --chown=app:app /app/apps/web/.next/standalone ./
COPY --from=build --chown=app:app /app/apps/web/.next/static ./apps/web/.next/static
# pnpm's standalone trace can retain links into the content-addressed store.
COPY --from=build --chown=app:app /app/node_modules/.pnpm ./node_modules/.pnpm
USER app
EXPOSE 3000
CMD ["node","apps/web/server.js"]
