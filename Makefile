# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

ifeq ($(OS),Windows_NT)
PY ?= python
BIN := Scripts
EXE := .exe
PLATFORM_LOCK := requirements-windows-lock.txt
else
PY ?= python3.14
BIN := bin
EXE :=
PLATFORM_LOCK :=
endif

VENV ?= .venv
VENV_SMOKE ?= .venv-smoke
PYTHON := $(VENV)/$(BIN)/python$(EXE)
PYTHON_SMOKE := $(VENV_SMOKE)/$(BIN)/python$(EXE)
DEPS_STAMP := $(VENV)/.deps-installed
VERSION := $(shell cat .version)
TEST_WORKERS ?= 4
PODMAN ?= podman
PYTHON_IMAGE ?= docker.io/library/python:3.14-slim

.PHONY: help venv freeze test lint typecheck verify-windows-deps schemas check build package smoke-wheel smoke-sdist smoke-artifacts smoke verify-release container-freeze container-check container-release clean

help:                    ## list available targets
	@grep -hE '^[a-zA-Z][a-zA-Z0-9_-]*:.*##' $(MAKEFILE_LIST) | \
		awk -F':.*## ' '{printf "  %-18s %s\n", $$1, $$2}'

$(PYTHON):
	$(PY) -m venv $(VENV)

$(DEPS_STAMP): $(PYTHON) requirements-lock.txt $(PLATFORM_LOCK) pyproject.toml .version
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r requirements-lock.txt
ifneq ($(PLATFORM_LOCK),)
	$(PYTHON) -m pip install -r $(PLATFORM_LOCK)
endif
	$(PYTHON) -m pip install --no-deps -e .
	$(PYTHON) -c "import sys; assert sys.version_info[:2] == (3, 14), sys.version"
	$(PYTHON) -c "import joplin_importer; assert joplin_importer.__version__ == '$(VERSION)'"
	$(PYTHON) -c "from pathlib import Path; Path('$(DEPS_STAMP)').touch()"

venv: $(DEPS_STAMP)      ## create/update the pinned Python 3.14 project environment

freeze:                  ## re-resolve runtime and dev dependencies for Python 3.14
	rm -rf $(VENV)
	$(PY) -m venv $(VENV)
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -e ".[dev]"
	$(PYTHON) -m piptools compile --extra=dev --output-file=requirements-lock.txt --strip-extras pyproject.toml
	$(PYTHON) -m pip install -r requirements-lock.txt
	$(PYTHON) -m pip install --no-deps -e .
	$(PYTHON) -c "from pathlib import Path; Path('$(DEPS_STAMP)').touch()"

test: venv              ## run all offline tests in parallel (override TEST_WORKERS=N)
	$(PYTHON) -m pytest -n $(TEST_WORKERS) tests

lint: venv              ## run Ruff over source, tests, and release scripts
	$(VENV)/$(BIN)/ruff$(EXE) check src tests scripts

typecheck: venv         ## run mypy over the application source
	$(VENV)/$(BIN)/mypy$(EXE) src

verify-windows-deps: venv  ## verify the Windows COM dependency in the project environment
	$(PYTHON) -c "import pythoncom, win32com.client"

schemas: venv           ## regenerate committed JSON Schemas
	$(PYTHON) -m joplin_importer.schemas schemas

check: lint typecheck test schemas  ## run the complete CI check suite
	git diff --exit-code -- schemas

build: venv             ## build wheel and sdist into dist/
	rm -rf dist build
	$(PYTHON) -m build

package: build          ## build release artifacts and SHA-256 checksums
	$(PYTHON) -c "import hashlib, pathlib; root = pathlib.Path('dist'); (root / 'SHA256SUMS.txt').write_text(''.join(f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n' for path in sorted(root.iterdir()) if path.name != 'SHA256SUMS.txt'), encoding='ascii')"
	$(PYTHON) -c "print(open('dist/SHA256SUMS.txt', encoding='ascii').read(), end='')"

smoke-wheel:            ## install the built wheel into a clean environment
	rm -rf $(VENV_SMOKE)
	$(PY) -m venv $(VENV_SMOKE)
	$(PYTHON_SMOKE) -m pip install --quiet dist/joplin_importer-$(VERSION)-py3-none-any.whl
	$(VENV_SMOKE)/$(BIN)/joplin-importer$(EXE) --version
	$(VENV_SMOKE)/$(BIN)/joplin-importer$(EXE) --help > /dev/null
	rm -rf $(VENV_SMOKE)

smoke-sdist:            ## install the built sdist into a clean environment
	rm -rf $(VENV_SMOKE)
	$(PY) -m venv $(VENV_SMOKE)
	$(PYTHON_SMOKE) -m pip install --quiet dist/joplin_importer-$(VERSION).tar.gz
	$(VENV_SMOKE)/$(BIN)/joplin-importer$(EXE) --version
	rm -rf $(VENV_SMOKE)

smoke-artifacts: smoke-wheel smoke-sdist  ## exercise all already-built artifacts

smoke: package smoke-artifacts  ## build, install, and exercise all release artifacts

verify-release: venv    ## verify version and artifacts; pass TAG=vX.Y.Z for a tag
	$(PYTHON) scripts/verify_release.py $(if $(TAG),--tag $(TAG))

container-freeze:       ## refresh the common lock in Podman with Python 3.14
	$(PODMAN) run --rm --network=slirp4netns -e DEBIAN_FRONTEND=noninteractive -v "$(CURDIR):/work:Z" -w /work $(PYTHON_IMAGE) sh -lc 'apt-get update -qq && apt-get install -y -qq --no-install-recommends make >/dev/null && make freeze PY=python VENV=/tmp/joplin-importer-freeze'

container-check:        ## run make check in Podman with Python 3.14
	$(PODMAN) run --rm --network=slirp4netns -e DEBIAN_FRONTEND=noninteractive -v "$(CURDIR):/work:Z" -w /work $(PYTHON_IMAGE) sh -lc 'apt-get update -qq && apt-get install -y -qq --no-install-recommends git make >/dev/null && make check PY=python VENV=/tmp/joplin-importer-venv'

container-release:      ## run package verification and smoke tests in Podman
	$(PODMAN) run --rm --network=slirp4netns -e DEBIAN_FRONTEND=noninteractive -v "$(CURDIR):/work:Z" -w /work $(PYTHON_IMAGE) sh -lc 'apt-get update -qq && apt-get install -y -qq --no-install-recommends git make >/dev/null && make check package verify-release smoke-artifacts PY=python VENV=/tmp/joplin-importer-venv VENV_SMOKE=/tmp/joplin-importer-smoke'

clean:                  ## remove environments, build artifacts, and caches
	rm -rf $(VENV) $(VENV_SMOKE) dist build src/*.egg-info \
		.mypy_cache .ruff_cache .pytest_cache .coverage htmlcov
	find . -name __pycache__ -type d -not -path "./.git/*" -exec rm -rf {} + 2>/dev/null || true
