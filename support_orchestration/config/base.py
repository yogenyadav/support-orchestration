"""Base configuration — defaults valid across all clients."""

from __future__ import annotations

import os
from pathlib import Path

# ── Model routing (pin exact strings; re-verify at build time) ────────────────
MODEL_HAIKU  = "claude-haiku-4-5"      # classify / route / parse / dialect turns
MODEL_SONNET = "claude-sonnet-4-6"     # diagnose / synthesize / triage
MODEL_OPUS   = "claude-opus-4-8"       # novel diagnosis + final fix determination

# ── Agent loop caps ───────────────────────────────────────────────────────────
MAX_TURNS_ORCHESTRATOR = 8
MAX_TURNS_SUBAGENT     = 12

# ── SLA thresholds ────────────────────────────────────────────────────────────
SLA_TIGHT_SECONDS = 900   # escalate if fewer than 15 min remain

# ── Paths ─────────────────────────────────────────────────────────────────────
REPO_ROOT   = Path(__file__).parents[3]
MAPS_DIR    = REPO_ROOT / "maps"
SKILLS_DIR  = REPO_ROOT / "skills"
CLIENTS_DIR = REPO_ROOT / "clients"

# ── Concurrency ───────────────────────────────────────────────────────────────
MAX_CONCURRENT_ORCHESTRATORS = 10

# ── Prompt cache TTL ─────────────────────────────────────────────────────────
CACHE_TTL = "1h"  # passed to cache_control on stable system prompt + lifecycle map

# ── Secrets (from environment; never hardcoded) ───────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ATLASSIAN_BASE_URL = os.environ.get("ATLASSIAN_BASE_URL", "")
ATLASSIAN_API_TOKEN = os.environ.get("ATLASSIAN_API_TOKEN", "")
ATLASSIAN_USER = os.environ.get("ATLASSIAN_USER", "")
ATLASSIAN_PROJECT_KEY = os.environ.get("ATLASSIAN_PROJECT_KEY", "WH")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_BASE_ORG = os.environ.get("GITHUB_BASE_ORG", "")
GITHUB_CLIENT_ORG_PREFIX = os.environ.get("GITHUB_CLIENT_ORG_PREFIX", "client-")
PHOENIX_BASE_URL = os.environ.get("PHOENIX_BASE_URL", "")
PHOENIX_API_TOKEN = os.environ.get("PHOENIX_API_TOKEN", "")
VECTOR_STORE_DSN = os.environ.get("VECTOR_STORE_DSN", "")   # postgresql://...
TEAMS_APP_ID = os.environ.get("TEAMS_APP_ID", "")
TEAMS_APP_PASSWORD = os.environ.get("TEAMS_APP_PASSWORD", "")
TEAMS_TENANT_ID = os.environ.get("TEAMS_TENANT_ID", "")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
