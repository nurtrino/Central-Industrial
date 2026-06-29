# Deep Research — headless, browser-driven research service (Render).
#
# Uses python:3.12-slim + `playwright install --with-deps chromium` so the image
# always ships a Chromium that matches the installed Playwright version (no reliance
# on a pinned Playwright base-image tag). The app talks to that bundled Chromium
# headless (DRT_HEADED=0, DRT_BROWSER_CHANNEL="" → no "chrome" channel).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    DRT_HEADED=0 \
    DRT_BROWSER_CHANNEL=""

WORKDIR /app

# curl is used by the Odysseus sub-engine's page fetcher; ca-certificates for TLS.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install -r requirements.txt \
    && python -m playwright install --with-deps chromium

COPY . .

# Render injects $PORT at runtime; 5006 is just a local default.
ENV PORT=5006

# SINGLE worker is mandatory — the async job registry (_JOBS) is in-process, so a
# job started in one worker must be polled from the same worker. Threads handle the
# concurrent kickoff + /status polling pattern.
CMD gunicorn --workers 1 --threads 8 --timeout 300 --bind 0.0.0.0:$PORT dr_server:app
