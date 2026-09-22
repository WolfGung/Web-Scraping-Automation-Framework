.PHONY: help install browsers lint test test-live scrape demo-store report

help: ## List the available targets.
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*## "}; {printf "  %-12s %s\n", $$1, $$2}'

install: ## Install the project with its dev extras (uv if available, else pip), and Chromium.
	@if command -v uv >/dev/null 2>&1; then \
		uv pip install -e ".[dev]"; \
	else \
		pip install -e ".[dev]"; \
	fi
	@$(MAKE) --no-print-directory browsers

browsers: ## Install the Chromium the browser checks drive (Playwright's own build).
	@# `make test` runs the e2e checks, and they open a real browser: without this an
	@# otherwise-correct clean clone fails its first command for a reason that has
	@# nothing to do with the code. `--with-deps` also installs the OS packages that
	@# browser needs, which needs root — so a refusal (no sudo, or a password nobody
	@# typed) falls back to the browser alone and says so, rather than failing the
	@# install. On a machine that already has those libraries, which is most of them,
	@# the fallback is all that was needed anyway.
	@if playwright install --with-deps chromium; then \
		echo "browsers: Chromium and its OS dependencies are installed."; \
	else \
		echo "browsers: 'playwright install --with-deps chromium' exited $$? — installing the OS" >&2; \
		echo "browsers: packages needs root, and this shell was not allowed to. Falling back to" >&2; \
		echo "browsers: the browser alone; if a check later fails to launch it, run that command" >&2; \
		echo "browsers: again with sudo." >&2; \
		playwright install chromium && echo "browsers: Chromium is installed (OS dependencies were left alone)."; \
	fi

lint: ## ruff check src, tests, the page builder and the scripts.
	ruff check src tests showcase scripts

test: ## The network-free gate: everything except tests/live.
	pytest -m "not live"

test-live: ## Opt-in: hits the real practice sites (books.toscrape.com, quotes.toscrape.com).
	pytest -m live

scrape: ## Run every source once, into data/run.
	scrapewatch scrape all --out data/run

demo-store: ## Run the demo store locally, on 127.0.0.1:8765.
	scrapewatch demo-store

report: ## Rebuild the change report from storage and print it.
	scrapewatch report
