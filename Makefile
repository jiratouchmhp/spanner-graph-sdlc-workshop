# =============================================================================
# Cloud Spanner Enterprise SDLC Knowledge Graph & GraphRAG Workshop — Makefile
# =============================================================================
# All offline verification and unit test targets use Python 3 standard library
# (`python3 -m unittest`) — zero external pytest dependency required.
#
# Examples:
#   make help
#   make test
#   make validate-skills
#   make verify-starter-stubs
#   make TRACK=starter verify-lab LAB=1
#   make TRACK=solution verify-lab LAB=all
#   make sizing PRESET=all REGION=asia-southeast3
#   make sizing WORKSHEET=starter/tools/customer_scale_worksheet.json
# =============================================================================

SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

PYTHON ?= python3
TRACK ?= solution
LAB ?= all
PRESET ?= all
REGION ?= asia-southeast3
DR_BACKUP_REGION ?=
WORKSHEET ?=
SPANNER_PROCESSING_UNITS ?= 100

# Safely load `.env` when present without overriding explicit shell variables
LOAD_ENV = set -a; [ -f .env ] && source .env; set +a

.PHONY: help install test validate-skills verify-starter-stubs verify-lab \
        sizing dry-run-pipelines demo-mcp-mock provision-gcp cleanup-gcp

help: ## Print available workshop targets, variables, and quickstart examples
	@echo "Cloud Spanner Enterprise SDLC Knowledge Graph & GraphRAG Workshop"
	@echo ""
	@echo "Usage: make <target> [TRACK=starter|solution] [LAB=1..8|all] [PRESET=all|workshop|small|medium|large] [REGION=asia-southeast3|asia-southeast1|us-central1]"
	@echo "Defaults: TRACK=$(TRACK) | LAB=$(LAB) | PRESET=$(PRESET) | REGION=$(REGION)"
	@echo ""
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z0-9_-]+:.*## / {printf "  \033[36m%-24s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo ""
	@echo "Quickstart Examples:"
	@echo "  make test"
	@echo "  make validate-skills"
	@echo "  make verify-starter-stubs"
	@echo "  make TRACK=starter verify-lab LAB=1"
	@echo "  make TRACK=solution verify-lab LAB=all"
	@echo "  make sizing PRESET=all REGION=asia-southeast3 DR_BACKUP_REGION=asia-southeast1"
	@echo "  make sizing WORKSHEET=starter/tools/customer_scale_worksheet.json"
	@echo "  make dry-run-pipelines"
	@echo "  make demo-mcp-mock"

install: ## Install Python dependencies for live GCP pipelines and ADK/MCP demos
	@echo "==> Installing pipeline dependencies (solution/pipelines/requirements.txt)..."
	$(PYTHON) -m pip install -r solution/pipelines/requirements.txt
	@echo "==> Optional Lab 6 ADK/MCP live packages: $(PYTHON) -m pip install -r solution/mcp/requirements.txt"

test: ## Run all offline stdlib unittest modules under tests/ (zero pytest dependency)
	@if [ ! -d tests ]; then \
		echo "ERROR: tests/ directory not found." >&2; \
		exit 1; \
	fi
	@echo "==> Running full offline unittest suite (python3 -m unittest)..."
	$(PYTHON) -m unittest discover -s tests -p "test_*.py" -v

validate-skills: ## Validate all 8 Antigravity skills under .agents/skills/ and MCP config
	@echo "==> Auditing Antigravity skills & .antigravity/mcp_config.json..."
	bash scripts/setup_antigravity_skills.sh
	@if [ -f tests/test_antigravity_skills.py ]; then \
		echo "==> Running unittest skill validation (tests/test_antigravity_skills.py)..."; \
		$(PYTHON) -m unittest tests/test_antigravity_skills.py -v; \
	fi

verify-starter-stubs: ## Confirm all 8 labs fail on unsolved starter/ stubs and pass 100% on solution/
	@echo "==> Verifying starter/ stubs are armed (expected FAIL before completion) and solution/ passes..."
	$(PYTHON) scripts/verify_lab.py --verify-starter-stubs
	@if [ -f tests/test_starter_vs_solution_parity.py ]; then \
		echo "==> Running tests/test_starter_vs_solution_parity.py..."; \
		$(PYTHON) -m unittest tests/test_starter_vs_solution_parity.py -v; \
	fi

verify-lab: ## Verify Labs 1..8 or all offline (usage: make TRACK=starter|solution verify-lab LAB=1..8|all)
	@echo "==> Verifying LAB=$(LAB) on TRACK=$(TRACK)..."
	$(PYTHON) scripts/verify_lab.py --track "$(TRACK)" --lab "$(LAB)"

sizing: ## Run verified 2026 Bangkok/Singapore sizing & 3-store TCO calculator (supports PRESET, REGION, DR_BACKUP_REGION, WORKSHEET)
	@set -eu; \
	ARGS=""; \
	if [ -n "$(DR_BACKUP_REGION)" ]; then \
		ARGS="$$ARGS --dr-backup-region $(DR_BACKUP_REGION)"; \
	fi; \
	if [ -n "$(WORKSHEET)" ]; then \
		if [ "$(origin REGION)" = "command line" ]; then \
			ARGS="--region $(REGION) $$ARGS"; \
		fi; \
		echo "==> Running sizing calculator with worksheet $(WORKSHEET)..."; \
		$(PYTHON) solution/tools/sizing_calculator.py --worksheet "$(WORKSHEET)" $$ARGS; \
	else \
		echo "==> Running sizing calculator (PRESET=$(PRESET), REGION=$(REGION))..."; \
		$(PYTHON) solution/tools/sizing_calculator.py --preset "$(PRESET)" --region "$(REGION)" $$ARGS; \
	fi

dry-run-pipelines: ## Dry-run Path A loader, Path B GraphRAG ingest, post-load orchestrator & Hero offline replay (stdlib only)
	@echo "==> [1/5] Validating Path A Neo4j APOC staging manifest..."
	$(PYTHON) data/structured/stage_apoc_export.py
	@echo "==> [2/5] Dry-running Path A Spanner loader (nodes -> edges)..."
	$(PYTHON) solution/pipelines/neo4j_to_spanner_loader.py --dry-run
	@echo "==> [3/5] Dry-running Path B Markdown GraphRAG ingest (45 docs, asia-southeast1 embeddings)..."
	$(PYTHON) solution/pipelines/unstructured_graphrag_ingest.py --dry-run
	@echo "==> [4/5] Dry-running 4-stage post-load orchestrator (03a -> 03b -> 03 -> ANALYZE)..."
	$(PYTHON) solution/pipelines/apply_postload.py --dry-run --strict
	@echo "==> [5/5] Replaying Hero API blast-radius offline against 7-row fixture..."
	$(PYTHON) labs/run_hero_query.py --dry-run --query B --check-fixture solution/sql/fixtures/07_hero_expected_rows.json

demo-mcp-mock: ## Run Lab 6 3-persona RBAC & prompt-injection fail-closed demo offline (zero GCP creds required)
	@echo "==> Running Lab 6 multi-agent RBAC & prompt-injection fail-closed demo (--mock)..."
	$(PYTHON) solution/mcp/adk_multi_agent_demo.py --mock

provision-gcp: ## [LIVE GCP] Provision Bangkok 100-PU Enterprise sandbox (override REGION=asia-southeast1 for fallback)
	@$(LOAD_ENV); \
	echo "==> Provisioning Cloud Spanner Enterprise sandbox in $(REGION) ($(SPANNER_PROCESSING_UNITS) PU)..."; \
	bash scripts/provision_workshop_env.sh create --region "$(REGION)" --processing-units "$(SPANNER_PROCESSING_UNITS)"

cleanup-gcp: ## [LIVE GCP] Delete workshop Cloud Spanner instance, database, and DR backup copies cleanly
	@$(LOAD_ENV); \
	echo "==> Cleaning up Cloud Spanner workshop environment..."; \
	bash scripts/provision_workshop_env.sh cleanup --region "$(REGION)"
