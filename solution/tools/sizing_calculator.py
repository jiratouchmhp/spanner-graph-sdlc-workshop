#!/usr/bin/env python3
"""Enterprise Sizing & 3-Store Cost TCO Calculator for Cloud Spanner Enterprise.

100% Python standard library (zero third-party dependencies).

Models unified multi-model Cloud Spanner Enterprise deployments anchored on
Bangkok (`regional-asia-southeast3`, single-region 3-zone synchronous quorum,
99.99% SLA, Thailand PDPA data residency) + Singapore (`asia-southeast1`) ONLY
for companion AI/Security services unavailable in Bangkok (Vertex AI, Model
Armor, Cloud DLP), combining:
  - Spanner Graph (ISO GQL + reverse covering STORING indexes)
  - Full-Text Search (TOKENLIST + Search Index)
  - ScaNN Vector Search (768-dim ARRAY<FLOAT32> embeddings + ScaNN Vector Index)
  - Tiered Storage Locality Groups (SSD -> HDD 5x storage savings)
  - Serverless Data Boost (Graph Algorithms & analytical exports)
  - Same-region Bangkok backups/PITR ($0.0315/GiB-mo, $0.00/mo cross-region egress;
    optional --dr-backup-region override available for custom DR modeling)

Compares verified 2026 regional Cloud Spanner pricing (On-Demand, 1-Yr CUD,
3-Yr CUD) against a legacy 3-store architecture (Neo4j Enterprise HA +
Managed Vector DB HA + Managed OpenSearch/Elasticsearch HA + CDC/ETL sync
pipelines & multi-store SRE overhead).
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
from pathlib import Path
import sys
from typing import Any, Dict, List, Mapping, Optional, Tuple

# ---------------------------------------------------------------------------
# Constants & Verified Live 2026 Cloud Spanner Regional Pricing (USD)
# Source: https://cloud.google.com/spanner/pricing (verified 2026)
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "1.0.0"
HOURS_PER_MONTH = 730.0
BYTES_PER_KIB = 1024.0
BYTES_PER_GIB = float(1024 ** 3)
GIB_PER_TIB = 1024.0

# Capacity & failover thresholds (Cloud Spanner regional 3-zone quorum)
REGIONAL_HP_CPU_TARGET_PCT = 65.0
STORAGE_RECOMMENDED_TIB_PER_NODE = 7.5  # 75% alert threshold of 10 TiB/node
STORAGE_HARD_LIMIT_TIB_PER_NODE = 10.0
SUBNODE_STORAGE_LIMIT_TIB_PER_100PU = 1.0  # 1,024 GiB per 100 PU (< 1 node)
PRODUCTION_MIN_PU = 1000  # 1 node minimum for production & Managed Autoscaler
PU_PER_NODE = 1000
DEFAULT_WORKSHOP_HOURS = 8.0

# Official verified regional peak single-row 1 KiB SSD/HDD benchmarks (100% CPU)
REGIONAL_PEAK_SSD_READS_PER_NODE = 22_500.0
REGIONAL_PEAK_SSD_WRITES_STD_PER_NODE = 3_500.0
REGIONAL_PEAK_SSD_WRITES_BATCHED_PER_NODE = 22_500.0  # max_commit_delay=100ms
REGIONAL_PEAK_HDD_READS_PER_NODE = 1_500.0
REGIONAL_RO_REPLICA_PEAK_SSD_READS_PER_NODE = 7_500.0

# Multi-model GraphRAG workload complexity equivalents vs. 1 KiB KV ops:
# - 1 hybrid GraphRAG read (multi-hop MATCH TRAIL + FTS SEARCH + ScaNN ANN + RRF)
#   ~= 50 single-row 1 KiB read equivalents (450 peak / 292.5 @ 65% HP CPU per node)
# - 1 multi-model write (base row + reverse covering STORING index + FTS TOKENLIST
#   + FLOAT32 embedding + ScaNN index update)
#   ~= 12 single-row 1 KiB write equivalents (291.7 peak / 189.6 @ 65% HP CPU per node)
GRAPHRAG_READ_OP_EQUIVALENT = 50.0
GRAPHRAG_WRITE_OP_EQUIVALENT = 12.0
COLD_HDD_READ_TRAFFIC_SHARE = 0.05  # Cold archive receives ~5% of read ratio

# Storage sizing multipliers (multi-model Graph + FTS + ScaNN Vector)
AVG_NODE_ROW_BYTES = 1024  # 1 KiB/node (PK, attributes, JSON metadata, overhead)
AVG_EDGE_ROW_BYTES = 256  # 256 B/edge (composite PK, edge metadata, overhead)
AVG_CHUNK_TEXT_BYTES = 2048  # 2 KiB/chunk (~400-500 words + metadata + mentions)
REVERSE_COVERING_INDEX_MULTIPLIER = 2.0  # ~2.0x base edge bytes (STORING index)
FTS_INDEX_MULTIPLIER = 0.5  # ~0.5x indexed text bytes (TOKENLIST + Search Index)
FLOAT32_BYTES = 4  # 768-dim FLOAT32 = 3,072 B = 3.0 KiB/chunk
SCANN_INDEX_MULTIPLIER = 1.3  # ~1.3x raw vector bytes (ScaNN quantized + tree)

# Cross-region backup copy egress (Asia inter-region transfer, e.g., BKK -> SG)
CROSS_REGION_BACKUP_EGRESS_ASIA_PER_GIB_USD = 0.08
DR_BACKUP_MONTHLY_DELTA_RATIO = 0.15  # Estimated 15%/mo changed backup delta copy

# Verified 2026 live Cloud Spanner pricing schedule (USD, 730 hrs/month)
PRICING_REGIONS: Dict[str, Dict[str, Any]] = {
    "asia-southeast3": {
        "region": "asia-southeast3",
        "city": "Bangkok, Thailand",
        "spanner_instance_config": "regional-asia-southeast3",
        "sla": "99.99%",
        "zones": ["asia-southeast3-a", "asia-southeast3-b", "asia-southeast3-c"],
        "regional_multiplier_vs_us_central1": 1.05,
        "compute_per_node_hour_usd": {
            "standard": 0.945,
            "enterprise": 1.2915,
            "enterprise_plus": 1.7955,
        },
        "compute_per_100pu_hour_usd": {
            "standard": 0.0945,
            "enterprise": 0.12915,
            "enterprise_plus": 0.17955,
        },
        "compute_per_node_month_usd": {
            "standard": 689.85,
            "enterprise": 942.795,
            "enterprise_plus": 1310.715,
        },
        "compute_cud_1yr_per_node_hour_usd": {
            "standard": 0.756,
            "enterprise": 1.0332,
            "enterprise_plus": 1.4364,
        },
        "compute_cud_3yr_per_node_hour_usd": {
            "standard": 0.567,
            "enterprise": 0.7749,
            "enterprise_plus": 1.0773,
        },
        "additional_ro_replica_per_node_hour_usd": {
            "enterprise": 0.4305,
            "enterprise_plus": 0.5985,
        },
        "storage_per_gib_month_usd": {
            "ssd": 0.315,
            "hdd": 0.063,
            "backup": 0.0315,
        },
        "data_boost_per_1000_spu_hour_usd": 1.2285,
        "intra_region_replication_per_gib_usd": 0.0,
        "workshop_100pu_8hr_compute_usd": 1.0332,
        "residency_note": (
            "Single-region Bangkok (regional-asia-southeast3) 3-zone synchronous quorum "
            "(99.99% SLA, Thailand PDPA data residency, same-region backups at "
            "$0.0315/GiB-mo, $0.00/mo cross-region egress). Singapore (asia-southeast1) "
            "is used ONLY for Vertex AI, Model Armor & Cloud DLP (unavailable in Bangkok)."
        ),
    },
    "asia-southeast1": {
        "region": "asia-southeast1",
        "city": "Singapore",
        "spanner_instance_config": "regional-asia-southeast1",
        "sla": "99.99%",
        "zones": ["asia-southeast1-a", "asia-southeast1-b", "asia-southeast1-c"],
        "regional_multiplier_vs_us_central1": 1.144,
        "compute_per_node_hour_usd": {
            "standard": 1.0296,
            "enterprise": 1.40712,
            "enterprise_plus": 1.95624,
        },
        "compute_per_100pu_hour_usd": {
            "standard": 0.10296,
            "enterprise": 0.140712,
            "enterprise_plus": 0.195624,
        },
        "compute_per_node_month_usd": {
            "standard": 751.608,
            "enterprise": 1027.1976,
            "enterprise_plus": 1428.0552,
        },
        "compute_cud_1yr_per_node_hour_usd": {
            "standard": 0.82368,
            "enterprise": 1.125696,
            "enterprise_plus": 1.564992,
        },
        "compute_cud_3yr_per_node_hour_usd": {
            "standard": 0.61776,
            "enterprise": 0.844272,
            "enterprise_plus": 1.173744,
        },
        "additional_ro_replica_per_node_hour_usd": {
            "enterprise": 0.46904,
            "enterprise_plus": 0.65208,
        },
        "storage_per_gib_month_usd": {
            "ssd": 0.3432,
            "hdd": 0.06864,
            "backup": 0.03432,
        },
        "data_boost_per_1000_spu_hour_usd": 1.33848,
        "intra_region_replication_per_gib_usd": 0.0,
        "workshop_100pu_8hr_compute_usd": 1.125696,
        "residency_note": (
            "Companion region in Singapore (~25 ms RTT from Bangkok) used ONLY for "
            "Vertex AI, Model Armor, and Cloud DLP (unavailable in Bangkok). Spanner "
            "regional pricing is retained solely for rate comparison or optional override."
        ),
    },
    "us-central1": {
        "region": "us-central1",
        "city": "Iowa, US",
        "spanner_instance_config": "regional-us-central1",
        "sla": "99.99%",
        "zones": ["us-central1-a", "us-central1-b", "us-central1-c"],
        "regional_multiplier_vs_us_central1": 1.00,
        "compute_per_node_hour_usd": {
            "standard": 0.90,
            "enterprise": 1.23,
            "enterprise_plus": 1.71,
        },
        "compute_per_100pu_hour_usd": {
            "standard": 0.09,
            "enterprise": 0.123,
            "enterprise_plus": 0.171,
        },
        "compute_per_node_month_usd": {
            "standard": 657.0,
            "enterprise": 897.9,
            "enterprise_plus": 1248.3,
        },
        "compute_cud_1yr_per_node_hour_usd": {
            "standard": 0.72,
            "enterprise": 0.984,
            "enterprise_plus": 1.368,
        },
        "compute_cud_3yr_per_node_hour_usd": {
            "standard": 0.54,
            "enterprise": 0.738,
            "enterprise_plus": 1.026,
        },
        "additional_ro_replica_per_node_hour_usd": {
            "enterprise": 0.41,
            "enterprise_plus": 0.57,
        },
        "storage_per_gib_month_usd": {
            "ssd": 0.30,
            "hdd": 0.06,
            "backup": 0.03,
        },
        "data_boost_per_1000_spu_hour_usd": 1.17,
        "intra_region_replication_per_gib_usd": 0.0,
        "workshop_100pu_8hr_compute_usd": 0.984,
        "residency_note": (
            "US baseline reference region (1.00x pricing multiplier) for comparing "
            "Bangkok (1.05x) and Singapore (1.144x) regional economics."
        ),
    },
}

# Built-in T-shirt presets aligned with WORKSHOP_SPEC.json
PRESETS: Dict[str, Dict[str, Any]] = {
    "workshop": {
        "name": "workshop",
        "label": "Workshop Sandbox (Dev/Lab Only — No Managed Autoscaler)",
        "nodes": 68,
        "edges": 234,
        "chunks": 150,
        "qps": 5.0,
        "write_pct": 10.0,
        "vector_dim": 768,
        "cold_hdd_pct": 0.0,
        "processing_units_override": 100,
        "is_production_eligible": False,
        "data_boost_kspu_hrs_override": 0.05,
        "enable_primary_backup": False,  # Workshop uses --default-backup-schedule-type=NONE
    },
    "small": {
        "name": "small",
        "label": "Small Enterprise Production (1 Node / 1,000 PU Minimum)",
        "nodes": 100_000,
        "edges": 500_000,
        "chunks": 250_000,
        "qps": 50.0,
        "write_pct": 15.0,
        "vector_dim": 768,
        "cold_hdd_pct": 30.0,
        "processing_units_override": None,
        "is_production_eligible": True,
        "data_boost_kspu_hrs_override": None,
        "enable_primary_backup": True,
    },
    "medium": {
        "name": "medium",
        "label": "Medium Enterprise Production (Multi-Team Platform)",
        "nodes": 1_000_000,
        "edges": 5_000_000,
        "chunks": 2_500_000,
        "qps": 300.0,
        "write_pct": 15.0,
        "vector_dim": 768,
        "cold_hdd_pct": 40.0,
        "processing_units_override": None,
        "is_production_eligible": True,
        "data_boost_kspu_hrs_override": None,
        "enable_primary_backup": True,
    },
    "large": {
        "name": "large",
        "label": "Large Enterprise Production (Global Engineering Org)",
        "nodes": 10_000_000,
        "edges": 50_000_000,
        "chunks": 20_000_000,
        "qps": 2_000.0,
        "write_pct": 15.0,
        "vector_dim": 768,
        "cold_hdd_pct": 50.0,
        "processing_units_override": None,
        "is_production_eligible": True,
        "data_boost_kspu_hrs_override": None,
        "enable_primary_backup": True,
    },
}

PRESET_ORDER = ("workshop", "small", "medium", "large")


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkloadSpec:
    """Normalized sizing input specification."""

    name: str
    label: str
    nodes: int
    edges: int
    chunks: int
    qps: float
    write_pct: float
    vector_dim: int
    cold_hdd_pct: float
    region: str = "asia-southeast3"
    edition: str = "enterprise"
    dr_backup_region: Optional[str] = None
    ro_replicas: int = 0
    processing_units_override: Optional[int] = None
    is_production_eligible: bool = True
    data_boost_kspu_hrs_override: Optional[float] = None
    enable_primary_backup: bool = True
    workshop_hours: float = DEFAULT_WORKSHOP_HOURS
    worksheet_metadata: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Core Sizing & TCO Calculations
# ---------------------------------------------------------------------------


def validate_spec(spec: WorkloadSpec) -> None:
    """Validate workload inputs and region/edition choices."""
    if spec.region not in PRICING_REGIONS:
        raise ValueError(
            f"Unsupported region '{spec.region}'. "
            f"Choose from: {', '.join(PRICING_REGIONS.keys())}."
        )
    if spec.dr_backup_region is not None and spec.dr_backup_region not in PRICING_REGIONS:
        raise ValueError(
            f"Unsupported DR backup region '{spec.dr_backup_region}'. "
            f"Choose from: {', '.join(PRICING_REGIONS.keys())} or 'none'."
        )
    if spec.edition not in ("standard", "enterprise", "enterprise_plus"):
        raise ValueError(
            f"Unsupported edition '{spec.edition}'. "
            "Choose from: standard, enterprise, enterprise_plus."
        )
    if spec.nodes < 0 or spec.edges < 0 or spec.chunks < 0:
        raise ValueError("nodes, edges, and chunks must be non-negative integers.")
    if spec.qps < 0:
        raise ValueError("qps must be non-negative.")
    if not (0.0 <= spec.write_pct <= 100.0):
        raise ValueError("write_pct must be between 0 and 100.")
    if not (0.0 <= spec.cold_hdd_pct <= 100.0):
        raise ValueError("cold_hdd_pct must be between 0 and 100.")
    if spec.vector_dim <= 0:
        raise ValueError("vector_dim must be a positive integer.")
    if spec.ro_replicas < 0:
        raise ValueError("ro_replicas must be >= 0.")
    if spec.workshop_hours <= 0:
        raise ValueError("workshop_hours must be > 0.")
    if spec.processing_units_override is not None:
        pu = spec.processing_units_override
        if pu < 100 or (pu < 1000 and pu % 100 != 0) or (pu >= 1000 and pu % 1000 != 0):
            raise ValueError(
                "processing_units_override must be 100-900 in steps of 100, "
                "or >= 1000 in steps of 1000."
            )


def calculate_storage_breakdown(spec: WorkloadSpec) -> Dict[str, Any]:
    """Model multi-model storage footprint (Graph + FTS + Vector + ScaNN + Tiering)."""
    base_nodes_bytes = spec.nodes * AVG_NODE_ROW_BYTES
    base_edges_bytes = spec.edges * AVG_EDGE_ROW_BYTES
    base_chunks_text_bytes = spec.chunks * AVG_CHUNK_TEXT_BYTES
    base_rows_bytes = base_nodes_bytes + base_edges_bytes + base_chunks_text_bytes

    # Reverse covering STORING indexes (~2.0x base edge bytes)
    reverse_covering_index_bytes = base_edges_bytes * REVERSE_COVERING_INDEX_MULTIPLIER

    # Full-Text Search TOKENLIST + Search Index (~0.5x indexed text bytes)
    indexed_text_bytes = base_chunks_text_bytes + base_nodes_bytes
    fts_index_bytes = indexed_text_bytes * FTS_INDEX_MULTIPLIER

    # Embeddings (ARRAY<FLOAT32>) + ScaNN Vector Index (~1.3x vector bytes)
    bytes_per_embedding = spec.vector_dim * FLOAT32_BYTES
    raw_vector_bytes = spec.chunks * bytes_per_embedding
    scann_index_bytes = raw_vector_bytes * SCANN_INDEX_MULTIPLIER

    total_bytes = (
        base_rows_bytes
        + reverse_covering_index_bytes
        + fts_index_bytes
        + raw_vector_bytes
        + scann_index_bytes
    )

    total_gib = total_bytes / BYTES_PER_GIB
    total_tib = total_gib / GIB_PER_TIB

    # Tiered Storage Locality Group split (SSD -> HDD)
    hdd_fraction = spec.cold_hdd_pct / 100.0
    hdd_gib = total_gib * hdd_fraction
    ssd_gib = total_gib - hdd_gib

    return {
        "assumptions": {
            "avg_node_bytes": AVG_NODE_ROW_BYTES,
            "avg_edge_bytes": AVG_EDGE_ROW_BYTES,
            "avg_chunk_text_bytes": AVG_CHUNK_TEXT_BYTES,
            "reverse_covering_index_multiplier": REVERSE_COVERING_INDEX_MULTIPLIER,
            "fts_index_multiplier": FTS_INDEX_MULTIPLIER,
            "vector_dim": spec.vector_dim,
            "bytes_per_embedding": bytes_per_embedding,
            "kib_per_embedding": round(bytes_per_embedding / BYTES_PER_KIB, 3),
            "scann_index_multiplier": SCANN_INDEX_MULTIPLIER,
            "cold_hdd_pct": spec.cold_hdd_pct,
        },
        "components_gib": {
            "base_nodes_gib": round(base_nodes_bytes / BYTES_PER_GIB, 6),
            "base_edges_gib": round(base_edges_bytes / BYTES_PER_GIB, 6),
            "base_chunks_text_gib": round(base_chunks_text_bytes / BYTES_PER_GIB, 6),
            "base_rows_total_gib": round(base_rows_bytes / BYTES_PER_GIB, 6),
            "reverse_covering_indexes_gib": round(
                reverse_covering_index_bytes / BYTES_PER_GIB, 6
            ),
            "fts_tokenlist_and_search_index_gib": round(
                fts_index_bytes / BYTES_PER_GIB, 6
            ),
            "vector_embeddings_float32_gib": round(raw_vector_bytes / BYTES_PER_GIB, 6),
            "scann_vector_index_gib": round(scann_index_bytes / BYTES_PER_GIB, 6),
        },
        "total_gib": round(total_gib, 6),
        "total_tib": round(total_tib, 6),
        "ssd_gib": round(ssd_gib, 6),
        "hdd_gib": round(hdd_gib, 6),
        "tiered_storage_active": spec.cold_hdd_pct > 0.0,
        "locality_group_safety_note": (
            "Keep base table row-existence metadata and active indexes on SSD; "
            "assign only aged archival non-key columns (e.g., cold document bodies) "
            "to the HDD locality group to avoid the write existence-check HDD read trap."
        ),
    }


def calculate_compute_sizing(
    spec: WorkloadSpec, storage: Mapping[str, Any]
) -> Dict[str, Any]:
    """Size Cloud Spanner PUs/nodes respecting < 65% HP CPU and >= 1,000 PU prod floor."""
    write_qps = spec.qps * (spec.write_pct / 100.0)
    read_qps = spec.qps - write_qps

    # Effective per-node capacities for composite GraphRAG workload
    peak_read_qps_per_node = (
        REGIONAL_PEAK_SSD_READS_PER_NODE / GRAPHRAG_READ_OP_EQUIVALENT
    )
    peak_write_qps_per_node = (
        REGIONAL_PEAK_SSD_WRITES_STD_PER_NODE / GRAPHRAG_WRITE_OP_EQUIVALENT
    )
    hp_factor = REGIONAL_HP_CPU_TARGET_PCT / 100.0
    sustained_read_qps_per_node = peak_read_qps_per_node * hp_factor
    sustained_write_qps_per_node = peak_write_qps_per_node * hp_factor

    # Fractional nodes needed at 100% CPU vs. 65% HP CPU threshold
    raw_cpu_nodes_100pct = (read_qps / peak_read_qps_per_node) + (
        write_qps / peak_write_qps_per_node
    )
    raw_cpu_nodes_at_65pct = (read_qps / sustained_read_qps_per_node) + (
        write_qps / sustained_write_qps_per_node
    )

    # Storage-driven node floor (7.5 TiB/node = 75% alert threshold of 10 TiB hard limit)
    total_tib = float(storage["total_tib"])
    storage_nodes_required = max(
        1, math.ceil(total_tib / STORAGE_RECOMMENDED_TIB_PER_NODE)
    )

    # HDD disk-load node floor when Tiered Storage serves cold reads
    hdd_read_qps = (
        read_qps * (spec.cold_hdd_pct / 100.0) * COLD_HDD_READ_TRAFFIC_SHARE
        if spec.cold_hdd_pct > 0.0
        else 0.0
    )
    sustained_hdd_reads_per_node = REGIONAL_PEAK_HDD_READS_PER_NODE * hp_factor
    hdd_nodes_required = (
        math.ceil(hdd_read_qps / sustained_hdd_reads_per_node)
        if hdd_read_qps > 0.0
        else 0
    )

    # CPU-driven node count ensuring strictly < 65% HP CPU
    cpu_nodes_required = max(1, math.ceil(raw_cpu_nodes_at_65pct))
    while (
        spec.qps > 0
        and (raw_cpu_nodes_100pct / cpu_nodes_required) * 100.0
        >= REGIONAL_HP_CPU_TARGET_PCT
    ):
        cpu_nodes_required += 1

    # Resolve final provisioned capacity
    if spec.processing_units_override is not None:
        processing_units = spec.processing_units_override
        node_equivalent = processing_units / float(PU_PER_NODE)
        is_subnode_sandbox = processing_units < PRODUCTION_MIN_PU
        if is_subnode_sandbox:
            max_storage_tib = (
                processing_units / 100.0
            ) * SUBNODE_STORAGE_LIMIT_TIB_PER_100PU
        else:
            max_storage_tib = node_equivalent * STORAGE_HARD_LIMIT_TIB_PER_NODE
        sizing_driver = "manual_sandbox_override" if is_subnode_sandbox else "manual_override"
    else:
        nodes = max(
            cpu_nodes_required,
            storage_nodes_required,
            hdd_nodes_required,
            1,  # >= 1 node (1,000 PU) mandatory production minimum
        )
        processing_units = nodes * PU_PER_NODE
        node_equivalent = float(nodes)
        is_subnode_sandbox = False
        max_storage_tib = node_equivalent * STORAGE_HARD_LIMIT_TIB_PER_NODE

        if nodes == storage_nodes_required and storage_nodes_required > cpu_nodes_required:
            sizing_driver = "storage_75pct_threshold"
        elif nodes == hdd_nodes_required and hdd_nodes_required > cpu_nodes_required:
            sizing_driver = "hdd_disk_load_threshold"
        elif raw_cpu_nodes_at_65pct <= 1.0 and nodes == 1:
            sizing_driver = "production_1000pu_minimum"
        else:
            sizing_driver = "hp_cpu_under_65pct_threshold"

    projected_hp_cpu_pct = (
        (raw_cpu_nodes_100pct / node_equivalent) * 100.0 if node_equivalent > 0 else 0.0
    )
    storage_utilization_pct = (
        (total_tib / max_storage_tib) * 100.0 if max_storage_tib > 0 else 0.0
    )

    is_production_eligible = (
        spec.is_production_eligible
        and processing_units >= PRODUCTION_MIN_PU
        and spec.edition in ("enterprise", "enterprise_plus")
    )

    # Managed Autoscaler constraints: min >= 1 node, max <= 10x min, hp_cpu < total_cpu
    if processing_units >= PRODUCTION_MIN_PU and spec.edition in (
        "enterprise",
        "enterprise_plus",
    ):
        min_nodes = int(math.ceil(node_equivalent))
        max_nodes = min(min_nodes * 10, max(min_nodes * 3, min_nodes + 2))
        autoscaler = {
            "eligible": True,
            "min_nodes": min_nodes,
            "max_nodes": max_nodes,
            "high_priority_cpu_target_pct": 65,
            "total_cpu_target_pct": 70,
            "storage_target_pct": 75,
        }
    else:
        autoscaler = {
            "eligible": False,
            "reason": (
                "DEV/LAB ONLY: Sub-node instances (< 1,000 PUs) allocate a single server "
                "task per zone and DO NOT support Managed Autoscaler or Remote Functions. "
                "Provision >= 1,000 PUs (1 node) on Enterprise/Enterprise Plus for production."
                if processing_units < PRODUCTION_MIN_PU
                else "Managed Autoscaler requires Enterprise or Enterprise Plus edition."
            ),
        }

    # Estimate batched-write savings potential with max_commit_delay = 100ms
    batched_write_peak_per_node = (
        REGIONAL_PEAK_SSD_WRITES_BATCHED_PER_NODE / GRAPHRAG_WRITE_OP_EQUIVALENT
    )
    batched_raw_nodes_65 = (read_qps / sustained_read_qps_per_node) + (
        write_qps / (batched_write_peak_per_node * hp_factor)
    )
    batched_nodes_needed = max(1, math.ceil(batched_raw_nodes_65), storage_nodes_required)
    max_commit_delay_node_savings = (
        max(0, int(node_equivalent) - batched_nodes_needed)
        if not is_subnode_sandbox
        else 0
    )

    warnings: List[str] = []
    if is_subnode_sandbox:
        warnings.append(
            "DEV/LAB SANDBOX ONLY (< 1,000 PUs): No Managed Autoscaler, no Remote "
            "Functions, single server task/zone, non-linear tail latency under bursts."
        )
    if spec.edition == "standard":
        warnings.append(
            "Standard edition selected: Spanner Graph, Full-Text Search, Vector Search, "
            "Tiered Storage Locality Groups, and Managed Autoscaler require Enterprise "
            "or Enterprise Plus edition."
        )
    if projected_hp_cpu_pct >= REGIONAL_HP_CPU_TARGET_PCT:
        warnings.append(
            f"Projected HP CPU ({projected_hp_cpu_pct:.1f}%) meets or exceeds regional "
            f"failover threshold ({REGIONAL_HP_CPU_TARGET_PCT:.0f}%). Increase PUs/nodes."
        )

    return {
        "read_qps": round(read_qps, 2),
        "write_qps": round(write_qps, 2),
        "hdd_cold_read_qps": round(hdd_read_qps, 2),
        "processing_units": processing_units,
        "nodes_equivalent": round(node_equivalent, 2),
        "ro_replicas": spec.ro_replicas,
        "sizing_driver": sizing_driver,
        "cpu_nodes_required": cpu_nodes_required,
        "storage_nodes_required": storage_nodes_required,
        "hdd_nodes_required": hdd_nodes_required,
        "regional_hp_cpu_threshold_pct": REGIONAL_HP_CPU_TARGET_PCT,
        "projected_hp_cpu_pct": round(projected_hp_cpu_pct, 2),
        "hp_cpu_within_regional_threshold": projected_hp_cpu_pct
        < REGIONAL_HP_CPU_TARGET_PCT,
        "storage_utilization_pct": round(storage_utilization_pct, 4),
        "max_allotted_storage_tib": round(max_storage_tib, 2),
        "is_production_eligible": is_production_eligible,
        "production_min_pu_enforced": processing_units >= PRODUCTION_MIN_PU,
        "managed_autoscaler": autoscaler,
        "throughput_model": {
            "regional_peak_ssd_1kib_reads_per_node": REGIONAL_PEAK_SSD_READS_PER_NODE,
            "regional_peak_ssd_1kib_writes_per_node": REGIONAL_PEAK_SSD_WRITES_STD_PER_NODE,
            "graphrag_read_op_equivalent": GRAPHRAG_READ_OP_EQUIVALENT,
            "graphrag_write_op_equivalent": GRAPHRAG_WRITE_OP_EQUIVALENT,
            "sustained_graphrag_read_qps_per_node_at_65pct": round(
                sustained_read_qps_per_node, 2
            ),
            "sustained_graphrag_write_qps_per_node_at_65pct": round(
                sustained_write_qps_per_node, 2
            ),
            "max_commit_delay_100ms_potential_node_savings": max_commit_delay_node_savings,
        },
        "warnings": warnings,
    }


def estimate_data_boost_kspu_hrs(
    spec: WorkloadSpec, storage: Mapping[str, Any]
) -> float:
    """Estimate monthly serverless Data Boost usage in 1,000 SPU-hr units."""
    if spec.data_boost_kspu_hrs_override is not None:
        return round(spec.data_boost_kspu_hrs_override, 3)
    if not spec.is_production_eligible and spec.processing_units_override is not None:
        return 0.05  # Minimal lab PageRank / WCC demonstration run
    # Scales smoothly with storage footprint + edge count (nightly PageRank/WCC + weekly exports)
    total_gib = float(storage["total_gib"])
    estimated = 15.0 + (total_gib * 1.2) + (spec.edges / 250_000.0)
    return round(max(10.0, estimated), 1)


def calculate_spanner_costs(
    spec: WorkloadSpec,
    storage: Mapping[str, Any],
    compute: Mapping[str, Any],
) -> Dict[str, Any]:
    """Compute verified 2026 Cloud Spanner monthly & workshop-hour costs."""
    region_info = PRICING_REGIONS[spec.region]
    edition = spec.edition
    nodes_eq = float(compute["nodes_equivalent"])

    # Compute hourly & monthly rates (On-Demand, 1-Yr CUD -20%, 3-Yr CUD -40%)
    od_hr = region_info["compute_per_node_hour_usd"][edition]
    cud1_hr = region_info["compute_cud_1yr_per_node_hour_usd"][edition]
    cud3_hr = region_info["compute_cud_3yr_per_node_hour_usd"][edition]

    compute_od_mo = nodes_eq * od_hr * HOURS_PER_MONTH
    compute_cud1_mo = nodes_eq * cud1_hr * HOURS_PER_MONTH
    compute_cud3_mo = nodes_eq * cud3_hr * HOURS_PER_MONTH

    # Optional additional read-only replicas (Enterprise / Enterprise Plus)
    ro_hr = region_info["additional_ro_replica_per_node_hour_usd"].get(edition, 0.0)
    ro_od_mo = spec.ro_replicas * nodes_eq * ro_hr * HOURS_PER_MONTH
    ro_cud1_mo = ro_od_mo * 0.80
    ro_cud3_mo = ro_od_mo * 0.60

    # Storage (SSD + HDD Locality Group tiered vs. All-SSD baseline)
    ssd_rate = region_info["storage_per_gib_month_usd"]["ssd"]
    hdd_rate = region_info["storage_per_gib_month_usd"]["hdd"]
    backup_rate = region_info["storage_per_gib_month_usd"]["backup"]

    total_gib = float(storage["total_gib"])
    ssd_gib = float(storage["ssd_gib"])
    hdd_gib = float(storage["hdd_gib"])

    ssd_storage_mo = ssd_gib * ssd_rate
    hdd_storage_mo = hdd_gib * hdd_rate
    tiered_storage_mo = ssd_storage_mo + hdd_storage_mo
    all_ssd_storage_mo = total_gib * ssd_rate
    locality_group_savings_mo = all_ssd_storage_mo - tiered_storage_mo
    locality_group_savings_pct = (
        (locality_group_savings_mo / all_ssd_storage_mo) * 100.0
        if all_ssd_storage_mo > 0
        else 0.0
    )

    # Primary Backup Storage (AUTOMATIC for production; NONE for workshop sandbox)
    primary_backup_gib = total_gib if spec.enable_primary_backup else 0.0
    primary_backup_mo = primary_backup_gib * backup_rate

    # Optional Cross-Region DR Backup Copy (disabled by default: single-region Bangkok backups only)
    dr_info: Dict[str, Any] = {
        "enabled": False,
        "dr_backup_region": None,
        "dr_backup_city": None,
        "dr_backup_rate_per_gib_month_usd": 0.0,
        "egress_rate_per_gib_usd": 0.0,
        "dr_backup_storage_monthly_usd": 0.0,
        "initial_full_copy_egress_usd": 0.0,
        "monthly_delta_copy_egress_usd": 0.0,
        "total_dr_monthly_recurring_usd": 0.0,
        "architecture_note": (
            f"Single-region {region_info['city']} ({region_info['spanner_instance_config']}) "
            f"backups/PITR ONLY (${backup_rate:.5f}/GiB-mo, $0.00/mo cross-region egress; "
            "no second Spanner DR instance). Pass --dr-backup-region <region> only to "
            "model optional cross-region DR."
        ),
    }
    if spec.dr_backup_region is not None:
        dr_reg = PRICING_REGIONS[spec.dr_backup_region]
        dr_bkp_rate = dr_reg["storage_per_gib_month_usd"]["backup"]
        same_region = spec.dr_backup_region == spec.region
        egress_rate = (
            0.0 if same_region else CROSS_REGION_BACKUP_EGRESS_ASIA_PER_GIB_USD
        )
        dr_bkp_gib = total_gib  # 1x compressed/deduped DR snapshot baseline
        dr_storage_mo = dr_bkp_gib * dr_bkp_rate
        initial_egress_usd = dr_bkp_gib * egress_rate
        monthly_delta_egress_usd = (
            dr_bkp_gib * DR_BACKUP_MONTHLY_DELTA_RATIO * egress_rate
        )
        dr_recurring_mo = dr_storage_mo + monthly_delta_egress_usd

        if spec.region == "asia-southeast3" and spec.dr_backup_region == "asia-southeast1":
            arch_note = (
                "Optional cross-region DR override: regional-asia-southeast3 "
                "(Bangkok, PDPA resident) -> regional-asia-southeast1 (Singapore, "
                "$0.08/GiB egress + $0.03432/GiB-mo storage). Default architecture "
                "uses single-region Bangkok backups only ($0.00/mo egress)."
            )
        elif same_region:
            arch_note = (
                f"DR backup region matches primary ({spec.region}); $0.00/GiB egress "
                "(same-region backup copy only)."
            )
        else:
            arch_note = (
                f"Optional cross-region backup copy from {spec.region} to {spec.dr_backup_region} "
                f"(${egress_rate:.2f}/GiB egress + ${dr_bkp_rate:.5f}/GiB-mo DR storage)."
            )

        dr_info = {
            "enabled": True,
            "dr_backup_region": spec.dr_backup_region,
            "dr_backup_city": dr_reg["city"],
            "dr_instance_config": dr_reg["spanner_instance_config"],
            "dr_backup_rate_per_gib_month_usd": dr_bkp_rate,
            "egress_rate_per_gib_usd": egress_rate,
            "monthly_delta_ratio": DR_BACKUP_MONTHLY_DELTA_RATIO,
            "dr_backup_storage_monthly_usd": round(dr_storage_mo, 4),
            "initial_full_copy_egress_usd": round(initial_egress_usd, 4),
            "monthly_delta_copy_egress_usd": round(monthly_delta_egress_usd, 4),
            "total_dr_monthly_recurring_usd": round(dr_recurring_mo, 4),
            "architecture_note": arch_note,
        }

    # Data Boost Monthly Allowance
    db_kspu_hrs = estimate_data_boost_kspu_hrs(spec, storage)
    db_rate = region_info["data_boost_per_1000_spu_hour_usd"]
    data_boost_mo = db_kspu_hrs * db_rate

    # Total Monthly Costs (On-Demand, 1-Yr CUD, 3-Yr CUD)
    non_compute_mo = (
        tiered_storage_mo
        + primary_backup_mo
        + dr_info["total_dr_monthly_recurring_usd"]
        + data_boost_mo
    )
    total_od_mo = compute_od_mo + ro_od_mo + non_compute_mo
    total_cud1_mo = compute_cud1_mo + ro_cud1_mo + non_compute_mo
    total_cud3_mo = compute_cud3_mo + ro_cud3_mo + non_compute_mo

    # Workshop lab-duration estimate (e.g., 8 hrs @ 100 PU = $1.0332 in Bangkok)
    lab_compute_usd = nodes_eq * od_hr * spec.workshop_hours
    lab_storage_prorated_usd = tiered_storage_mo * (spec.workshop_hours / HOURS_PER_MONTH)
    lab_data_boost_usd = (
        data_boost_mo
        if not spec.is_production_eligible
        else data_boost_mo * (spec.workshop_hours / HOURS_PER_MONTH)
    )
    lab_total_usd = lab_compute_usd + lab_storage_prorated_usd + lab_data_boost_usd

    return {
        "region": spec.region,
        "city": region_info["city"],
        "spanner_instance_config": region_info["spanner_instance_config"],
        "edition": edition,
        "sla": region_info["sla"],
        "regional_multiplier_vs_us_central1": region_info[
            "regional_multiplier_vs_us_central1"
        ],
        "unit_rates_usd": {
            "compute_per_node_hour_od": od_hr,
            "compute_per_100pu_hour_od": round(od_hr / 10.0, 6),
            "compute_per_node_month_od": round(od_hr * HOURS_PER_MONTH, 4),
            "compute_per_node_hour_cud_1yr": cud1_hr,
            "compute_per_node_month_cud_1yr": round(cud1_hr * HOURS_PER_MONTH, 4),
            "compute_per_node_hour_cud_3yr": cud3_hr,
            "compute_per_node_month_cud_3yr": round(cud3_hr * HOURS_PER_MONTH, 4),
            "additional_ro_replica_per_node_hour": ro_hr,
            "ssd_per_gib_month": ssd_rate,
            "hdd_per_gib_month": hdd_rate,
            "backup_per_gib_month": backup_rate,
            "data_boost_per_1000_spu_hour": db_rate,
            "intra_region_replication_per_gib": region_info[
                "intra_region_replication_per_gib_usd"
            ],
        },
        "compute_monthly_usd": {
            "on_demand": round(compute_od_mo, 2),
            "cud_1yr": round(compute_cud1_mo, 2),
            "cud_3yr": round(compute_cud3_mo, 2),
            "ro_replicas_on_demand": round(ro_od_mo, 2),
            "ro_replicas_cud_1yr": round(ro_cud1_mo, 2),
            "ro_replicas_cud_3yr": round(ro_cud3_mo, 2),
            "cud_1yr_savings_usd": round((compute_od_mo - compute_cud1_mo) + (ro_od_mo - ro_cud1_mo), 2),
            "cud_3yr_savings_usd": round((compute_od_mo - compute_cud3_mo) + (ro_od_mo - ro_cud3_mo), 2),
        },
        "storage_monthly_usd": {
            "ssd_usd": round(ssd_storage_mo, 4),
            "hdd_usd": round(hdd_storage_mo, 4),
            "tiered_total_usd": round(tiered_storage_mo, 4),
            "all_ssd_baseline_usd": round(all_ssd_storage_mo, 4),
            "locality_group_savings_usd": round(locality_group_savings_mo, 4),
            "locality_group_savings_pct": round(locality_group_savings_pct, 2),
            "primary_backup_enabled": spec.enable_primary_backup,
            "primary_backup_usd": round(primary_backup_mo, 4),
        },
        "dr_backup": dr_info,
        "data_boost": {
            "monthly_allowance_1000_spu_hrs": db_kspu_hrs,
            "monthly_allowance_spu_hrs": round(db_kspu_hrs * 1000.0, 1),
            "rate_per_1000_spu_hour_usd": db_rate,
            "monthly_usd": round(data_boost_mo, 2),
            "workloads": (
                "Serverless Graph Algorithms (PageRank, WCC, ShortestPath), "
                "BigQuery federated queries, and bulk exports with zero OLTP CPU impact."
            ),
        },
        "totals_monthly_usd": {
            "on_demand": round(total_od_mo, 2),
            "cud_1yr": round(total_cud1_mo, 2),
            "cud_3yr": round(total_cud3_mo, 2),
        },
        "annualized_usd": {
            "on_demand": round(total_od_mo * 12.0, 2),
            "cud_1yr": round(total_cud1_mo * 12.0, 2),
            "cud_3yr": round(total_cud3_mo * 12.0, 2),
        },
        "workshop_session_estimate": {
            "duration_hours": spec.workshop_hours,
            "compute_usd": round(lab_compute_usd, 4),
            "prorated_storage_usd": round(lab_storage_prorated_usd, 4),
            "data_boost_usd": round(lab_data_boost_usd, 4),
            "total_spanner_usd": round(lab_total_usd, 4),
        },
    }


def calculate_legacy_3_store_tco(
    spec: WorkloadSpec,
    storage: Mapping[str, Any],
    spanner_costs: Mapping[str, Any],
) -> Dict[str, Any]:
    """Model Legacy 3-Store Stack TCO and compare savings vs. Cloud Spanner Enterprise.

    Legacy stack components (for production HA parity):
      1. Neo4j Enterprise / AuraDB Enterprise (3-node HA Causal Cluster)
      2. Managed Vector Database HA (multi-AZ in-memory HNSW/IVF pods)
      3. Managed OpenSearch / Elasticsearch Enterprise (3-node HA cluster)
      4. ETL / CDC Sync Infrastructure (Kafka/PubSub/Debezium/Dataflow + DLQ)
      5. Multi-Store SRE & Data Engineering Ops Overhead (itemized separately)
    """
    region_info = PRICING_REGIONS[spec.region]
    mult = float(region_info["regional_multiplier_vs_us_central1"])
    comp = storage["components_gib"]

    write_qps = spec.qps * (spec.write_pct / 100.0)
    total_gib = float(storage["total_gib"])
    graph_gib = (
        float(comp["base_nodes_gib"])
        + float(comp["base_edges_gib"])
        + float(comp["reverse_covering_indexes_gib"])
    )
    vector_gib = float(comp["vector_embeddings_float32_gib"])
    search_gib = (
        float(comp["base_chunks_text_gib"])
        + float(comp["fts_tokenlist_and_search_index_gib"])
    )

    is_dev_sandbox = (
        not spec.is_production_eligible
        and spec.processing_units_override is not None
        and spec.processing_units_override < PRODUCTION_MIN_PU
    )

    if is_dev_sandbox:
        # Minimal non-HA dev/sandbox 3-store equivalents (single dev instances + sync worker)
        neo4j_mo = 120.0 * mult
        vector_db_mo = 70.0 * mult
        search_cluster_mo = 95.0 * mult
        etl_sync_infra_mo = 65.0 * mult
        sre_eng_overhead_mo = 250.0  # Minimal dev pipeline maintenance
        comparison_note = (
            "Workshop preset is a 100 PU dev/lab sandbox (not production HA). "
            "Comparison reflects minimal single-node dev equivalents; production "
            "3-store HA consolidation savings start at >= 1,000 PUs (small+)."
        )
    else:
        # 1. Neo4j Enterprise HA (3-node cluster + RAM page cache + commercial license)
        neo4j_mo = (1800.0 + (spec.qps * 2.20) + (graph_gib * 45.0)) * mult

        # 2. Managed Vector DB HA (multi-AZ enterprise tier + HNSW memory pods)
        vector_db_mo = (350.0 + (spec.qps * 0.55) + (vector_gib * 32.0)) * mult

        # 3. Managed OpenSearch / Elasticsearch Enterprise HA (3-AZ cluster)
        search_cluster_mo = (650.0 + (spec.qps * 0.85) + (search_gib * 14.0)) * mult

        # 4. CDC / ETL Sync Infrastructure (Kafka/Debezium/Dataflow + DLQ)
        etl_sync_infra_mo = (450.0 + (write_qps * 1.20) + (total_gib * 1.50)) * mult

        # 5. Multi-store SRE & Data Engineering Ops Overhead (0.15 - 0.60 FTE equivalent)
        #    Covers 3 upgrade cycles, 3 IAM/audit models, and CDC drift incident triage
        fte_fraction = min(0.60, 0.12 + 0.08 * math.log10(max(10.0, spec.qps)))
        sre_eng_overhead_mo = round(fte_fraction * 10_000.0, 2)
        comparison_note = (
            "Models production HA parity across 3 separate engines (upgrading from "
            "single-node Neo4j Community Edition to 3-node Enterprise HA + Managed "
            "Vector DB HA + Managed OpenSearch/Elasticsearch HA + CDC sync pipelines). "
            "Both Infra-Only and Fully Loaded (Infra + SRE Ops) savings are itemized."
        )

    legacy_infra_only_mo = (
        neo4j_mo + vector_db_mo + search_cluster_mo + etl_sync_infra_mo
    )
    legacy_fully_loaded_mo = legacy_infra_only_mo + sre_eng_overhead_mo

    sp_od = float(spanner_costs["totals_monthly_usd"]["on_demand"])
    sp_cud1 = float(spanner_costs["totals_monthly_usd"]["cud_1yr"])
    sp_cud3 = float(spanner_costs["totals_monthly_usd"]["cud_3yr"])

    def _savings(baseline: float, target: float) -> Dict[str, float]:
        delta = baseline - target
        pct = (delta / baseline) * 100.0 if baseline > 0 else 0.0
        return {
            "monthly_usd": round(delta, 2),
            "annual_usd": round(delta * 12.0, 2),
            "savings_pct": round(pct, 1),
        }

    return {
        "is_dev_sandbox_comparison": is_dev_sandbox,
        "comparison_note": comparison_note,
        "legacy_components_monthly_usd": {
            "neo4j_enterprise_ha_cluster": round(neo4j_mo, 2),
            "managed_vector_db_ha": round(vector_db_mo, 2),
            "managed_opensearch_elasticsearch_ha": round(search_cluster_mo, 2),
            "cdc_etl_sync_infrastructure": round(etl_sync_infra_mo, 2),
            "infra_only_subtotal": round(legacy_infra_only_mo, 2),
            "multi_store_sre_eng_overhead": round(sre_eng_overhead_mo, 2),
            "fully_loaded_total": round(legacy_fully_loaded_mo, 2),
        },
        "savings_vs_infra_only": {
            "spanner_on_demand": _savings(legacy_infra_only_mo, sp_od),
            "spanner_cud_1yr": _savings(legacy_infra_only_mo, sp_cud1),
            "spanner_cud_3yr": _savings(legacy_infra_only_mo, sp_cud3),
        },
        "savings_vs_fully_loaded": {
            "spanner_on_demand": _savings(legacy_fully_loaded_mo, sp_od),
            "spanner_cud_1yr": _savings(legacy_fully_loaded_mo, sp_cud1),
            "spanner_cud_3yr": _savings(legacy_fully_loaded_mo, sp_cud3),
        },
        "architectural_benefits": {
            "sync_lag_ms": 0,
            "consistency_model": (
                "External consistency (TrueTime ACID) across relational tables, "
                "ISO GQL Property Graph, FTS TOKENLIST, and ScaNN vector indexes "
                "in a single commit — zero CDC lag, zero ghost search hits, zero "
                "dangling graph edges."
            ),
            "consolidated_pillars": [
                "1 unified ACID database instead of 3 HA clusters (9+ nodes)",
                "Zero ETL/CDC glue pipelines or DLQ replay jobs between stores",
                "Single IAM + FGAC + Cloud Audit Log + Thailand PDPA residency perimeter",
                "5x cheaper HDD Tiered Storage via Locality Groups + 20%/40% CUDs",
                "Serverless Data Boost isolates PageRank/WCC analytics from OLTP CPU",
            ],
        },
    }


def evaluate_scenario(spec: WorkloadSpec) -> Dict[str, Any]:
    """Run full sizing, pricing, and 3-store TCO evaluation for one workload."""
    validate_spec(spec)
    storage = calculate_storage_breakdown(spec)
    compute = calculate_compute_sizing(spec, storage)
    spanner_costs = calculate_spanner_costs(spec, storage, compute)
    legacy_tco = calculate_legacy_3_store_tco(spec, storage, spanner_costs)

    result: Dict[str, Any] = {
        "name": spec.name,
        "label": spec.label,
        "inputs": {
            "nodes": spec.nodes,
            "edges": spec.edges,
            "chunks": spec.chunks,
            "qps": spec.qps,
            "write_pct": spec.write_pct,
            "vector_dim": spec.vector_dim,
            "cold_hdd_pct": spec.cold_hdd_pct,
            "region": spec.region,
            "edition": spec.edition,
            "dr_backup_region": spec.dr_backup_region,
            "ro_replicas": spec.ro_replicas,
            "processing_units_override": spec.processing_units_override,
        },
        "storage": storage,
        "compute": compute,
        "spanner_costs": spanner_costs,
        "legacy_3_store_tco": legacy_tco,
    }
    if spec.worksheet_metadata:
        result["worksheet_metadata"] = spec.worksheet_metadata
    return result


# ---------------------------------------------------------------------------
# Worksheet Loader & CLI Resolution
# ---------------------------------------------------------------------------


def _normalize_optional_region(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = str(value).strip().lower()
    if cleaned in ("", "none", "null", "false", "disabled"):
        return None
    return cleaned


def _pick(mapping: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    """Lookup the first present non-None key across flat or nested worksheet sections."""
    sections = [mapping]
    for nested_key in ("workload", "scale", "deployment", "storage", "region_config", "options"):
        sub = mapping.get(nested_key)
        if isinstance(sub, Mapping):
            sections.append(sub)
    for key in keys:
        for sec in sections:
            if key in sec and sec[key] is not None:
                return sec[key]
    return default


def load_worksheet(path: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Load a customer scale worksheet JSON file (supports flat or nested schema)."""
    if not path.exists():
        raise FileNotFoundError(f"Worksheet file not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        raw = json.load(fh)
    if not isinstance(raw, dict):
        raise ValueError("Worksheet JSON root must be an object.")

    base_preset_name = _pick(raw, "base_preset", "preset", default="medium")
    base = PRESETS.get(str(base_preset_name).lower(), PRESETS["medium"])

    extracted = {
        "name": str(_pick(raw, "workload_id", "name", default=path.stem)),
        "label": str(
            _pick(
                raw,
                "workload_name",
                "customer_name",
                "title",
                "label",
                default=f"Customer Scale Worksheet ({path.name})",
            )
        ),
        "nodes": int(_pick(raw, "nodes", "node_count", "graph_nodes", default=base["nodes"])),
        "edges": int(_pick(raw, "edges", "edge_count", "graph_edges", default=base["edges"])),
        "chunks": int(
            _pick(
                raw,
                "chunks",
                "document_chunks",
                "chunk_count",
                default=base["chunks"],
            )
        ),
        "qps": float(_pick(raw, "qps", "peak_qps", "target_qps", default=base["qps"])),
        "write_pct": float(
            _pick(raw, "write_pct", "write_percentage", default=base["write_pct"])
        ),
        "vector_dim": int(
            _pick(
                raw,
                "vector_dim",
                "embedding_dim",
                "embedding_dimensions",
                default=base["vector_dim"],
            )
        ),
        "cold_hdd_pct": float(
            _pick(
                raw,
                "cold_hdd_pct",
                "cold_storage_hdd_pct",
                "hdd_pct",
                default=base["cold_hdd_pct"],
            )
        ),
        "region": str(
            _pick(raw, "region", "primary_region", default="asia-southeast3")
        ).strip().lower(),
        "dr_backup_region": _normalize_optional_region(
            _pick(raw, "dr_backup_region", "secondary_dr_region", "dr_region", default=None)
        ),
        "edition": str(_pick(raw, "edition", "spanner_edition", default="enterprise"))
        .strip()
        .lower(),
        "ro_replicas": int(
            _pick(raw, "ro_replicas", "additional_ro_replicas", default=0)
        ),
        "processing_units_override": _pick(raw, "processing_units_override", default=None),
        "data_boost_kspu_hrs_override": _pick(
            raw, "data_boost_kspu_hrs", "data_boost_kspu_hrs_override", default=None
        ),
        "is_production_eligible": bool(
            _pick(raw, "is_production_eligible", default=True)
        ),
        "enable_primary_backup": bool(
            _pick(raw, "enable_primary_backup", default=True)
        ),
    }

    metadata: Dict[str, Any] = {"worksheet_path": str(path)}
    for meta_key in ("todo_status", "customer_name", "use_case", "notes", "owner"):
        if meta_key in raw:
            metadata[meta_key] = raw[meta_key]

    return extracted, metadata


def build_specs_from_args(args: argparse.Namespace) -> Tuple[str, List[WorkloadSpec]]:
    """Resolve CLI flags + optional worksheet into one or more WorkloadSpecs."""
    region_override = args.region.strip().lower() if args.region else None
    dr_override = (
        _normalize_optional_region(args.dr_backup_region)
        if args.dr_backup_region is not None
        else None
    )
    edition_override = args.edition.strip().lower() if args.edition else None

    has_custom_workload_flags = any(
        val is not None
        for val in (args.nodes, args.edges, args.chunks, args.qps)
    )

    # Case 1: Customer worksheet JSON
    if args.worksheet:
        ws, metadata = load_worksheet(Path(args.worksheet))
        spec = WorkloadSpec(
            name=ws["name"],
            label=ws["label"],
            nodes=args.nodes if args.nodes is not None else ws["nodes"],
            edges=args.edges if args.edges is not None else ws["edges"],
            chunks=args.chunks if args.chunks is not None else ws["chunks"],
            qps=args.qps if args.qps is not None else ws["qps"],
            write_pct=(
                args.write_pct if args.write_pct is not None else ws["write_pct"]
            ),
            vector_dim=(
                args.vector_dim if args.vector_dim is not None else ws["vector_dim"]
            ),
            cold_hdd_pct=(
                args.cold_hdd_pct
                if args.cold_hdd_pct is not None
                else ws["cold_hdd_pct"]
            ),
            region=region_override or ws["region"],
            edition=edition_override or ws["edition"],
            dr_backup_region=(
                dr_override
                if args.dr_backup_region is not None
                else ws["dr_backup_region"]
            ),
            ro_replicas=(
                args.ro_replicas if args.ro_replicas is not None else ws["ro_replicas"]
            ),
            processing_units_override=ws["processing_units_override"],
            is_production_eligible=ws["is_production_eligible"],
            data_boost_kspu_hrs_override=(
                args.data_boost_kspu_hrs
                if args.data_boost_kspu_hrs is not None
                else ws["data_boost_kspu_hrs_override"]
            ),
            enable_primary_backup=ws["enable_primary_backup"],
            workshop_hours=args.workshop_hours,
            worksheet_metadata=metadata,
        )
        return "worksheet", [spec]

    # Case 2: Custom CLI sizing (without explicit --preset)
    if args.preset is None and has_custom_workload_flags:
        base = PRESETS["medium"]
        nodes = args.nodes if args.nodes is not None else base["nodes"]
        edges = args.edges if args.edges is not None else nodes * 5
        chunks = args.chunks if args.chunks is not None else int(nodes * 2.5)
        qps = args.qps if args.qps is not None else base["qps"]
        spec = WorkloadSpec(
            name="custom",
            label="Custom Enterprise Workload",
            nodes=nodes,
            edges=edges,
            chunks=chunks,
            qps=qps,
            write_pct=args.write_pct if args.write_pct is not None else 15.0,
            vector_dim=args.vector_dim if args.vector_dim is not None else 768,
            cold_hdd_pct=args.cold_hdd_pct if args.cold_hdd_pct is not None else 30.0,
            region=region_override or "asia-southeast3",
            edition=edition_override or "enterprise",
            dr_backup_region=dr_override,
            ro_replicas=args.ro_replicas if args.ro_replicas is not None else 0,
            processing_units_override=None,
            is_production_eligible=True,
            data_boost_kspu_hrs_override=args.data_boost_kspu_hrs,
            enable_primary_backup=True,
            workshop_hours=args.workshop_hours,
        )
        return "custom", [spec]

    # Case 3: Built-in T-shirt presets (default: all if nothing specified)
    preset_choice = (args.preset or "all").strip().lower()
    preset_names = list(PRESET_ORDER) if preset_choice == "all" else [preset_choice]

    specs: List[WorkloadSpec] = []
    for pname in preset_names:
        if pname not in PRESETS:
            raise ValueError(
                f"Unknown preset '{pname}'. Choose from: workshop, small, medium, large, all."
            )
        p = PRESETS[pname]
        specs.append(
            WorkloadSpec(
                name=p["name"],
                label=p["label"],
                nodes=args.nodes if args.nodes is not None else p["nodes"],
                edges=args.edges if args.edges is not None else p["edges"],
                chunks=args.chunks if args.chunks is not None else p["chunks"],
                qps=args.qps if args.qps is not None else p["qps"],
                write_pct=(
                    args.write_pct if args.write_pct is not None else p["write_pct"]
                ),
                vector_dim=(
                    args.vector_dim if args.vector_dim is not None else p["vector_dim"]
                ),
                cold_hdd_pct=(
                    args.cold_hdd_pct
                    if args.cold_hdd_pct is not None
                    else p["cold_hdd_pct"]
                ),
                region=region_override or "asia-southeast3",
                edition=edition_override or "enterprise",
                dr_backup_region=dr_override,
                ro_replicas=args.ro_replicas if args.ro_replicas is not None else 0,
                processing_units_override=p["processing_units_override"],
                is_production_eligible=p["is_production_eligible"],
                data_boost_kspu_hrs_override=(
                    args.data_boost_kspu_hrs
                    if args.data_boost_kspu_hrs is not None
                    else p["data_boost_kspu_hrs_override"]
                ),
                enable_primary_backup=p["enable_primary_backup"],
                workshop_hours=args.workshop_hours,
            )
        )

    mode = "all" if preset_choice == "all" else "preset"
    return mode, specs


# ---------------------------------------------------------------------------
# Human-Readable Report Formatting
# ---------------------------------------------------------------------------


def _fmt_money(amount: float, decimals: int = 2) -> str:
    return f"${amount:,.{decimals}f}"


def _fmt_int(value: int) -> str:
    return f"{value:,}"


def _fmt_gib(value: float) -> str:
    if value >= 1024.0:
        return f"{value:,.2f} GiB ({value / 1024.0:,.2f} TiB)"
    if value >= 1.0:
        return f"{value:,.2f} GiB"
    return f"{value:,.4f} GiB ({value * 1024.0:,.2f} MiB)"


def render_text_report(payload: Mapping[str, Any]) -> str:
    """Render an executive-grade, human-readable CLI sizing & TCO report."""
    lines: List[str] = []
    sep = "=" * 108
    subsep = "-" * 108

    region_key = payload["primary_region"]
    reg = PRICING_REGIONS[region_key]
    dr_key = payload["dr_backup_region"]
    edition = payload["edition"]
    scenarios: List[Dict[str, Any]] = payload["scenarios"]

    lines.append(sep)
    if region_key == "asia-southeast3" and not dr_key:
        lines.append(
            "CLOUD SPANNER ENTERPRISE — BANGKOK SINGLE-REGION SIZING & 3-STORE TCO CALCULATOR (2026)"
        )
    else:
        lines.append(
            "CLOUD SPANNER ENTERPRISE — MULTI-MODEL SIZING & 3-STORE TCO CALCULATOR (2026)"
        )
    lines.append(sep)
    lines.append(
        f"Primary Region   : {reg['city']} ({reg['region']} / {reg['spanner_instance_config']}, "
        f"{reg['sla']} SLA, {reg['regional_multiplier_vs_us_central1']:.3g}x US) — SINGLE Spanner instance"
    )
    lines.append(
        "Companion AI/Sec : Singapore (asia-southeast1, ~25 ms RTT) — ONLY for Vertex AI, "
        "Model Armor & Cloud DLP (unavailable in Bangkok)"
    )
    lines.append(f"Spanner Edition  : {edition.upper()}")
    if dr_key:
        dr_reg = PRICING_REGIONS[dr_key]
        lines.append(
            f"Optional DR Copy : {dr_reg['city']} ({dr_reg['region']} / "
            f"{dr_reg['spanner_instance_config']}) — "
            f"${CROSS_REGION_BACKUP_EGRESS_ASIA_PER_GIB_USD:.2f}/GiB copy egress + "
            f"${dr_reg['storage_per_gib_month_usd']['backup']:.5f}/GiB-mo backup"
        )
    else:
        lines.append(
            f"Backup Strategy  : Same-region {reg['city']} backups/PITR ONLY "
            f"(${reg['storage_per_gib_month_usd']['backup']:.5f}/GiB-mo, "
            "$0.00/mo cross-region egress; optional --dr-backup-region override available)"
        )
    lines.append(f"Residency Note   : {reg['residency_note']}")
    lines.append("")

    # Verified Live 2026 Regional Rate Card
    lines.append("VERIFIED 2026 CLOUD SPANNER RATE CARD (USD, 730 hrs/month)")
    lines.append(subsep)
    lines.append(
        f"{'Region':<29} {'Std/hr':>7} {'Ent/hr':>8} {'Ent/mo':>10} "
        f"{'Ent 1Y':>8} {'Ent 3Y':>8} {'SSD':>7} {'HDD':>8} {'Backup':>8} {'DBoost':>8}"
    )
    lines.append(subsep)
    short_names = {
        "asia-southeast3": "Bangkok",
        "asia-southeast1": "Singapore",
        "us-central1": "Iowa, US",
    }
    for rkey in ("asia-southeast3", "asia-southeast1", "us-central1"):
        r = PRICING_REGIONS[rkey]
        marker = "*" if rkey == region_key else " "
        label = f"{marker}{short_names[rkey]} ({rkey})"
        lines.append(
            f"{label:<29} "
            f"${r['compute_per_node_hour_usd']['standard']:>6.4f} "
            f"${r['compute_per_node_hour_usd']['enterprise']:>7.5f} "
            f"${r['compute_per_node_month_usd']['enterprise']:>9.3f} "
            f"${r['compute_cud_1yr_per_node_hour_usd']['enterprise']:>7.5f} "
            f"${r['compute_cud_3yr_per_node_hour_usd']['enterprise']:>7.5f} "
            f"${r['storage_per_gib_month_usd']['ssd']:>6.4f} "
            f"${r['storage_per_gib_month_usd']['hdd']:>7.5f} "
            f"${r['storage_per_gib_month_usd']['backup']:>7.5f} "
            f"${r['data_boost_per_1000_spu_hour_usd']:>7.4f}"
        )
    lines.append(
        f"  * Active primary region (Singapore & Iowa Spanner rates shown for comparison only) | "
        f"Additional RO Replica ({region_key} Enterprise): "
        f"${reg['additional_ro_replica_per_node_hour_usd']['enterprise']:.5f}/node-hr | "
        f"Enterprise Plus: ${reg['compute_per_node_hour_usd']['enterprise_plus']:.5f}/node-hr"
    )
    lines.append("")

    # Multi-scenario executive comparison table
    if len(scenarios) > 1:
        lines.append("EXECUTIVE T-SHIRT COMPARISON SUMMARY (MONTHLY USD)")
        lines.append(subsep)
        lines.append(
            f"{'Preset':<10} {'PUs':>6} {'CPU%':>6} {'Storage':>10} "
            f"{'Spanner OD':>11} {'1-Yr CUD':>10} {'3-Yr CUD':>10} "
            f"{'3-Store Infra':>14} {'3Y Sav%':>8}"
        )
        lines.append(subsep)
        for sc in scenarios:
            pu = sc["compute"]["processing_units"]
            cpu = sc["compute"]["projected_hp_cpu_pct"]
            gib = sc["storage"]["total_gib"]
            sp_od = sc["spanner_costs"]["totals_monthly_usd"]["on_demand"]
            sp_1y = sc["spanner_costs"]["totals_monthly_usd"]["cud_1yr"]
            sp_3y = sc["spanner_costs"]["totals_monthly_usd"]["cud_3yr"]
            leg_infra = sc["legacy_3_store_tco"]["legacy_components_monthly_usd"][
                "infra_only_subtotal"
            ]
            sav_3y_pct = sc["legacy_3_store_tco"]["savings_vs_infra_only"][
                "spanner_cud_3yr"
            ]["savings_pct"]
            tag = " *" if not sc["compute"]["is_production_eligible"] else ""
            lines.append(
                f"{sc['name'] + tag:<10} "
                f"{pu:>6,} "
                f"{cpu:>5.1f}% "
                f"{gib:>7.2f} GiB "
                f"{_fmt_money(sp_od):>11} "
                f"{_fmt_money(sp_1y):>10} "
                f"{_fmt_money(sp_3y):>10} "
                f"{_fmt_money(leg_infra):>14} "
                f"{sav_3y_pct:>7.1f}%"
            )
        lines.append(
            "  * workshop = 100 PU DEV/LAB SANDBOX ONLY (no Managed Autoscaler; "
            f"8-hr lab compute = ${reg['workshop_100pu_8hr_compute_usd']:.4f} ~"
            f"${reg['workshop_100pu_8hr_compute_usd']:.2f})."
        )
        lines.append("")

    # Detailed per-scenario breakdown
    for sc in scenarios:
        inp = sc["inputs"]
        st = sc["storage"]
        comp = st["components_gib"]
        cm = sc["compute"]
        cost = sc["spanner_costs"]
        tco = sc["legacy_3_store_tco"]
        lcomp = tco["legacy_components_monthly_usd"]

        lines.append(sep)
        lines.append(f"SCENARIO: {sc['name'].upper()} — {sc['label']}")
        lines.append(sep)

        if "worksheet_metadata" in sc:
            meta = sc["worksheet_metadata"]
            status = meta.get("todo_status")
            lines.append(f"Worksheet Source : {meta.get('worksheet_path')}")
            if status:
                lines.append(f"Worksheet Status : {status}")

        for warn in cm["warnings"]:
            lines.append(f"[!] WARNING: {warn}")

        # 1. Workload & Compute Sizing
        lines.append("")
        lines.append("1. Workload Profile & Compute Sizing (< 65% Regional HP CPU Threshold)")
        lines.append(subsep)
        lines.append(
            f"  Graph & Corpus    : {_fmt_int(inp['nodes'])} nodes | "
            f"{_fmt_int(inp['edges'])} edges | {_fmt_int(inp['chunks'])} chunks "
            f"({inp['vector_dim']}-dim FLOAT32)"
        )
        lines.append(
            f"  Traffic Mix       : {inp['qps']:,.1f} total QPS "
            f"({cm['read_qps']:,.1f} reads/s, {cm['write_qps']:,.1f} writes/s @ "
            f"{inp['write_pct']:.0f}% writes)"
        )
        lines.append(
            f"  Provisioned Cap.  : {_fmt_int(cm['processing_units'])} PUs "
            f"({cm['nodes_equivalent']:.2g} node eq.) | "
            f"Driver: {cm['sizing_driver']}"
        )
        lines.append(
            f"  Projected HP CPU  : {cm['projected_hp_cpu_pct']:.1f}% "
            f"(Regional 3-zone target: < {cm['regional_hp_cpu_threshold_pct']:.0f}%)"
        )
        lines.append(
            f"  Storage Headroom  : {cm['storage_utilization_pct']:.2f}% of "
            f"{cm['max_allotted_storage_tib']:.1f} TiB allotted limit"
        )
        auto = cm["managed_autoscaler"]
        if auto["eligible"]:
            lines.append(
                f"  Managed Autoscaler: ELIGIBLE — min={auto['min_nodes']} nodes, "
                f"max={auto['max_nodes']} nodes "
                f"(HP CPU={auto['high_priority_cpu_target_pct']}%, "
                f"Total CPU={auto['total_cpu_target_pct']}%, "
                f"Storage={auto['storage_target_pct']}%)"
            )
        else:
            lines.append(f"  Managed Autoscaler: NOT ELIGIBLE — {auto['reason']}")

        if not cm["is_production_eligible"]:
            ws_est = cost["workshop_session_estimate"]
            lines.append(
                f"  Lab Session Est.  : {ws_est['duration_hours']:.0f}-hr sandbox = "
                f"${ws_est['compute_usd']:.4f} compute (~${ws_est['compute_usd']:.2f}) | "
                f"Total Spanner = ${ws_est['total_spanner_usd']:.4f}"
            )

        # 2. Multi-Model Storage Breakdown
        lines.append("")
        lines.append("2. Multi-Model Storage Footprint & Locality Group Tiering (SSD -> HDD)")
        lines.append(subsep)
        emb_label = f"FLOAT32 Embeddings ({st['assumptions']['kib_per_embedding']:.1f} KiB/chunk)"
        split_label = (
            f"Tiered Split ({100.0 - inp['cold_hdd_pct']:.0f}% SSD / "
            f"{inp['cold_hdd_pct']:.0f}% HDD)"
        )
        lines.append(
            f"  {'Base Rows (Nodes + Edges + Chunks)':<36}: {_fmt_gib(comp['base_rows_total_gib'])}"
        )
        lines.append(
            f"  {'Reverse Covering STORING (~2.0x E)':<36}: {_fmt_gib(comp['reverse_covering_indexes_gib'])}"
        )
        lines.append(
            f"  {'FTS TOKENLIST + Search (~0.5x txt)':<36}: {_fmt_gib(comp['fts_tokenlist_and_search_index_gib'])}"
        )
        lines.append(
            f"  {emb_label:<36}: {_fmt_gib(comp['vector_embeddings_float32_gib'])}"
        )
        lines.append(
            f"  {'ScaNN Vector Index (~1.3x vectors)':<36}: {_fmt_gib(comp['scann_vector_index_gib'])}"
        )
        lines.append(
            f"  {'TOTAL LOGICAL STORAGE':<36}: {_fmt_gib(st['total_gib'])}"
        )
        lines.append(
            f"  {split_label:<36}: {_fmt_gib(st['ssd_gib'])} SSD + {_fmt_gib(st['hdd_gib'])} HDD"
        )
        lines.append(
            f"  {'Locality Group Savings (5x HDD)':<36}: "
            f"{_fmt_money(cost['storage_monthly_usd']['locality_group_savings_usd'])}/mo "
            f"(-{cost['storage_monthly_usd']['locality_group_savings_pct']:.1f}% vs. all-SSD "
            f"{_fmt_money(cost['storage_monthly_usd']['all_ssd_baseline_usd'])}/mo)"
        )

        # 3. Cloud Spanner Enterprise Monthly Cost Breakdown
        lines.append("")
        lines.append(
            f"3. Cloud Spanner {edition.upper()} Monthly Cost Breakdown ({cost['city']})"
        )
        lines.append(subsep)
        cmo = cost["compute_monthly_usd"]
        smo = cost["storage_monthly_usd"]
        dbo = cost["data_boost"]
        dr = cost["dr_backup"]
        tot = cost["totals_monthly_usd"]

        lines.append(
            f"  {'Compute (On-Demand / 1-Yr / 3-Yr)':<36}: "
            f"{_fmt_money(cmo['on_demand'])} / {_fmt_money(cmo['cud_1yr'])} / "
            f"{_fmt_money(cmo['cud_3yr'])} /mo"
        )
        if inp["ro_replicas"] > 0:
            ro_label = f"Optional RO Replicas ({inp['ro_replicas']}x)"
            lines.append(
                f"  {ro_label:<36}: "
                f"{_fmt_money(cmo['ro_replicas_on_demand'])} / "
                f"{_fmt_money(cmo['ro_replicas_cud_1yr'])} / "
                f"{_fmt_money(cmo['ro_replicas_cud_3yr'])} /mo"
            )
        lines.append(
            f"  {'Storage (SSD + HDD Tiered)':<36}: "
            f"{_fmt_money(smo['tiered_total_usd'])}/mo "
            f"(SSD {_fmt_money(smo['ssd_usd'])} + HDD {_fmt_money(smo['hdd_usd'])})"
        )
        lines.append(
            f"  {'Primary Backup Storage':<36}: "
            f"{_fmt_money(smo['primary_backup_usd'])}/mo "
            f"({'AUTOMATIC same-region, $0.00/mo cross-region egress' if smo['primary_backup_enabled'] else 'NONE — workshop sandbox, $0.00/mo egress'})"
        )
        if dr["enabled"]:
            dr_label = f"Cross-Region DR ({dr['dr_backup_region']})"
            lines.append(
                f"  {dr_label:<36}: "
                f"{_fmt_money(dr['total_dr_monthly_recurring_usd'])}/mo recurring "
                f"(DR storage {_fmt_money(dr['dr_backup_storage_monthly_usd'])} + "
                f"est. 15% delta egress {_fmt_money(dr['monthly_delta_copy_egress_usd'])}; "
                f"initial full copy egress: {_fmt_money(dr['initial_full_copy_egress_usd'])})"
            )
        db_label = f"Data Boost ({dbo['monthly_allowance_1000_spu_hrs']:,.2f}k SPU-hr/mo)"
        lines.append(
            f"  {db_label:<36}: {_fmt_money(dbo['monthly_usd'])}/mo"
        )
        lines.append(
            f"  {'TOTAL SPANNER (OD / 1-Yr / 3-Yr)':<36}: "
            f"{_fmt_money(tot['on_demand'])} / {_fmt_money(tot['cud_1yr'])} / "
            f"{_fmt_money(tot['cud_3yr'])} /mo"
        )

        # 4. 3-Store Legacy TCO Comparison
        lines.append("")
        lines.append("4. Legacy 3-Store Stack vs. Consolidated Cloud Spanner Enterprise TCO")
        lines.append(subsep)
        if tco["is_dev_sandbox_comparison"]:
            lines.append(f"  [Note] {tco['comparison_note']}")
        neo4j_lbl = (
            "Neo4j Dev Sandbox Instance"
            if tco["is_dev_sandbox_comparison"]
            else "Neo4j Enterprise HA Cluster"
        )
        vec_lbl = (
            "Managed Vector DB (Dev Tier)"
            if tco["is_dev_sandbox_comparison"]
            else "Managed Vector Database HA"
        )
        search_lbl = (
            "Managed OpenSearch/Elastic (Dev)"
            if tco["is_dev_sandbox_comparison"]
            else "Managed OpenSearch/Elastic HA"
        )
        lines.append(
            f"  {neo4j_lbl:<36}: "
            f"{_fmt_money(lcomp['neo4j_enterprise_ha_cluster'])}/mo"
        )
        lines.append(
            f"  {vec_lbl:<36}: "
            f"{_fmt_money(lcomp['managed_vector_db_ha'])}/mo"
        )
        lines.append(
            f"  {search_lbl:<36}: "
            f"{_fmt_money(lcomp['managed_opensearch_elasticsearch_ha'])}/mo"
        )
        lines.append(
            f"  {'CDC / ETL Sync Infra & DLQ':<36}: "
            f"{_fmt_money(lcomp['cdc_etl_sync_infrastructure'])}/mo"
        )
        lines.append(
            f"  {'Legacy 3-Store Infra Subtotal':<36}: "
            f"{_fmt_money(lcomp['infra_only_subtotal'])}/mo"
        )
        lines.append(
            f"  {'Multi-Store SRE & Eng Ops Overhead':<36}: "
            f"{_fmt_money(lcomp['multi_store_sre_eng_overhead'])}/mo"
        )
        lines.append(
            f"  {'Legacy 3-Store Fully Loaded Total':<36}: "
            f"{_fmt_money(lcomp['fully_loaded_total'])}/mo"
        )
        lines.append("")

        si = tco["savings_vs_infra_only"]
        sf = tco["savings_vs_fully_loaded"]
        lines.append(
            f"  {'Net Savings (Infra-Only)   — OD':<36}: "
            f"{_fmt_money(si['spanner_on_demand']['monthly_usd'])}/mo "
            f"({si['spanner_on_demand']['savings_pct']:.1f}%, "
            f"{_fmt_money(si['spanner_on_demand']['annual_usd'])}/yr)"
        )
        lines.append(
            f"  {'Net Savings (Infra-Only)   — 3Y CUD':<36}: "
            f"{_fmt_money(si['spanner_cud_3yr']['monthly_usd'])}/mo "
            f"({si['spanner_cud_3yr']['savings_pct']:.1f}%, "
            f"{_fmt_money(si['spanner_cud_3yr']['annual_usd'])}/yr)"
        )
        lines.append(
            f"  {'Net Savings (Fully Loaded) — OD':<36}: "
            f"{_fmt_money(sf['spanner_on_demand']['monthly_usd'])}/mo "
            f"({sf['spanner_on_demand']['savings_pct']:.1f}%, "
            f"{_fmt_money(sf['spanner_on_demand']['annual_usd'])}/yr)"
        )
        lines.append(
            f"  {'Net Savings (Fully Loaded) — 3Y CUD':<36}: "
            f"{_fmt_money(sf['spanner_cud_3yr']['monthly_usd'])}/mo "
            f"({sf['spanner_cud_3yr']['savings_pct']:.1f}%, "
            f"{_fmt_money(sf['spanner_cud_3yr']['annual_usd'])}/yr)"
        )
        lines.append(
            f"  {'Zero-Sync-Lag Benefit':<36}: "
            f"{tco['architectural_benefits']['consistency_model']}"
        )
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sizing_calculator.py",
        description=(
            "Enterprise Sizing & 3-Store Cost TCO Calculator for Cloud Spanner "
            "Enterprise (Graph + FTS + ScaNN Vector Search). Defaults to Bangkok "
            "single-region (regional-asia-southeast3, same-region backups, $0.00/mo "
            "cross-region egress) with Singapore (asia-southeast1) ONLY for Vertex AI, "
            "Model Armor & DLP. Verified 2026 rates for Bangkok, Singapore (comparison), "
            "and Iowa reference (us-central1)."
        ),
    )
    parser.add_argument(
        "--preset",
        choices=("workshop", "small", "medium", "large", "all"),
        default=None,
        help="Built-in T-shirt preset (default: all when no custom flags/worksheet provided).",
    )
    parser.add_argument(
        "--nodes",
        type=int,
        default=None,
        help="Custom graph node count (e.g., 1000000).",
    )
    parser.add_argument(
        "--edges",
        type=int,
        default=None,
        help="Custom graph edge count (e.g., 5000000).",
    )
    parser.add_argument(
        "--chunks",
        type=int,
        default=None,
        help="Custom unstructured document chunk count (e.g., 2500000).",
    )
    parser.add_argument(
        "--qps",
        type=float,
        default=None,
        help="Custom peak sustained query + write throughput (QPS).",
    )
    parser.add_argument(
        "--write-pct",
        type=float,
        default=None,
        help="Percentage of traffic that is writes (0-100, default: 15).",
    )
    parser.add_argument(
        "--vector-dim",
        type=int,
        default=None,
        help="FLOAT32 embedding dimensionality (default: 768 -> 3.0 KiB/chunk).",
    )
    parser.add_argument(
        "--cold-hdd-pct",
        type=float,
        default=None,
        help="Percentage of storage tiered to HDD via Locality Groups (0-100).",
    )
    parser.add_argument(
        "--region",
        choices=("asia-southeast3", "asia-southeast1", "us-central1"),
        default=None,
        help="Primary Cloud Spanner region (default: asia-southeast3 Bangkok).",
    )
    parser.add_argument(
        "--dr-backup-region",
        choices=("none", "asia-southeast3", "asia-southeast1", "us-central1"),
        default=None,
        help=(
            "Optional cross-region DR backup modeling override (default: none — pure "
            "same-region Bangkok backups at $0.0315/GiB-mo and $0.00/mo egress; pass "
            "e.g. asia-southeast1 only if explicitly modeling cross-region DR)."
        ),
    )
    parser.add_argument(
        "--edition",
        choices=("standard", "enterprise", "enterprise_plus"),
        default=None,
        help="Cloud Spanner edition (default: enterprise).",
    )
    parser.add_argument(
        "--ro-replicas",
        type=int,
        default=None,
        help="Optional additional read-only replicas per node (default: 0).",
    )
    parser.add_argument(
        "--data-boost-kspu-hrs",
        type=float,
        default=None,
        help="Override monthly Data Boost allowance in 1,000 SPU-hr units.",
    )
    parser.add_argument(
        "--workshop-hours",
        type=float,
        default=DEFAULT_WORKSHOP_HOURS,
        help="Workshop lab duration in hours for sandbox estimate (default: 8.0).",
    )
    parser.add_argument(
        "--worksheet",
        type=str,
        default=None,
        help="Path to a customer scale JSON worksheet (e.g., starter/tools/customer_scale_worksheet.json).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON output to stdout.",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        mode, specs = build_specs_from_args(args)
        scenarios = [evaluate_scenario(spec) for spec in specs]
    except (ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    first_spec = specs[0]
    payload: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "pricing_verified_year": 2026,
        "pricing_source": "https://cloud.google.com/spanner/pricing",
        "hours_per_month": HOURS_PER_MONTH,
        "mode": mode,
        "primary_region": first_spec.region,
        "dr_backup_region": first_spec.dr_backup_region,
        "edition": first_spec.edition,
        "cross_region_backup_egress_asia_per_gib_usd": (
            CROSS_REGION_BACKUP_EGRESS_ASIA_PER_GIB_USD
        ),
        "pricing_regions": PRICING_REGIONS,
        "scenarios": scenarios,
    }

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(render_text_report(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
