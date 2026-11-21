#!/usr/bin/env bash
# =============================================================================
# Antigravity Skills & MCP Configuration Verification Helper
# =============================================================================
# Verifies all 8 workshop skills under `.agents/skills/`, the always-on rule
# `.agents/rules/spanner-graph-workshop.md`, and `.antigravity/mcp_config.json`
# (plus `.agents/mcp_config.json` when present or linked via `--link-legacy`).
#
# Usage:
#   bash scripts/setup_antigravity_skills.sh
#   bash scripts/setup_antigravity_skills.sh --link-legacy
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
LINK_LEGACY="false"

for arg in "$@"; do
  case "${arg}" in
    --link-legacy) LINK_LEGACY="true" ;;
    -h|--help)
      echo "Usage: bash scripts/setup_antigravity_skills.sh [--link-legacy]"
      echo "  Verifies all 8 Antigravity skills under .agents/skills/ and MCP config."
      echo "  --link-legacy  Create optional symlinks (.agents/mcp_config.json -> ../.antigravity/mcp_config.json"
      echo "                 and .agent/skills -> ../.agents/skills) for legacy IDE discovery paths."
      exit 0
      ;;
    *)
      echo "[ERROR] Unknown argument: ${arg}" >&2
      exit 1
      ;;
  esac
done

if [[ "${LINK_LEGACY}" == "true" ]]; then
  echo "[LINK-LEGACY] Creating optional legacy compatibility symlinks..."
  if [[ ! -e "${REPO_ROOT}/.agents/mcp_config.json" ]]; then
    ln -s "../.antigravity/mcp_config.json" "${REPO_ROOT}/.agents/mcp_config.json"
    echo "  [OK] Linked .agents/mcp_config.json -> ../.antigravity/mcp_config.json"
  else
    echo "  [SKIP] .agents/mcp_config.json already exists."
  fi
  mkdir -p "${REPO_ROOT}/.agent"
  if [[ ! -e "${REPO_ROOT}/.agent/skills" ]]; then
    ln -s "../.agents/skills" "${REPO_ROOT}/.agent/skills"
    echo "  [OK] Linked .agent/skills -> ../.agents/skills"
  else
    echo "  [SKIP] .agent/skills already exists."
  fi
fi

python3 - "${REPO_ROOT}" <<'PYEOF'
import json
import re
import sys
from pathlib import Path

repo_root = Path(sys.argv[1])
spec_path = repo_root / "WORKSHOP_SPEC.json"
spec = json.loads(spec_path.read_text(encoding="utf-8"))

errors: list[str] = []
warnings: list[str] = []

print("==============================================================================")
print("Antigravity IDE Skills & MCP Configuration Audit")
print(f"  Workspace : {repo_root}")
print("==============================================================================")

# 1. Verify always-on rule file
rule_path = repo_root / ".agents" / "rules" / "spanner-graph-workshop.md"
if rule_path.is_file():
    rule_text = rule_path.read_text(encoding="utf-8")
    if "trigger: always_on" not in rule_text:
        errors.append(f"{rule_path.relative_to(repo_root)} missing 'trigger: always_on' frontmatter")
    else:
        print(f"[PASS] Always-on rule verified: {rule_path.relative_to(repo_root)}")
else:
    errors.append("Missing always-on rule: .agents/rules/spanner-graph-workshop.md")

# 2. Verify all 8 skills in WORKSHOP_SPEC.json
manifest = spec.get("skills_manifest", [])
if len(manifest) != 8:
    errors.append(f"Expected 8 skills in WORKSHOP_SPEC.json skills_manifest, found {len(manifest)}")

skills_dir = repo_root / ".agents" / "skills"
actual_dirs = sorted(p.name for p in skills_dir.iterdir() if p.is_dir()) if skills_dir.is_dir() else []
expected_names = sorted(s["name"] for s in manifest)
if actual_dirs != expected_names:
    errors.append(f"Skill directory mismatch under .agents/skills/: actual={actual_dirs}, expected={expected_names}")

for skill in manifest:
    sname = skill["name"]
    sdir = repo_root / skill["path"]
    skill_md = sdir / "SKILL.md"
    if not skill_md.is_file():
        errors.append(f"Missing SKILL.md for skill '{sname}' at {skill_md}")
        continue

    content = skill_md.read_text(encoding="utf-8")
    fm_match = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
    if not fm_match:
        errors.append(f"Skill '{sname}' SKILL.md missing YAML frontmatter")
    else:
        fm = fm_match.group(1)
        if f"name: {sname}" not in fm:
            errors.append(f"Skill '{sname}' frontmatter name does not match directory name")

    missing_refs = []
    for ref_file in skill.get("references", []):
        if not (sdir / "references" / ref_file).is_file():
            missing_refs.append(ref_file)
    if missing_refs:
        errors.append(f"Skill '{sname}' missing reference files: {missing_refs}")
    else:
        scope = "Production (reusable)" if skill.get("is_production_reusable") else "Workshop guide"
        print(
            f"[PASS] Skill '{sname}' ({scope}) — SKILL.md + "
            f"{len(skill.get('references', []))} references verified"
        )

# 3. Verify MCP config (.antigravity/mcp_config.json and optional .agents/mcp_config.json)
canonical_mcp = repo_root / ".antigravity" / "mcp_config.json"
agents_mcp = repo_root / ".agents" / "mcp_config.json"
configs_to_check = []
if canonical_mcp.is_file():
    configs_to_check.append(canonical_mcp)
else:
    errors.append("Missing canonical Antigravity MCP config: .antigravity/mcp_config.json")

if agents_mcp.is_file():
    configs_to_check.append(agents_mcp)
else:
    warnings.append(
        ".agents/mcp_config.json not present (canonical config is .antigravity/mcp_config.json; "
        "run with --link-legacy if your client reads .agents/mcp_config.json)"
    )

secret_patterns = (
    re.compile(r"AIza[0-9A-Za-z\-_]{20,}"),
    re.compile(r"ya29\.[0-9A-Za-z\-_]+"),
    re.compile(r"GOCSPX-[0-9A-Za-z\-_]+"),
)

for cfg_path in configs_to_check:
    rel = cfg_path.relative_to(repo_root)
    raw = cfg_path.read_text(encoding="utf-8")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        errors.append(f"{rel} is invalid JSON: {exc}")
        continue

    servers = parsed.get("mcpServers")
    if not isinstance(servers, dict) or not servers:
        errors.append(f"{rel} missing non-empty top-level 'mcpServers' object")
        continue

    for pat in secret_patterns:
        if pat.search(raw):
            errors.append(f"{rel} appears to contain a hardcoded secret matching {pat.pattern}")

    for srv_name, srv_cfg in servers.items():
        env = srv_cfg.get("env", {}) if isinstance(srv_cfg, dict) else {}
        vloc = str(env.get("VERTEX_AI_LOCATION", "")).strip().lower()
        if vloc == "global":
            errors.append(f"{rel} server '{srv_name}' sets forbidden VERTEX_AI_LOCATION=global")

    print(f"[PASS] MCP config verified: {rel} ({len(servers)} server profile(s), zero secrets)")

if warnings:
    print("")
    for w in warnings:
        print(f"[INFO] {w}")

print("------------------------------------------------------------------------------")
if errors:
    for err in errors:
        print(f"[FAIL] {err}", file=sys.stderr)
    sys.exit(1)

print("[OK] All 8 Antigravity skills, workspace rules, and MCP configurations verified.")
PYEOF
