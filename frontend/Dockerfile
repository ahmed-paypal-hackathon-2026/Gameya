# Gam'eya web image: build the Vite app, then serve the static files with nginx.
# Build context is the repo root: the app imports ../../../shared/fixtures.
FROM node:20-alpine AS build
WORKDIR /app/frontend
# Vite reads these at build time; pass them with --build-arg (docker-compose does).
ARG VITE_API_BASE_URL=http://localhost:8000
ARG VITE_USE_MOCKS=0
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL \
    VITE_USE_MOCKS=$VITE_USE_MOCKS
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
# Same layout as the repo, so the ../../../shared imports resolve.
COPY shared/fixtures /app/shared/fixtures
COPY frontend/ ./
RUN npm run build

FROM nginx:1.27-alpine
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/frontend/dist /usr/share/nginx/html
EXPOSE 8080
