# ============================================================
# SessionFlow frontend — multi-stage build
# Stage 1: build the Angular app
# Stage 2: serve the static SPA with nginx
# ============================================================

# ---- build ----
FROM node:22-alpine AS build
WORKDIR /app

# install deps with a clean, reproducible install
COPY package.json package-lock.json ./
RUN npm ci

# build the production bundle
COPY . .
RUN npm run build

# ---- serve ----
FROM nginx:1.27-alpine AS serve

# SPA-aware nginx config (history fallback to index.html)
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Angular outputs to dist/<project>/browser
COPY --from=build /app/dist/frontend/browser /usr/share/nginx/html

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
