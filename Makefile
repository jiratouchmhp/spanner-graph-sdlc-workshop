# =============================================================================
# Cloud Spanner Enterprise SDLC Knowledge Graph & GraphRAG Workshop — Makefile
# =============================================================================
# All offline verification and unit test targets use Python 3 standard library
# (`python3 -m unittest`) — zero external pytest dependency required.
#
# Examples:
#   make help
#   make preflight
#   make install
#   make install-mcp
#   make test
#   make validate-skills
#   make verify-starter-stubs
#   make lab1-no-docker
#   make TRACK=starter verify-lab LAB=1
#   make TRACK=starter MODE=express verify-lab LAB=1
#   make TRACK=solution verify-lab LAB=all
#   make sizing PRESET=all REGION=asia-southeast3
#   make sizing WORKSHEET=starter/tools/customer_scale_worksheet.json
# =============================================================================

SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

SYSTEM_PYTHON ?= python3
VENV_DIR ?= .venv
VENV_PYTHON := $(VENV_DIR)/bin/python
# Auto-prefer .venv/bin/python when executable and populated (pyyaml present), else python3 (overrideable via PYTHON=...)
PYTHON ?= $(if $(wildcard $(VENV_PYTHON)),$(if $(shell test -x "$(VENV_PYTHON)" && "$(VENV_PYTHON)" -c "import yaml" 2>/dev/null && echo yes),$(VENV_PYTHON),$(SYSTEM_PYTHON)),$(SYSTEM_PYTHON))

TRACK ?= solution
LAB ?= all
MODE ?= full
PRESET ?= all
REGION ?= asia-southeast3
VERTEX_AI_LOCATION ?= asia-southeast1
WORKSHEET ?=
SPANNER_PROCESSING_UNITS ?= 100
STRICT_GCP ?=
DRY_RUN ?=

.PHONY: help preflight install install-mcp test validate-skills \
        verify-starter-stubs verify-lab lab1-no-docker sizing \
        dry-run-pipelines demo-mcp-mock provision-gcp verify-live \
        cleanup-gcp cleanup-all-gcp

help: ## Print available workshop targets, variables, and quickstart examples
	@echo "Cloud Spanner Enterprise SDLC Knowledge Graph & GraphRAG Workshop"
	@echo "Architecture: Bangkok (asia-southeast3) single-region primary (Spanner, Cloud Run, Secrets, GCS, backups)"
	@echo "              + Singapore (asia-southeast1) ONLY for Vertex AI, Model Armor & Cloud DLP (unavailable in Bangkok)"
	@echo ""
	@echo "Usage: make <target> [TRACK=starter|solution] [LAB=1..8|all] [MODE=full|express] [PRESET=all|workshop|small|medium|large] [REGION=asia-southeast3]"
	@echo "Defaults: PYTHON=$(PYTHON) | TRACK=$(TRACK) | LAB=$(LAB) | MODE=$(MODE) | PRESET=$(PRESET) | REGION=$(REGION) | VERTEX_AI_LOCATION=$(VERTEX_AI_LOCATION)"
	@echo ""
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z0-9_-]+:.*## / {printf "  \033[36m%-24s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo ""
	@echo "Quickstart Examples:"
	@echo "  make preflight"
	@echo "  make install"
	@echo "  make install-mcp"
	@echo "  make test"
	@echo "  make validate-skills"
	@echo "  make verify-starter-stubs"
	@echo "  make lab1-no-docker"
	@echo "  make TRACK=starter verify-lab LAB=1"
	@echo "  make TRACK=starter MODE=express verify-lab LAB=1"
	@echo "  make TRACK=solution verify-lab LAB=all"
	@echo "  make sizing PRESET=all REGION=asia-southeast3"
	@echo "  make sizing WORKSHEET=starter/tools/customer_scale_worksheet.json"
	@echo "  make dry-run-pipelines"
	@echo "  make demo-mcp-mock"
	@echo "  make provision-gcp TRACK=solution"
	@echo "  make verify-live"
	@echo "  make cleanup-all-gcp"

preflight: ## Run fast 5-second attendee readiness check (pass STRICT_GCP=1 for live GCP gate)
	@bash scripts/preflight_check.sh $(if $(filter 1 true TRUE yes YES,$(STRICT_GCP)),--strict-gcp,)

install: ## Create PEP-668-safe .venv and install pipeline dependencies (solution/pipelines/requirements.txt)
	@if [ ! -x "$(VENV_PYTHON)" ]; then \
		echo "==> Creating PEP-668-safe virtualenv at $(VENV_DIR) using $(SYSTEM_PYTHON)..."; \
		$(SYSTEM_PYTHON) -m venv "$(VENV_DIR)" 2>/dev/null || $(SYSTEM_PYTHON) -m venv --without-pip "$(VENV_DIR)"; \
	fi
	@if ! "$(VENV_PYTHON)" -m pip --version >/dev/null 2>&1; then \
		echo "==> Bootstrapping pip inside $(VENV_DIR) (system Python lacks ensurepip)..."; \
		if command -v uv >/dev/null 2>&1; then \
			uv venv --seed "$(VENV_DIR)"; \
		else \
			TMP_GETPIP="$$(mktemp -t get-pip-XXXXXX.py)"; \
			trap 'rm -f "$$TMP_GETPIP"' EXIT; \
			curl -fsSL https://bootstrap.pypa.io/get-pip.py -o "$$TMP_GETPIP"; \
			"$(VENV_PYTHON)" "$$TMP_GETPIP"; \
			rm -f "$$TMP_GETPIP"; \
		fi; \
	fi
	@echo "==> Upgrading pip inside $(VENV_DIR)..."
	"$(VENV_PYTHON)" -m pip install --upgrade pip
	@echo "==> Installing pipeline dependencies (solution/pipelines/requirements.txt) into $(VENV_DIR)..."
	"$(VENV_PYTHON)" -m pip install -r solution/pipelines/requirements.txt
	@echo "==> Done! 'make' targets auto-detect $(VENV_PYTHON) (or run: source $(VENV_DIR)/bin/activate)."
	@echo "==> Optional Lab 6 ADK/MCP live packages: make install-mcp"

install-mcp: ## Install Lab 6 ADK/MCP & Model Armor dependencies (solution/mcp/requirements.txt) inside .venv
	@if [ ! -x "$(VENV_PYTHON)" ] || ! "$(VENV_PYTHON)" -m pip --version >/dev/null 2>&1; then \
		$(MAKE) install; \
	fi
	@echo "==> Installing Lab 6 ADK/MCP dependencies (solution/mcp/requirements.txt) into $(VENV_DIR)..."
	"$(VENV_PYTHON)" -m pip install -r solution/mcp/requirements.txt
	@echo "==> Done! Lab 6 MCP/ADK packages installed inside $(VENV_DIR)."

test: ## Run all offline stdlib unittest modules under tests/ (zero pytest dependency)
	@if [ ! -d tests ]; then \
		echo "ERROR: tests/ directory not found." >&2; \
		exit 1; \
	fi
	@echo "==> Running full offline unittest suite ($(PYTHON) -m unittest)..."
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

verify-lab: ## Verify Labs 1..8 or all offline (usage: make TRACK=starter|solution [MODE=full|express] verify-lab LAB=1..8|all)
	@echo "==> Verifying LAB=$(LAB) on TRACK=$(TRACK) (MODE=$(MODE))..."
	$(PYTHON) scripts/verify_lab.py --track "$(TRACK)" --lab "$(LAB)" --mode "$(MODE)"

lab1-no-docker: ## Run Zero-Docker Lab 1 Neo4j CE & APOC streaming simulator, parity diff & ISO GQL check
	@echo "==> [1/2] Running Zero-Docker Neo4j CE & APOC streaming export simulator..."
	@if [ -f data/structured/no_docker_neo4j_sim.py ]; then \
		$(PYTHON) data/structured/no_docker_neo4j_sim.py --demo; \
	else \
		$(PYTHON) data/structured/stage_apoc_export.py; \
	fi
	@if [ "$(origin TRACK)" = "command line" ]; then \
		echo "==> [2/2] Verifying Lab 1 openCypher -> ISO GQL cookbook (TRACK=$(TRACK), MODE=$(MODE))..."; \
		$(PYTHON) scripts/verify_lab.py --track "$(TRACK)" --lab 1 --mode "$(MODE)"; \
	elif [ -f solution/sql/09_opencypher_to_gql_cookbook.sql ]; then \
		echo "==> [2/2] Verifying reference Lab 1 openCypher -> ISO GQL cookbook (TRACK=solution)..."; \
		$(PYTHON) scripts/verify_lab.py --track solution --lab 1 --mode "$(MODE)"; \
		echo "Next (attendees): complete TODO(Lab 1) in starter/sql/09_opencypher_to_gql_cookbook.sql, then run:"; \
		echo "  make TRACK=starter verify-lab LAB=1          # Full Track B (all 5 queries)"; \
		echo "  make TRACK=starter MODE=express verify-lab LAB=1  # Express Track A (queries 1-3)"; \
	else \
		echo "==> [2/2] Starter-only workspace detected (solution/ absent) — simulator & APOC parity verified!"; \
		echo "Next: complete TODO(Lab 1) in starter/sql/09_opencypher_to_gql_cookbook.sql, then run:"; \
		echo "  make TRACK=starter verify-lab LAB=1          # Full Track B (all 5 queries)"; \
		echo "  make TRACK=starter MODE=express verify-lab LAB=1  # Express Track A (queries 1-3)"; \
	fi

sizing: ## Run verified 2026 Bangkok single-region sizing & 3-store TCO calculator (supports PRESET, REGION, WORKSHEET; $0 cross-region egress)
	@set -eu; \
	ARGS=""; \
	if [ -n "$(WORKSHEET)" ]; then \
		if [ "$(origin REGION)" = "command line" ]; then \
			ARGS="--region $(REGION)"; \
		fi; \
		echo "==> Running sizing calculator with worksheet $(WORKSHEET)..."; \
		$(PYTHON) solution/tools/sizing_calculator.py --worksheet "$(WORKSHEET)" $$ARGS; \
	else \
		echo "==> Running sizing calculator (PRESET=$(PRESET), REGION=$(REGION), single-region backups)..."; \
		$(PYTHON) solution/tools/sizing_calculator.py --preset "$(PRESET)" --region "$(REGION)"; \
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

provision-gcp: ## [LIVE GCP] Provision Bangkok (asia-southeast3) 100-PU Enterprise sandbox (supports TRACK=starter|solution; Vertex AI in asia-southeast1)
	@echo "==> Provisioning Cloud Spanner Enterprise sandbox in $(REGION) ($(SPANNER_PROCESSING_UNITS) PU, TRACK=$(TRACK), Vertex AI=$(VERTEX_AI_LOCATION))..."; \
	bash scripts/provision_workshop_env.sh create --region "$(REGION)" --vertex-location "$(VERTEX_AI_LOCATION)" --processing-units "$(SPANNER_PROCESSING_UNITS)" --track "$(TRACK)" $(if $(filter 1 true TRUE yes YES,$(DRY_RUN)),--dry-run,)

verify-live: ## [LIVE GCP] Run end-to-end live Cloud Spanner verification (DDL, ScaNN PLAN, Hero Query B, FGAC, Bangkok residency & Vertex AI)
	@echo "==> Running live Cloud Spanner verification suite in $(REGION) (Vertex AI=$(VERTEX_AI_LOCATION))..."; \
	bash scripts/verify_live_gcp.sh --region "$(REGION)" --vertex-location "$(VERTEX_AI_LOCATION)" $(if $(filter 1 true TRUE yes YES,$(DRY_RUN)),--dry-run,)

cleanup-gcp: ## [LIVE GCP] Delete workshop Bangkok Cloud Spanner instance, database, and same-region backups cleanly
	@echo "==> Cleaning up Cloud Spanner workshop environment in $(REGION)..."; \
	bash scripts/provision_workshop_env.sh cleanup --region "$(REGION)" $(if $(filter 1 true TRUE yes YES,$(DRY_RUN)),--dry-run,)

cleanup-all-gcp: ## [LIVE GCP] Full teardown (Bangkok Spanner/Cloud Run/Secrets/GCS, SAs, Singapore DLP/Model Armor) + zero-residual audit
	@echo "==> Running full workshop GCP teardown (--all) and zero-residual verification..."; \
	bash scripts/provision_workshop_env.sh cleanup --all --region "$(REGION)" $(if $(filter 1 true TRUE yes YES,$(DRY_RUN)),--dry-run,)
