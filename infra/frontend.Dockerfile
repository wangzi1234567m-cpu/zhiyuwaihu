FROM node:22-bookworm-slim AS development
WORKDIR /app
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
ENV BACKEND_URL=http://backend:8000
FROM development AS builder
ENV CONTAINER_BUILD=1
RUN npm run build
FROM node:22-bookworm-slim AS runner
WORKDIR /app
ENV NODE_ENV=production HOSTNAME=0.0.0.0 PORT=3000
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
USER node
CMD ["node", "server.js"]
