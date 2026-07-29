"""Assemble one JSON document shape for table discovery, at every aimeta tier.

The shape is aimeta's meta.json, plus exactly two server-owned additions:

    doc["mcp"]              tier, transport and fetch time - computed here, never from the wire
    table["live"]           rowCount, partitioned, preview - queried live, never from aimeta

The `live` block is the only merge point. aimeta deliberately carries no row counts, no
partition state and no real rows (its `sampleData` is illustrative, not sampled), so those
stay live regardless of tier.

Below tier 2 there is no aimeta document at all, so `table_entry_from_meta` builds the same
shape from `meta` - whose c/t/f/a map exactly onto name/kdbType/foreignRef/attributes, the
same mapping aimeta uses for its own in-host fallback. `functions` stays empty there: aimeta
scans namespaces for names, but it runs inside a host whose author opted in, and scanning an
arbitrary process yields unfiltered internals with no signal for what is meant to be callable.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from mcp_server.utils.aimeta import (
    SUPPORTED_SCHEMA_VERSION,
    TIER_NO_AIMETA,
    fetch_meta_document,
    tier_from_document,
)
from mcp_server.utils.embeddings_helpers import get_embedding_config
from mcp_server.utils.kdbx import get_kdb_connection

logger = logging.getLogger(__name__)

DEFAULT_PREVIEW_ROWS = 3

# Fallback filter for tier 1, where nothing tells us which tables are internal. These are the
# index tables the KDB-X AI libraries create alongside a user table. From tier 2 up, aimeta's
# `private` flag supersedes this.
_INTERNAL_TABLE_SUFFIXES = ("document", "stats", "token")

# Batched so a wide database costs one round trip rather than one per table. Each count is
# individually trapped: an unreadable table yields null rather than failing the whole batch.
# Keys stay symbols - .j.j signals 'type on a dict with char-vector keys.
_ROW_COUNTS = '{.j.j x!@[{count get x};;0Nj] each x}'

_META = '{.j.j 0!meta x}'

# Column projection happens in q so embedding vectors never cross the wire, and .j.j does the
# encoding so kdb temporal types survive the trip.
_PREVIEW = '{[n;t;d] r:n sublist get t; .j.j ((cols r) except d)#r}'
_PREVIEW_PARTITIONED = '{[n;t;d] r:.Q.ind[get t;til n]; .j.j ((cols r) except d)#r}'


def _decode(value: Any) -> Any:
    return value.decode("utf-8") if isinstance(value, bytes) else value


def _loads(value: Any) -> Any:
    return json.loads(_decode(value))


def is_internal_table_name(name: str) -> bool:
    """Tier-1 heuristic for AI-library index tables."""
    return name.endswith(_INTERNAL_TABLE_SUFFIXES)


def _vector_columns(table: str) -> list[str]:
    """Embedding columns to project away, so previews don't ship float vectors to the model."""
    try:
        embedding, _, _, sparse, _, _, _ = get_embedding_config(table)
    except Exception as error:
        logger.debug(f"No embedding config for table {table}: {error}")
        return []
    return [column for column in (embedding, sparse) if column]


def columns_from_meta(conn, table: str) -> list[dict]:
    """Build canonical column entries from `meta`. c->name, t->kdbType, f->foreignRef, a->attributes."""
    rows = _loads(conn(_META, table).py())
    columns = []
    for row in rows:
        column = {"name": row.get("c"), "kdbType": row.get("t")}
        if row.get("f"):
            column["foreignRef"] = row["f"]
        if row.get("a"):
            column["attributes"] = [row["a"]]
        columns.append(column)
    return columns


def table_entry_from_meta(conn, table: str) -> dict:
    """A canonical table entry built from native introspection alone.

    Used for a process with no aimeta, and for a table aimeta has not heard of. Degrades to
    empty columns rather than raising, so one unreadable table cannot blank the whole schema.
    """
    try:
        columns = columns_from_meta(conn, table)
    except Exception as error:
        logger.warning(f"Could not read meta for table '{table}': {error}")
        columns = []
    return {"name": table, "private": False, "desc": "", "columns": columns}


def live_table_names(conn) -> list[str]:
    return [_decode(name) for name in conn.tables(None).py()]


def _live_block(conn, table: str, counts: dict, partitioned: set, preview_rows: int) -> dict:
    row_count = counts.get(table)
    is_partitioned = table in partitioned
    live: dict[str, Any] = {"rowCount": row_count, "partitioned": is_partitioned}

    if not preview_rows or not row_count:
        live["preview"] = []
        return live

    size = min(preview_rows, row_count)
    query = _PREVIEW_PARTITIONED if is_partitioned else _PREVIEW
    try:
        live["preview"] = _loads(conn(query, size, table, _vector_columns(table)).py())
    except Exception as error:
        logger.warning(f"Could not preview table '{table}': {error}")
        live["preview"] = []
    return live


def _reconcile_row_data(entry: dict, live: dict) -> None:
    """Resolve live preview against annotated sampleData so a table carries only one of them.

    They answer different questions: `preview` is real rows (actual symbol values, null
    density, whether the table is even populated), `sampleData` is fabricated from @sampleRow
    literals and documents intended shape. Real wins - a model that trusts a made-up symbol
    writes a query that runs and returns nothing. But when there is nothing real to show
    (empty table, failed or too-expensive preview) the curated rows are better than silence.

    `live.rowSource` tells the model which kind it got, so it never mistakes one for the other.
    """
    if live.get("preview"):
        entry.pop("sampleData", None)
        live["rowSource"] = "preview"
    elif entry.get("sampleData"):
        live.pop("preview", None)
        live["rowSource"] = "sampleData"
    else:
        live.pop("preview", None)
        live["rowSource"] = "none"


def build_database_document(
    table_names: Optional[list[str]] = None,
    include_live: bool = True,
    preview_rows: int = DEFAULT_PREVIEW_ROWS,
) -> dict:
    """The unified document. Live table list is authoritative for existence; aimeta supplies semantics."""
    conn = get_kdb_connection()

    aimeta_doc = fetch_meta_document(conn=conn)
    tier = tier_from_document(aimeta_doc)

    by_name = {entry.get("name"): entry for entry in (aimeta_doc or {}).get("tables") or []}

    requested = table_names
    names = live_table_names(conn) if requested is None else list(requested)

    if requested is None:
        if tier == TIER_NO_AIMETA:
            names = [name for name in names if not is_internal_table_name(name)]
        else:
            # aimeta's `private` flag supersedes the suffix heuristic. Tables aimeta has never
            # heard of (created after the host compiled) are kept - absence is not privacy.
            names = [name for name in names if not (by_name.get(name) or {}).get("private")]

    counts: dict = {}
    partitioned: set = set()
    if include_live and names:
        try:
            counts = _loads(conn(_ROW_COUNTS, names).py())
        except Exception as error:
            logger.warning(f"Could not read row counts: {error}")
        try:
            partitioned = {_decode(name) for name in (conn('.Q.pt').py() or [])}
        except Exception as error:
            logger.debug(f"Could not read .Q.pt: {error}")

    tables = []
    for name in names:
        entry = dict(by_name.get(name) or {}) or table_entry_from_meta(conn, name)
        if include_live:
            live = _live_block(conn, name, counts, partitioned, preview_rows)
            _reconcile_row_data(entry, live)
            entry["live"] = live
        tables.append(entry)

    document = {
        "schemaVersion": (aimeta_doc or {}).get("schemaVersion", SUPPORTED_SCHEMA_VERSION),
        "process": (aimeta_doc or {}).get("process", {"name": ""}),
        "mcp": {
            "tier": tier,
            "fetchedAt": datetime.now(timezone.utc).isoformat(),
            "tableCount": len(tables),
        },
        "tables": tables,
        "references": (aimeta_doc or {}).get("references") or [],
        "functions": (aimeta_doc or {}).get("functions") or [],
    }
    return document


def build_table_document(table: str, preview_rows: int = DEFAULT_PREVIEW_ROWS) -> dict:
    """One table, without walking the database."""
    document = build_database_document(
        table_names=[table], include_live=True, preview_rows=preview_rows
    )
    entry = document["tables"][0] if document["tables"] else None
    if entry is None:
        return {"mcp": document["mcp"], "error": f"Table '{table}' not found"}

    # Carry only the references this table's columns actually point at.
    semantic_types = {
        column.get("semanticType") for column in entry.get("columns") or []
    } - {None}
    return {
        "schemaVersion": document["schemaVersion"],
        "mcp": document["mcp"],
        "table": entry,
        "references": [
            reference
            for reference in document["references"]
            if reference.get("semanticType") in semantic_types
        ],
    }


def build_functions_document() -> dict:
    """The callable surface. Empty below tier 3, with a hint saying why."""
    conn = get_kdb_connection()
    aimeta_doc = fetch_meta_document(conn=conn)
    tier = tier_from_document(aimeta_doc)

    from mcp_server.utils.aimeta import tier_guidance

    functions = (aimeta_doc or {}).get("functions") or []
    document = {
        "mcp": {
            "tier": tier,
            "fetchedAt": datetime.now(timezone.utc).isoformat(),
            "functionCount": len(functions),
        },
        "functions": functions,
    }

    if not functions or tier < 3:
        document["mcp"]["hint"] = tier_guidance(tier, aimeta_doc)
    return document
