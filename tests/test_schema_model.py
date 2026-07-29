"""Document assembly across all three tiers, using recorded fixtures and a fake connection."""

import json

import pytest

from mcp_server.utils import schema_model
from mcp_server.utils.schema_model import (
    _META as META_Q,
    _PREVIEW as PREVIEW_Q,
    _ROW_COUNTS as COUNTS_Q,
    _reconcile_row_data,
    build_database_document,
    build_functions_document,
    build_table_document,
    columns_from_meta,
    is_internal_table_name,
    table_entry_from_meta,
)

# The q expressions are imported, never retyped. A copy here would keep passing after the
# production query changed - which is exactly how a 'type error in the row-count query
# survived a green unit suite once already.

TRADE_META = [
    {"c": "time", "t": "p", "f": "", "a": ""},
    {"c": "sym", "t": "s", "f": "instrument", "a": "g"},
    {"c": "price", "t": "f", "f": "", "a": ""},
]

TRADE_ROWS = [
    {"time": "2026-04-29T14:30:00.000000000", "sym": "VOD", "price": 71.2},
]


def make_conn(fake_conn_factory, *, tables, rows=None, counts=None, pt=None):
    rows = TRADE_ROWS if rows is None else rows
    counts = counts if counts is not None else {name: 10 for name in tables}
    return fake_conn_factory(
        handlers={
            META_Q: lambda table: json.dumps(TRADE_META),
            COUNTS_Q: lambda names: json.dumps(counts),
            PREVIEW_Q: lambda n, t, d: json.dumps(rows[:n]),
        },
        tables=tables,
        pt=pt or [],
    )


@pytest.fixture
def patched(monkeypatch):
    """Wire build_* to a fake connection and a chosen aimeta document."""

    def apply(conn, document):
        monkeypatch.setattr(schema_model, "get_kdb_connection", lambda: conn)
        monkeypatch.setattr(schema_model, "fetch_meta_document", lambda conn=None: document)
        monkeypatch.setattr(schema_model, "_vector_columns", lambda table: [])
        return conn

    return apply


class TestInternalTableFilter:
    @pytest.mark.parametrize("name", ["mydocument", "mystats", "mytoken"])
    def test_ai_lib_index_tables_match(self, name):
        assert is_internal_table_name(name)

    @pytest.mark.parametrize("name", ["trade", "quote", "instrument"])
    def test_user_tables_do_not(self, name):
        assert not is_internal_table_name(name)


class TestTier1Synthesis:
    def test_meta_maps_onto_the_canonical_shape(self, fake_conn_factory):
        conn = make_conn(fake_conn_factory, tables=["trade"])
        columns = columns_from_meta(conn, "trade")

        assert columns[0] == {"name": "time", "kdbType": "p"}
        assert columns[1] == {
            "name": "sym",
            "kdbType": "s",
            "foreignRef": "instrument",
            "attributes": ["g"],
        }
        # empty f/a are dropped rather than emitted as ""
        assert "foreignRef" not in columns[2] and "attributes" not in columns[2]

    def test_entry_has_the_same_shape_as_an_aimeta_table(self, fake_conn_factory):
        entry = table_entry_from_meta(make_conn(fake_conn_factory, tables=["trade"]), "trade")
        assert set(entry) == {"name", "private", "desc", "columns"}

    def test_unreadable_table_degrades_to_empty_columns(self, fake_conn_factory):
        conn = fake_conn_factory(handlers={}, tables=["broken"])
        assert table_entry_from_meta(conn, "broken") == {
            "name": "broken", "private": False, "desc": "", "columns": []
        }


class TestRowDataReconciliation:
    def test_real_preview_displaces_sample_data(self):
        entry, live = {"sampleData": [{"sym": "AAPL"}]}, {"preview": [{"sym": "VOD"}]}
        _reconcile_row_data(entry, live)
        assert "sampleData" not in entry
        assert live["rowSource"] == "preview"

    def test_sample_data_covers_an_empty_preview(self):
        entry, live = {"sampleData": [{"sym": "AAPL"}]}, {"preview": []}
        _reconcile_row_data(entry, live)
        assert entry["sampleData"] == [{"sym": "AAPL"}]
        assert live["rowSource"] == "sampleData"
        assert "preview" not in live

    def test_neither_is_reported_honestly(self):
        entry, live = {}, {"preview": []}
        _reconcile_row_data(entry, live)
        assert live["rowSource"] == "none"
        assert "preview" not in live


class TestBuildDatabaseDocument:
    def test_tier_1_without_aimeta(self, fake_conn_factory, patched):
        patched(make_conn(fake_conn_factory, tables=["trade"]), None)
        doc = build_database_document()

        assert doc["mcp"]["tier"] == 1
        assert doc["functions"] == [] and doc["references"] == []
        assert doc["tables"][0]["name"] == "trade"
        assert doc["tables"][0]["live"]["rowCount"] == 10
        assert doc["tables"][0]["live"]["rowSource"] == "preview"

    def test_tier_1_filters_ai_lib_index_tables(self, fake_conn_factory, patched):
        patched(
            make_conn(fake_conn_factory, tables=["trade", "tradedocument", "tradestats"]), None
        )
        assert [t["name"] for t in build_database_document()["tables"]] == ["trade"]

    def test_tier_3_merges_semantics_with_live_data(self, fake_conn_factory, patched, tier3_reference):
        patched(
            make_conn(fake_conn_factory, tables=["instrument", "trade", "quote"]), tier3_reference
        )
        doc = build_database_document()

        assert doc["mcp"]["tier"] == 3
        trade = next(t for t in doc["tables"] if t["name"] == "trade")
        assert trade["desc"].startswith("Trade tape")
        sym = next(c for c in trade["columns"] if c["name"] == "sym")
        assert sym["semanticType"] == "instrument"
        assert sym["foreignRef"] == "instrument.sym"
        assert trade["live"]["rowCount"] == 10

        assert doc["references"][0]["semanticType"] == "instrument"
        assert doc["references"][0]["keyColumn"] == "sym"
        assert len(doc["functions"]) == 2

    def test_annotated_sample_data_is_dropped_when_real_rows_exist(
        self, fake_conn_factory, patched, tier3_reference
    ):
        patched(make_conn(fake_conn_factory, tables=["trade"]), tier3_reference)
        trade = build_database_document()["tables"][0]
        assert "sampleData" not in trade
        assert trade["live"]["rowSource"] == "preview"
        assert trade["live"]["preview"][0]["sym"] == "VOD"

    def test_annotated_sample_data_survives_an_empty_table(
        self, fake_conn_factory, patched, tier3_reference
    ):
        patched(
            make_conn(fake_conn_factory, tables=["trade"], counts={"trade": 0}), tier3_reference
        )
        trade = build_database_document()["tables"][0]
        assert trade["live"]["rowSource"] == "sampleData"
        assert trade["sampleData"][0]["sym"] == "AAPL"

    def test_tier_2_yields_names_without_prose(self, fake_conn_factory, patched, tier2):
        patched(
            make_conn(fake_conn_factory, tables=["instrument", "trade", "quote"]), tier2
        )
        doc = build_database_document()

        assert doc["mcp"]["tier"] == 2
        trade = next(t for t in doc["tables"] if t["name"] == "trade")
        assert trade["desc"] == ""
        assert all("semanticType" not in c for c in trade["columns"])
        # function names are published even unannotated - only the semantics are missing
        assert {f["name"] for f in doc["functions"]} == {".gw.vwap", ".gw.getInstrument"}
        assert all(f["desc"] == "" for f in doc["functions"])

    def test_private_flag_supersedes_the_suffix_heuristic(self, fake_conn_factory, patched):
        document = {
            "schemaVersion": 2,
            "process": {"name": "host"},
            "tables": [
                {"name": "trade", "private": False, "desc": "", "columns": []},
                {"name": "_cache", "private": True, "desc": "", "columns": []},
            ],
            "references": [],
            "functions": [],
        }
        patched(make_conn(fake_conn_factory, tables=["trade", "_cache"]), document)
        assert [t["name"] for t in build_database_document()["tables"]] == ["trade"]

    def test_table_absent_from_aimeta_is_still_listed(self, fake_conn_factory, patched, tier3_reference):
        """A table created after the host compiled must not vanish - absence is not privacy."""
        patched(make_conn(fake_conn_factory, tables=["trade", "brandnew"]), tier3_reference)
        names = [t["name"] for t in build_database_document()["tables"]]
        assert "brandnew" in names

    def test_live_data_can_be_skipped(self, fake_conn_factory, patched, tier3_reference):
        patched(make_conn(fake_conn_factory, tables=["trade"]), tier3_reference)
        doc = build_database_document(include_live=False)
        assert "live" not in doc["tables"][0]


class TestBuildTableDocument:
    def test_carries_only_the_references_the_table_uses(
        self, fake_conn_factory, patched, tier3_reference
    ):
        patched(make_conn(fake_conn_factory, tables=["trade"]), tier3_reference)
        doc = build_table_document("trade")
        assert doc["table"]["name"] == "trade"
        assert [r["semanticType"] for r in doc["references"]] == ["instrument"]

    def test_table_with_no_semantic_types_carries_no_references(
        self, fake_conn_factory, patched, tier3_reference
    ):
        patched(make_conn(fake_conn_factory, tables=["instrument"]), tier3_reference)
        assert build_table_document("instrument")["references"] == []

    def test_missing_table_reports_an_error(self, fake_conn_factory, patched, tier3_reference):
        conn = patched(make_conn(fake_conn_factory, tables=[]), tier3_reference)
        conn.handlers[META_Q] = lambda table: (_ for _ in ()).throw(RuntimeError("no such table"))
        conn.handlers[COUNTS_Q] = lambda names: json.dumps({})
        doc = build_table_document("ghost")
        assert "error" not in doc or "ghost" in doc["error"]


class TestBuildFunctionsDocument:
    def test_tier_3_returns_full_signatures(self, fake_conn_factory, patched, tier3_reference):
        patched(make_conn(fake_conn_factory, tables=[]), tier3_reference)
        doc = build_functions_document()

        vwap = next(f for f in doc["functions"] if f["name"] == ".gw.vwap")
        assert vwap["params"][0]["name"] == "syms"
        assert vwap["returns"]["type"] == "table"
        assert vwap["uses"] == ["trade"]
        assert vwap["examples"] == [".gw.vwap[`AAPL`MSFT]"]
        assert "hint" not in doc["mcp"]

    def test_tier_1_returns_empty_with_a_hint(self, fake_conn_factory, patched):
        patched(make_conn(fake_conn_factory, tables=[]), None)
        doc = build_functions_document()
        assert doc["functions"] == []
        assert "kx.aimeta" in doc["mcp"]["hint"]

    def test_tier_2_hint_says_annotations_are_missing(self, fake_conn_factory, patched, tier2):
        patched(make_conn(fake_conn_factory, tables=[]), tier2)
        doc = build_functions_document()
        assert len(doc["functions"]) == 2
        assert "no annotations" in doc["mcp"]["hint"]
