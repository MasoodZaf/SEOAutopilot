FROM mcr.microsoft.com/playwright:v1.62.0-noble
WORKDIR /app
RUN npm install --global pnpm@10.15.0 && useradd --create-home --uid 10001 app
COPY . .
RUN pnpm install --filter @seo-autopilot/crawler --no-frozen-lockfile && pnpm --filter @seo-autopilot/crawler build
USER app
CMD ["node","services/crawler/dist/index.js"]
