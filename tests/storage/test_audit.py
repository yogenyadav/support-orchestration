import json
import sqlite3

import pytest

from support_orchestration.storage.audit import AuditStore


def test_append_read_and_read_back(tmp_path):
    store = AuditStore(db_path=tmp_path / "audit.db")
    store.append_read(
        case_id="case-001",
        client="acme",
        tool_name="db_state_read",
        input_keys=["client_id", "entity_type", "entity_id"],
        output_size=512,
    )
    rows = store.reads_for_case("case-001")
    assert len(rows) == 1
    row = rows[0]
    assert row["case_id"] == "case-001"
    assert row["client"] == "acme"
    assert row["tool_name"] == "db_state_read"
    assert json.loads(row["input_keys"]) == ["client_id", "entity_type", "entity_id"]
    assert row["output_size"] == 512
    assert row["timestamp"]  # non-empty ISO timestamp


def test_append_is_monotonic(tmp_path):
    store = AuditStore(db_path=tmp_path / "audit.db")
    store.append_read(
        case_id="case-002", client="c1",
        tool_name="log_read", input_keys=["client_id"], output_size=100,
    )
    store.append_read(
        case_id="case-002", client="c1",
        tool_name="db_state_read", input_keys=["client_id", "entity_id"], output_size=50,
    )
    rows = store.reads_for_case("case-002")
    assert len(rows) == 2
    assert rows[1]["id"] > rows[0]["id"]
    assert rows[0]["tool_name"] == "log_read"
    assert rows[1]["tool_name"] == "db_state_read"


def test_append_approval(tmp_path):
    store = AuditStore(db_path=tmp_path / "audit.db")
    store.append_approval(
        case_id="case-003", client="acme",
        verb="/approve", message="Fix approved — apply UPDATE order SET state='released'",
        mirrored_to_jira=True,
    )
    approvals = store.approvals_for_case("case-003")
    assert len(approvals) == 1
    a = approvals[0]
    assert a["verb"] == "/approve"
    assert a["mirrored_to_jira"] == 1
    assert "UPDATE order" in a["message"]


def test_reads_for_different_cases_are_isolated(tmp_path):
    store = AuditStore(db_path=tmp_path / "audit.db")
    store.append_read(case_id="case-A", client="c1", tool_name="log_read",
                      input_keys=["client_id"], output_size=10)
    store.append_read(case_id="case-B", client="c2", tool_name="db_state_read",
                      input_keys=["client_id"], output_size=20)
    assert len(store.reads_for_case("case-A")) == 1
    assert len(store.reads_for_case("case-B")) == 1
    assert store.reads_for_case("case-A")[0]["tool_name"] == "log_read"
