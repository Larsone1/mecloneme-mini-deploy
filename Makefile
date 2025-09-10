# Makefile
SHELL := /bin/bash

.PHONY: api web dev fmt lint smoke pages-ci

api:
	uvicorn backend.mcm_api:app --host 0.0.0.0 --port 8000 --reload

web:
	cd web && npm run dev -- --host

dev:
	@echo "API -> http://localhost:8000/api/health"
	@echo "WEB -> http://localhost:5173"
	make -j2 api web

fmt:
	pip install -q pre-commit black==24.8.0 ruff==0.6.4 || true
	pre-commit run --all-files || true
	cd web && npm run format || true

lint:
	ruff backend || true
	cd web && npm run lint || true

smoke:
	curl -fsS http://localhost:8000/api/health | jq .
	@echo "OK: local API health"

pages-ci:
	gh workflow run ci.yml
