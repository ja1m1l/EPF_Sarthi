# ─────────────────────────────────────────────────────────────
# EPF Sentinel Makefile
# ─────────────────────────────────────────────────────────────
# Targets: build | deploy | test | logs
#
# Usage:
#   make build          - SAM build (no container)
#   make deploy         - SAM deploy with git sha injected
#   make test           - run all pytest unit tests
#   make logs FUNC=<name>  - tail the last 10 minutes of logs
#
# Variables (override on CLI):
#   STAGE   = dev | staging | prod   (default: dev)
#   REGION  = AWS region             (default: ap-south-1)
#   FUNC    = Lambda function name   (default: epf-sentinel-health-dev)
# ─────────────────────────────────────────────────────────────

STAGE   ?= dev
REGION  ?= ap-south-1
FUNC    ?= epf-sentinel-health-$(STAGE)

# Resolve short git SHA; fall back to "local" if git is unavailable.
GIT_SHA := $(shell git rev-parse --short HEAD 2>/dev/null || echo local)

SAM_FLAGS := \
	--region $(REGION) \
	--config-file infra/samconfig.toml \
	--config-env $(STAGE)

TEMPLATE := infra/template.yaml

.PHONY: build deploy test logs clean

## ── build ────────────────────────────────────────────────────
build:
	@echo ">>> SAM build (stage=$(STAGE), sha=$(GIT_SHA))"
	sam build \
		--template $(TEMPLATE) \
		--use-container=false \
		$(SAM_FLAGS)

## ── deploy ───────────────────────────────────────────────────
deploy: build
	@echo ">>> SAM deploy (stage=$(STAGE), sha=$(GIT_SHA))"
	sam deploy \
		--template $(TEMPLATE) \
		--parameter-overrides \
			Stage=$(STAGE) \
			GitSha=$(GIT_SHA) \
		$(SAM_FLAGS)

## ── test ─────────────────────────────────────────────────────
test:
	@echo ">>> Running pytest unit tests"
	python -m pytest tests/unit -v --tb=short

## ── logs ─────────────────────────────────────────────────────
logs:
	@echo ">>> Tailing CloudWatch logs for $(FUNC)"
	sam logs \
		--name $(FUNC) \
		--region $(REGION) \
		--tail \
		--since 10m

## ── clean ────────────────────────────────────────────────────
clean:
	@echo ">>> Cleaning SAM build artefacts"
	rm -rf .aws-sam
