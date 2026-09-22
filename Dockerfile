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
RUN playwright install --with-deps chromium

CMD ["scrapewatch", "--help"]
