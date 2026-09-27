.DEFAULT_GOAL := help
SHELL := /bin/zsh

BACKEND := backend
FRONTEND := frontend
CONFIG := $(HOME)/.config/atlas/atlas.json

.PHONY: help install config protocol build serve dev dev-backend dev-frontend check check-backend check-frontend status log push

help:  ## Show available targets
	@printf '%s\n' \
	  '  install         Sync both projects, install user config, generate protocol, build' \
	  '  config          Install ~/.config/atlas/atlas.json if absent' \
	  '  protocol        Generate frontend protocol types from backend schemas' \
	  '  build           Build the browser client' \
	  '  serve           Build and run the complete local application' \
	  '  dev             Run backend and Vite together' \
	  '  check           Run backend and frontend quality gates' \
	  '  status          Show Git status' \
	  '  push            Push the current branch to every configured remote'

install:  ## Install the complete local development application
	@uv sync --project $(BACKEND) --all-groups
	@bun install --cwd $(FRONTEND)
	@$(MAKE) config
	@$(MAKE) protocol
	@$(MAKE) build

config:  ## Install the adjustable user configuration without overwriting it
	@uv run --project $(BACKEND) atlas config-init

protocol:  ## Generate TypeScript protocol types from backend OpenAPI
	@uv run --project $(BACKEND) atlas export-openapi $(CURDIR)/$(FRONTEND)/openapi.json
	@bun run --cwd $(FRONTEND) generate:protocol

build:  ## Build the browser client
	@bun run --cwd $(FRONTEND) build

serve: config protocol build  ## Run backend and serve the built browser client
	@ATLAS_FRONTEND_DIR="$(CURDIR)/$(FRONTEND)/dist" uv run --project $(BACKEND) atlas serve

dev: config protocol  ## Run backend and Vite development servers
	@$(MAKE) -j2 dev-backend dev-frontend

dev-backend:  ## Run the backend with reload
	@uv run --project $(BACKEND) atlas serve --reload

dev-frontend:  ## Run Vite
	@bun run --cwd $(FRONTEND) dev

check: check-backend check-frontend  ## Run every project check

check-backend:  ## Lint, format-check, type-check, and test backend
	@uv run --project $(BACKEND) ruff check .
	@uv run --project $(BACKEND) ruff format --check .
	@uv run --project $(BACKEND) pyright
	@uv run --project $(BACKEND) pytest

check-frontend:  ## Type-check and build frontend
	@bun run --cwd $(FRONTEND) check

status:  ## Show Git status
	@git status --short

log:  ## Show recent Git history
	@git log --oneline -10 2>&1 || true

push:  ## Push the current branch to every configured remote
	@branch="$$(git branch --show-current)"; \
	git remote | while read -r remote; do \
		printf '==> pushing %s to %s\n' "$$branch" "$$remote"; \
		git push -u "$$remote" "$$branch"; \
	done
