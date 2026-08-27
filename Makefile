PYTHON := .venv/bin/python
VENV   := .venv

.DEFAULT_GOAL := help
.PHONY: help venv lint reformat test-local test-all clean

help: ## Show available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk -F':.*?## ' '{printf "  %-14s %s\n", $$1, $$2}'

venv: ## Create .venv and install the package with its dev group
	test -d $(VENV) || uv venv
	uv sync --group dev

lint: venv ## Run ruff checks, the redefinition guard and the type checker
	$(PYTHON) -m ruff check src tests tools
	$(PYTHON) -m ruff format --check src tests tools
	# Not covered by ruff: F811 only fires while the first binding is unused,
	# so a pasted block whose names are called in between passes it clean.
	$(PYTHON) tools/check_redefinitions.py
	$(PYTHON) -m ty check src

reformat: venv ## Autoformat and autofix
	$(PYTHON) -m ruff format src tests tools
	$(PYTHON) -m ruff check --fix src tests tools

test-local: venv ## Run the test suite
	$(PYTHON) -m pytest

# The panel reads platform-specific facts -- the macOS SystemVersion plist, APFS
# partitions, an absent Docker socket -- so a green run on one OS says little
# about the others. This target runs the interpreter matrix; the operating
# system matrix only exists in CI, which has the runners for it.
test-all: ## Run the test suite across every supported interpreter
	uvx --with tox-uv tox

clean: ## Remove the virtualenv and build artefacts
	rm -rf $(VENV) dist build .pytest_cache htmlcov .coverage
