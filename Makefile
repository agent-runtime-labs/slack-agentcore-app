UV := uv run --quiet --no-project --python 3.13
DREAM := npx --yes dream-machine@0.1.2
ENV ?= dev

.PHONY: help up down test tf-validate identity identity-github local-workload deploy outputs \
	dream-check dream-compile dream-ledger

help: ## Show targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

up: ## Start local stack with Tilt
	tilt up

down: ## Stop local stack
	tilt down

test: ## Run all unit tests
	$(UV) --with-requirements backends/lambdas/requirements-dev.txt pytest -q backends/lambdas/tests
	$(UV) --with-requirements backends/agents/slack_agent/requirements-dev.txt pytest -q backends/agents/slack_agent/tests
	$(UV) --with pytest pytest -q scripts/tests

tf-validate: ## terraform fmt check + validate
	terraform fmt -check -recursive infra-as-code
	./infra-as-code/tf-wrapper.sh $(ENV) validate

identity: ## Create/update the LinkedIn credential provider (needs LINKEDIN_CLIENT_ID/SECRET)
	./infra-as-code/scripts/identity-setup.sh linkedin-provider

identity-github: ## Create/update the GitHub credential provider (needs GITHUB_CLIENT_ID/SECRET)
	./infra-as-code/scripts/identity-setup.sh github-provider

local-workload: ## Create/update the workload identity used by Tilt
	./infra-as-code/scripts/identity-setup.sh local-workload

deploy: ## terraform init + apply for ENV (default dev)
	./infra-as-code/tf-wrapper.sh $(ENV) init
	./infra-as-code/tf-wrapper.sh $(ENV) apply

outputs: ## Show deployed URLs and ARNs
	./infra-as-code/tf-wrapper.sh $(ENV) output

dream-check: ## Dream Machine: validate config + ledger, show tonight's focus (offline)
	$(UV) python scripts/dream_check.py

dream-compile: ## Dream Machine: compile dream.config.json into the nightly prompt (.dream/PROMPT.md)
	mkdir -p .dream
	$(DREAM) compile dream.config.json --out .dream/PROMPT.md
	@echo "Wrote .dream/PROMPT.md"

dream-ledger: ## Dream Machine: verify the ledger and print its learning signals
	$(DREAM) ledger verify --path docs/dream-cycle/LEDGER.md
	$(DREAM) ledger signals --path docs/dream-cycle/LEDGER.md
