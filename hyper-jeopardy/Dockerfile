FROM node:20-slim
WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .
RUN npm run build

ENV NODE_ENV=production
EXPOSE 3000

# Run the server as the container's main process (exec form, PID 1) rather than
# through `npm run start` → `sh -c`. That wrapper chain turns the normal SIGTERM
# Render sends on a restart/redeploy into noisy `npm error signal SIGTERM` /
# `command failed` log lines, and it can swallow the signal before our graceful
# snapshot flush runs. With Node as PID 1, SIGTERM reaches the server directly:
# it flushes the game snapshot and exits 0 — clean shutdown, no npm errors.
CMD ["node", "--import", "tsx", "server.ts"]
