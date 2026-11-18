PYTHON ?= python3
VENV := .venv
PY := $(VENV)/bin/python
RUFF := $(VENV)/bin/ruff
PYTEST := $(VENV)/bin/pytest

.PHONY: setup config config-tunnel lint format test check serve tunnel stdio hooks commit-lint clean

$(VENV)/.installed: pyproject.toml
	$(PYTHON) -m venv $(VENV)
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e '.[dev]'
	touch $@

setup: $(VENV)/.installed

config: setup
	$(PY) -m scripts.config

# Not required for direct stdio clients.
config-tunnel: setup
	$(PY) -m scripts.config --tunnel

lint: setup
	$(RUFF) check .

format: setup
	$(RUFF) format .

test: setup
	$(PYTEST) -q

check: lint test

# Default workflow: start the tunnel client, which supervises the stdio MCP.
serve: check
	@$(PY) -m scripts.serve --tunnel

# Compatibility alias.
tunnel: serve

# Direct, tunnel-free stdio mode for any compatible MCP client.
# All make/install/lint output must go to stderr to preserve JSON-RPC stdout.
stdio:
	@$(MAKE) --no-print-directory check 1>&2
	@$(PY) -m scripts.serve

hooks:
	chmod +x .githooks/commit-msg .githooks/pre-commit
	git config core.hooksPath .githooks

commit-lint: setup
	@if [ -z "$(MSG)" ]; then echo 'Usage: make commit-lint MSG="feat: example"'; exit 2; fi
	@$(PY) -m scripts.lint_commit --subject "$(MSG)"

clean:
	rm -rf $(VENV) .pytest_cache .ruff_cache
