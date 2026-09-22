.PHONY: help install lint test test-live scrape demo-store report

help: ## List the available targets.
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*## "}; {printf "  %-12s %s\n", $$1, $$2}'

install: ## Install the project with its dev extras (uv if available, else pip).
	@if command -v uv >/dev/null 2>&1; then \
		uv pip install -e ".[dev]"; \
	else \
		pip install -e ".[dev]"; \
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
