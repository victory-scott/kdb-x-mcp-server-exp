"""End-to-end tests against a real KDB-X process. Skipped unless one is listening.

These are the only tests that exercise the actual transports - the HTTP /meta fetch, the qIPC
fallback, and real `meta`/`.Q.pt`/preview queries. Everything else runs off recorded fixtures.

Point KDBX_DB_HOST/KDBX_DB_PORT at the host before running. The aimeta demos give ready-made
hosts at all three tiers:

    tier 3   cd <aimeta>/demos/authoring && q host-annotated.q       # :5013
    tier 2   cp <aimeta>/demos/authoring/host.q /tmp/bare/ && cd /tmp/bare && q host.q
    tier 1   q -p 5013

Then:  KDBX_DB_PORT=5013 uv run pytest tests/test_live_integration.py -v
"""

import os
import socket

import pytest

HOST = os.environ.get("KDBX_DB_HOST", "127.0.0.1")
PORT = int(os.environ.get("KDBX_DB_PORT", "5000"))


def _listening() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        return sock.connect_ex((HOST, PORT)) == 0


pytestmark = pytest.mark.skipif(
    not _listening(), reason=f"no KDB-X process listening on {HOST}:{PORT}"
)


@pytest.fixture(autouse=True)
def clean_cache():
    from mcp_server.utils.aimeta import invalidate_meta_cache

    invalidate_meta_cache()
    yield
    invalidate_meta_cache()


@pytest.fixture
def tier():
    from mcp_server.utils.aimeta import fetch_meta_document, tier_from_document

    return tier_from_document(fetch_meta_document())


class TestTransport:
    def test_document_is_reachable_or_absent_cleanly(self, tier):
        assert tier in (1, 2, 3)

    def test_fetched_document_is_already_normalized(self, tier):
        """No padding survives the fetch, against a live host rather than a fixture."""
        from mcp_server.utils.aimeta import fetch_meta_document

        if tier == 1:
            pytest.skip("no aimeta on this host")
        for table in fetch_meta_document()["tables"]:
            assert table.get("reference") != ""
            assert table.get("labels") != [""]
            assert "sampleData" not in table or table["sampleData"]
            for column in table["columns"]:
                assert column.get("foreignRef") != ""

    def test_cache_is_reused_then_cleared(self, tier):
        from mcp_server.utils.aimeta import fetch_meta_document, invalidate_meta_cache

        first = fetch_meta_document()
        assert fetch_meta_document() is first, "second read should hit the cache"
        invalidate_meta_cache()
        assert (fetch_meta_document() is None) == (first is None)


class TestDatabaseDocument:
    def test_every_table_has_columns_and_live_data(self):
        from mcp_server.utils.schema_model import build_database_document

        doc = build_database_document()
        assert doc["mcp"]["tier"] in (1, 2, 3)
        for table in doc["tables"]:
            assert table["name"] and "columns" in table
            assert table["live"]["rowSource"] in ("preview", "sampleData", "none")
            # A null count means the batched count query failed, not that the table is
            # empty - an empty table counts 0. Asserting int here is what catches a broken
            # q expression; allowing None silently disables the whole preview path.
            assert isinstance(table["live"]["rowCount"], int), (
                f"row count for '{table['name']}' came back null - the batched count query failed"
            )

    def test_populated_tables_yield_real_previews(self):
        """The preview path only runs when rowCount > 0, so it needs a populated table."""
        from mcp_server.utils.schema_model import build_database_document

        populated = [
            t for t in build_database_document()["tables"] if (t["live"]["rowCount"] or 0) > 0
        ]
        if not populated:
            pytest.skip("no populated tables on this host")
        for table in populated:
            assert table["live"]["rowSource"] == "preview"
            assert table["live"]["preview"], f"'{table['name']}' has rows but no preview"
            assert "sampleData" not in table

    def test_annotated_host_carries_semantics(self, tier):
        from mcp_server.utils.schema_model import build_database_document

        if tier != 3:
            pytest.skip("host is not annotated")
        doc = build_database_document()
        columns = [c for t in doc["tables"] for c in t["columns"]]
        assert any(c.get("semanticType") for c in columns)
        assert any(c.get("foreignRef") for c in columns)
        assert doc["references"] and doc["references"][0]["keyColumn"]

    def test_unannotated_host_still_lists_everything(self, tier):
        from mcp_server.utils.schema_model import build_database_document

        if tier != 2:
            pytest.skip("host is not tier 2")
        doc = build_database_document()
        assert doc["tables"]
        assert all(t["desc"] == "" for t in doc["tables"])
        # names are published even unannotated
        assert doc["functions"] and all(f["desc"] == "" for f in doc["functions"])

    def test_per_table_lookup_matches_the_full_walk(self):
        from mcp_server.utils.schema_model import build_database_document, build_table_document

        doc = build_database_document()
        if not doc["tables"]:
            pytest.skip("no tables on this host")
        name = doc["tables"][0]["name"]
        assert build_table_document(name)["table"]["columns"] == doc["tables"][0]["columns"]


class TestFunctionsDocument:
    def test_shape_holds_at_every_tier(self, tier):
        from mcp_server.utils.schema_model import build_functions_document

        doc = build_functions_document()
        assert isinstance(doc["functions"], list)
        if tier < 3:
            assert doc["mcp"]["hint"]
        else:
            assert all(f["name"] for f in doc["functions"])
