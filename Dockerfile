FROM node:24.14.1-bookworm-slim AS node_runtime

FROM python:3.12.13-slim-bookworm

# NexaWeave development image: use the selected Node and Python runtimes.
RUN apt-get update \
  && apt-get install -y --no-install-recommends libstdc++6 libatomic1 \
  && rm -rf /var/lib/apt/lists/*
COPY --from=node_runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node_runtime /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
  && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

# Copy uv from its pinned upstream image.
COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /uvx /bin/

WORKDIR /app

# Copy dependency manifests first for build caching.
COPY package.json package-lock.json ./
COPY frontend/package.json frontend/package-lock.json ./frontend/
COPY backend/pyproject.toml backend/uv.lock ./backend/

# Install the repository's locked Node and Python dependencies.
RUN npm ci \
  && npm ci --prefix frontend \
  && cd backend && uv sync --frozen

# Copy the local NexaWeave source tree.
COPY . .

EXPOSE 3000 5001

# Vite must not launch a browser or inherit browser arguments in this image.
ENV BROWSER=none

# Development command only; the frontend binds within the container while
# Compose exposes it only on the host loopback interface.
CMD ["env", "-u", "BROWSER_ARGS", "./node_modules/.bin/concurrently", "--kill-others", "-n", "backend,frontend", "-c", "green,cyan", "npm run backend", "cd frontend && npm run dev -- --host 0.0.0.0"]
