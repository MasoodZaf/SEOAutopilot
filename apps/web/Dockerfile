FROM node:22-alpine AS build
WORKDIR /app
RUN corepack enable
COPY . .
RUN pnpm install --no-frozen-lockfile && pnpm --filter @seo-autopilot/contracts build && pnpm --filter @seo-autopilot/web build
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
