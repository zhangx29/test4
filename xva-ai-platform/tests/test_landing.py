from datetime import date

import pytest
from openpyxl import Workbook
from openpyxl.workbook.defined_name import DefinedName

from conftest import STORE_FILES, backends
from xva_landing import land_mfl_workbook
from xva_mcp import db
from xva_landing.land_stored_procs import ProcedureNotAllowed, build_call, run_proc

AS_OF = date(2026, 11, 20)


def make_workbook(path, rows, control, as_of=AS_OF):
    wb = Workbook()
    ws = wb.active
    ws.title = "Output"
    ws["A1"] = as_of
    ws.append([])
    ws["A3"], ws["B3"], ws["C3"] = "counterparty_id", "measure", "value"
    for i, (cp, measure, value) in enumerate(rows, start=4):
        ws[f"A{i}"], ws[f"B{i}"], ws[f"C{i}"] = cp, measure, value
    last = 3 + len(rows)
    ws["E1"] = control
    wb.defined_names["XVA_ASOF"] = DefinedName("XVA_ASOF", attr_text="Output!$A$1")
    wb.defined_names["XVA_RESULTS"] = DefinedName("XVA_RESULTS", attr_text=f"Output!$A$3:$C${last}")
    wb.defined_names["XVA_CONTROL_TOTAL"] = DefinedName("XVA_CONTROL_TOTAL", attr_text="Output!$E$1")
    wb.save(path)
    return path


ROWS = [("CP001", "CVA", -1_000_000.0), ("CP001", "FVA", -250_000.0), ("CP002", "CVA", -400_000.0)]


@pytest.mark.parametrize("backend", backends())
def test_clean_workbook_lands_and_publishes_status(tmp_path, backend):
    store = tmp_path / STORE_FILES[backend]
    wb = make_workbook(tmp_path / "mfl.xlsx", ROWS, control=-1_650_000.0)
    result = land_mfl_workbook.land(wb, AS_OF, store, loaded_by="tester")
    assert result.passed and result.rows == 3
    with db.connect(store) as con:
        assert con.execute("SELECT COUNT(*) FROM mfl_result").fetchone()[0] == 3
        assert con.execute("SELECT checks_passed FROM data_status").fetchone()[0] is True
    # Landing the same file again is idempotent.
    land_mfl_workbook.land(wb, AS_OF, store, loaded_by="tester")
    with db.connect(store) as con:
        assert con.execute("SELECT COUNT(*) FROM mfl_result").fetchone()[0] == 3
        assert con.execute("SELECT COUNT(*) FROM data_status").fetchone()[0] == 1


@pytest.mark.parametrize("backend", backends())
def test_errors_wrong_date_and_untied_totals_fail_but_still_publish(tmp_path, backend):
    store = tmp_path / STORE_FILES[backend]
    rows = ROWS + [("CP003", "CVA", "#N/A")]
    wb = make_workbook(tmp_path / "bad.xlsx", rows, control=-1_000.0, as_of=date(2026, 11, 19))
    result = land_mfl_workbook.land(wb, AS_OF, store, loaded_by="tester")
    text = " | ".join(result.failures)
    assert not result.passed
    assert "does not match business date" in text
    assert "Excel error #N/A" in text
    assert "do not tie to control cell" in text
    with db.connect(store) as con:
        assert con.execute("SELECT COUNT(*) FROM mfl_result").fetchone()[0] == 0
        passed, notes = con.execute("SELECT checks_passed, notes FROM data_status").fetchone()
        assert passed is False and "control cell" in notes


def test_stored_procs_are_allowlisted_and_bound():
    sql, values = build_call("xva.usp_GetCollateralBalances", {"counterparty_id": "CP001", "as_of": AS_OF})
    assert sql == "{CALL xva.usp_GetCollateralBalances(?, ?)}"
    assert values == [AS_OF, "CP001"]
    with pytest.raises(ProcedureNotAllowed):
        build_call("xva.usp_DeleteEverything", {})
    with pytest.raises(ProcedureNotAllowed):
        build_call("xva.usp_GetNettingSetMap", {"as_of": AS_OF, "extra": 1})


def test_run_proc_uses_parameters_not_string_building():
    class Cursor:
        def execute(self, sql, values):
            self.sql, self.values = sql, values

        def fetchall(self):
            return [("row",)]

        def close(self):
            pass

    class Conn:
        def __init__(self):
            self.cur = Cursor()

        def cursor(self):
            return self.cur

    conn = Conn()
    assert run_proc(conn, "xva.usp_GetNettingSetMap", {"as_of": AS_OF}) == [("row",)]
    assert conn.cur.sql == "{CALL xva.usp_GetNettingSetMap(?)}" and conn.cur.values == [AS_OF]
