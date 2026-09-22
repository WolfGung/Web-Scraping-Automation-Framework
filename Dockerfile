# ScrapeWatch: one image for every service in docker-compose.yml (demo-store, scrape,
# scrape-pg). Playwright's own Chromium is installed here, with its OS dependencies,
# so `scrapewatch scrape quotes` works out of the box in a container the same way it
# does after `playwright install --with-deps chromium` on a bare host.
FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

# The dev extra brings in pytest/ruff — this image doubles as the one CI's test jobs
# could run in, not only the runtime image — and `postgres` brings in `psycopg` for
# the `scrape-pg` service's PostgreSQL URL.
RUN pip install --no-cache-dir -e ".[dev,postgres]"

# Installed to a path of its own rather than into the installing user's home: the
# container runs as `scrapewatch` (below) and a browser unpacked into /root is one
# that user cannot read, let alone launch. The variable stays set at runtime, which
# is how Playwright finds it again.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
RUN playwright install --with-deps chromium

# Nothing here needs root. A scraper drives a browser over other people's pages,
# which is the last process that should be able to write anywhere in its own
# container — and the browser is happier unprivileged too. /data exists and is owned
# before the volume is mounted on it, because Docker takes a fresh named volume's
# ownership from the image directory it covers: without this the SQLite database
# would be unwritable the first time anyone ran `docker compose run --rm scrape`.
RUN useradd --create-home --uid 10001 scrapewatch \
    && mkdir -p /data \
    && chown -R scrapewatch:scrapewatch /app /data
USER scrapewatch

CMD ["scrapewatch", "--help"]
