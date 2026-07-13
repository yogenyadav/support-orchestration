"""Tests for production DB adapters — scope enforcement + param conversion."""

from __future__ import annotations

import pytest

from support_orchestration.tools.adapters.db_adapters import (
    _named_to_positional,
    _named_to_qmark,
)

# ── Param conversion helpers ──────────────────────────────────────────────────

def test_named_to_positional_basic():
    sql, vals = _named_to_positional("SELECT * FROM t WHERE id = :entity_id", {"entity_id": "42"})
    assert sql == "SELECT * FROM t WHERE id = $1"
    assert vals == ["42"]


def test_named_to_positional_multiple():
    sql, vals = _named_to_positional(
        "SELECT * FROM t WHERE a = :a AND b = :b",
        {"a": "x", "b": "y"},
    )
    assert sql == "SELECT * FROM t WHERE a = $1 AND b = $2"
    assert vals == ["x", "y"]


def test_named_to_positional_no_params():
    sql, vals = _named_to_positional("SELECT 1", {})
    assert sql == "SELECT 1"
    assert vals == []


def test_named_to_qmark_basic():
    sql, vals = _named_to_qmark("SELECT * FROM t WHERE id = :entity_id", {"entity_id": "99"})
    assert sql == "SELECT * FROM t WHERE id = ?"
    assert vals == ["99"]


def test_named_to_qmark_multiple():
    sql, vals = _named_to_qmark(
        "SELECT * FROM t WHERE a = :a AND b = :b",
        {"a": 1, "b": 2},
    )
    assert sql == "SELECT * FROM t WHERE a = ? AND b = ?"
    assert vals == [1, 2]


# ── OracleDbAdapter — scope enforcement ──────────────────────────────────────

def test_oracle_import_error_on_missing_oracledb(monkeypatch):
    """OracleDbAdapter raises a helpful error when oracledb is not installed."""
    import builtins
    real_import = builtins.__import__

    def fake_import(name: str, *args, **kwargs):
        if name == "oracledb":
            raise ImportError("No module named 'oracledb'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(RuntimeError, match="oracledb"):
        from support_orchestration.tools.adapters.db_adapters import OracleDbAdapter
        OracleDbAdapter("acme", "host", 1521, "svc", "user", "pw")


def test_oracle_scope_check():
    """OracleDbAdapter raises PermissionError on wrong client_id."""
    import unittest.mock as mock

    with mock.patch.dict("sys.modules", {"oracledb": mock.MagicMock()}):
        from importlib import reload

        import support_orchestration.tools.adapters.db_adapters as db_mod
        reload(db_mod)
        adapter = db_mod.OracleDbAdapter("acme", "host", 1521, "svc", "user", "pw")

    with pytest.raises(PermissionError, match="scope violation"):
        adapter._check_scope("other-client")


def test_oracle_correct_scope():
    """OracleDbAdapter does not raise when client_id matches."""
    import unittest.mock as mock

    with mock.patch.dict("sys.modules", {"oracledb": mock.MagicMock()}):
        from importlib import reload

        import support_orchestration.tools.adapters.db_adapters as db_mod
        reload(db_mod)
        adapter = db_mod.OracleDbAdapter("acme", "host", 1521, "svc", "user", "pw")

    adapter._check_scope("acme")  # should not raise


# ── PostgresDbAdapter — scope enforcement ────────────────────────────────────

def test_postgres_import_error_on_missing_asyncpg(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def fake_import(name: str, *args, **kwargs):
        if name == "asyncpg":
            raise ImportError("No module named 'asyncpg'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(RuntimeError, match="asyncpg"):
        from support_orchestration.tools.adapters.db_adapters import PostgresDbAdapter
        PostgresDbAdapter("acme", "postgresql://user:pw@host/db")


def test_postgres_scope_check():
    import unittest.mock as mock

    with mock.patch.dict("sys.modules", {"asyncpg": mock.MagicMock()}):
        from importlib import reload

        import support_orchestration.tools.adapters.db_adapters as db_mod
        reload(db_mod)
        adapter = db_mod.PostgresDbAdapter("acme", "postgresql://user:pw@host/db")

    with pytest.raises(PermissionError, match="scope violation"):
        adapter._check_scope("wrong-client")


# ── MsSqlDbAdapter — scope enforcement ───────────────────────────────────────

def test_mssql_import_error_on_missing_pyodbc(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def fake_import(name: str, *args, **kwargs):
        if name == "pyodbc":
            raise ImportError("No module named 'pyodbc'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(RuntimeError, match="pyodbc"):
        from support_orchestration.tools.adapters.db_adapters import MsSqlDbAdapter
        MsSqlDbAdapter("acme", "srv", "db", "user", "pw")


def test_mssql_scope_check():
    import unittest.mock as mock

    with mock.patch.dict("sys.modules", {"pyodbc": mock.MagicMock()}):
        from importlib import reload

        import support_orchestration.tools.adapters.db_adapters as db_mod
        reload(db_mod)
        adapter = db_mod.MsSqlDbAdapter("acme", "srv", "db", "user", "pw")

    with pytest.raises(PermissionError, match="scope violation"):
        adapter._check_scope("wrong")
