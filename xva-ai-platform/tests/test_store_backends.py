"""SQLite (default) and DuckDB (optional) must give the same answers from the same sample data."""

import sqlite3
from datetime import date

import pytest

from conftest import STORE_FILES, backends
from sample_data.make_sample_data import build
from xva_mcp import db, store

D1, D2, LAST = date(2026, 11, 19), date(2026, 11, 20), date(2026, 11, 20)


def _answers(path, monkeypatch):
    monkeypatch.setenv("XVA_STORE_PATH", str(path))

    def dump(model):
        out = model.model_dump(mode="json")
        out.get("provenance", {}).pop("generated_at", None)
        return out

    return {
        "dates": [d.isoformat() for d in store.business_dates()],
        "status": dump(store.data_status(LAST)),
        "clean": store.latest_clean_date().isoformat(),
        "summary": dump(store.xva_summary(LAST, "counterparty", 5)),
        "movers": dump(store.xva_movers(D1, D2, "book", 10)),
        "timeseries": dump(store.counterparty_timeseries("CP001", date(2026, 11, 1), LAST)),
        "sens": dump(store.sensitivities(LAST, "IR", "delta", "bucket", None, 10)),
        "pnl": dump(store.pnl_attribution(date(2026, 10, 30), LAST, "desk", None)),
        "search": dump(store.search_counterparties("maple", 5)),
    }


@pytest.mark.parametrize("backend", backends())
def test_backend_returns_python_types(tmp_path, monkeypatch, backend):
    path = build(tmp_path / STORE_FILES[backend])
    assert db.backend_for(path) == backend
    monkeypatch.setenv("XVA_STORE_PATH", str(path))
    status = store.data_status(LAST)
    assert isinstance(status.sources[0].loaded_at.year, int)
    assert status.all_passed is False  # the sample MFL check fails on the last day
    assert store.latest_clean_date() == date(2026, 11, 19)
    assert all(isinstance(d, date) for d in store.business_dates())
    # Name search is case-insensitive on both backends.
    assert [m.counterparty_id for m in store.search_counterparties("MAPLE", 5).matches] == ["CP002"]


def test_duckdb_and_sqlite_agree(tmp_path, monkeypatch):
    pytest.importorskip("duckdb")
    lite = _answers(build(tmp_path / "s.sqlite"), monkeypatch)
    duck = _answers(build(tmp_path / "d.duckdb"), monkeypatch)
    assert lite.keys() == duck.keys()
    for key in lite:
        _same(lite[key], duck[key], key)


def _same(a, b, where):
    """Equal, allowing for summation-order differences in the last digits of floats."""
    if isinstance(a, float) or isinstance(b, float):
        assert a == pytest.approx(b, rel=1e-9, abs=1e-6), where
    elif isinstance(a, dict):
        assert a.keys() == b.keys(), where
        for k in a:
            _same(a[k], b[k], f"{where}.{k}")
    elif isinstance(a, list):
        assert len(a) == len(b), where
        for i, (x, y) in enumerate(zip(a, b)):
            _same(x, y, f"{where}[{i}]")
    else:
        assert a == b, where


def test_backend_choice(monkeypatch):
    monkeypatch.delenv("XVA_STORE_BACKEND", raising=False)
    assert db.backend_for("x.sqlite") == "sqlite"
    assert db.backend_for("x.db") == "sqlite"
    assert db.backend_for("X.DUCKDB") == "duckdb"
    monkeypatch.setenv("XVA_STORE_BACKEND", "sqlite")
    assert db.backend_for("x.duckdb") == "sqlite"
    monkeypatch.setenv("XVA_STORE_BACKEND", "oracle")
    with pytest.raises(ValueError):
        db.backend_for("x.db")


def test_missing_duckdb_gives_a_clear_message(tmp_path, monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_duckdb(name, *args, **kwargs):
        if name == "duckdb":
            raise ImportError("No module named 'duckdb'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_duckdb)
    assert db.duckdb_available() is False
    with pytest.raises(db.BackendUnavailable, match="XVA_STORE_BACKEND=sqlite"):
        with db.connect(tmp_path / "x.duckdb"):
            pass


def test_sqlite_read_only_connection_cannot_write(tmp_path):
    path = build(tmp_path / "folder with spaces" / "ro.sqlite")
    with db.connect(path, read_only=True) as con:
        with pytest.raises(sqlite3.OperationalError):
            con.execute("DELETE FROM xva_value")
