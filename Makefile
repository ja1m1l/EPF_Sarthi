# ─────────────────────────────────────────────────────────────
# EPF Sarthi Makefile
# ─────────────────────────────────────────────────────────────
# Targets: build | deploy | test | logs
#
# Usage:
#   make build             - SAM containerized build
#   make deploy            - SAM deploy with git sha injected
#   make test              - run all pytest unit tests
#   make logs              - print last 10 minutes of Lambda logs and exit
#   make logs TAIL=1       - tail CloudWatch logs continuously
#
# Variables (override on CLI):
#   STAGE           = dev | staging | prod   (default: dev)
#   REGION          = AWS region             (default: ap-south-1)
#   AMPLIFY_ORIGIN  = frontend origin        (default: https://placeholder.amplifyapp.com)
#   CORS_ORIGINS    = comma-separated allowed origins
#                     (default: Amplify origin, plus localhost:5173 on dev)
#   USE_CONTAINER   = 1 to build inside Docker (default: 0)
# ─────────────────────────────────────────────────────────────

STAGE           ?= dev
REGION          ?= ap-south-1
AMPLIFY_ORIGIN  ?= https://placeholder.amplifyapp.com
# Dev and prod must use different Secrets Manager names. The key value
# itself is never passed on the CLI.
GEMINI_SECRET_NAME ?= $(if $(filter prod,$(STAGE)),epf-sentinel/gemini-api-key-prod,epf-sentinel/gemini-api-key)
DAILY_ANALYSIS_CAP ?= 20

comma := ,

# The Vite dev server is allowed through CORS on the dev stage only;
# prod ships with the Amplify origin alone.
CORS_ORIGINS ?= $(AMPLIFY_ORIGIN)$(if $(filter dev,$(STAGE)),$(comma)http://localhost:5173,)

# Resolve short git SHA; fall back to "local" if git is unavailable.
GIT_SHA := $(or $(shell git rev-parse --short HEAD),local)

# Map stage to config-env section in infra/samconfig.toml
CONFIG_ENV := $(if $(filter dev,$(STAGE)),default,$(STAGE))

TEMPLATE := infra/template.yaml

.PHONY: build deploy test logs

## ── build ────────────────────────────────────────────────────
# A container build is off by default: every function's requirements are
# pure Python, and the only binary dependency (pydantic-core) lives in the
# shared layer, whose build-SharedLayer recipe already pip-installs
# manylinux2014_x86_64 wheels explicitly.  Set USE_CONTAINER=1 if Docker is
# available and you want the stricter build.
build:
	@echo ">>> SAM build (stage=$(STAGE), sha=$(GIT_SHA))"
	sam build \
		--template $(TEMPLATE) \
		$(if $(filter 1 true,$(USE_CONTAINER)),--use-container,)

## ── deploy ───────────────────────────────────────────────────
# sam deploy uses .aws-sam/build/template.yaml from the build; omitting --template keeps --config-file from resolving relative to infra/
deploy: build
	@echo ">>> SAM deploy (stage=$(STAGE), sha=$(GIT_SHA))"
	sam deploy \
		--no-confirm-changeset \
		--config-file infra/samconfig.toml \
		--config-env $(CONFIG_ENV) \
		--parameter-overrides \
			Stage=$(STAGE) \
			GitSha=$(GIT_SHA) \
			AmplifyOrigin=$(AMPLIFY_ORIGIN) \
			"CorsAllowedOrigins=$(CORS_ORIGINS)" \
			GeminiSecretName=$(GEMINI_SECRET_NAME) \
			DailyAnalysisCap=$(DAILY_ANALYSIS_CAP)

## ── test ─────────────────────────────────────────────────────
test:
	@echo ">>> Running pytest unit tests"
	python -m pytest tests/unit -v --tb=short

## ── logs ─────────────────────────────────────────────────────
logs:
	@echo ">>> Fetching CloudWatch logs for /aws/lambda/epf-sentinel-health-$(STAGE)"
	sam logs \
		--cw-log-group /aws/lambda/epf-sentinel-health-$(STAGE) \
		--region $(REGION) \
		-s "10mins ago" \
		$(if $(filter 1 true,$(TAIL)),--tail,)
