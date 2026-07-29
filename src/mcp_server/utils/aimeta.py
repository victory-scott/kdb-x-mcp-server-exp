"""Fetch, cache and tier-detect the aimeta metadata document.

aimeta (https://github.com/KxSystems/aimeta) is a kdb-x module that compiles qdoc-style
annotations in .q source into a canonical metadata document, served over HTTP (`/meta`) and
qIPC (`.aimeta.*`). It is additive: a process with no aimeta still answers native
introspection, a process with aimeta loaded but unannotated source answers the canonical
shape with empty prose, and an annotated process answers the full model.

Three tiers, distinguished by `process.compileStatus`:

    1  no aimeta            /meta 404s; `.aimeta.data` is undefined
    2  loaded, unannotated  compileStatus == "empty" (or "failed")
    3  loaded + annotated   compileStatus key is absent

Transport: qIPC only, over the connection this server already holds. aimeta also serves the
same document over HTTP on the same port, but once the document is normalized the two are
byte-identical, so a second transport would buy nothing but its own failure modes.

Normalization matters. The qIPC form re-serializes a hydrated q table, and aimeta's `(uj/)`
uniform-table hydration pads rows that lack optional columns, so `.j.j .aimeta.data[]` emits
phantom `reference:""`, `labels:[""]` and `sampleData:null`. Tier-2 documents separately carry
`foreignRef:""` on every column. `scrub_ipc_artifacts` drops both.
"""

import json
import logging
import threading
import time
from typing import Any, Optional

from mcp_server.server import app_settings

logger = logging.getLogger(__name__)

db_config = app_settings.db

# Highest meta.json schemaVersion this server knows how to read. aimeta's consumer contract
# says a newer document must fail loudly rather than be best-effort parsed.
SUPPORTED_SCHEMA_VERSION = 2

TIER_NO_AIMETA = 1
TIER_UNANNOTATED = 2
TIER_ANNOTATED = 3

# Probe for `.aimeta.data`, the no-arg getter that init[] binds into the global namespace.
# Protected eval matching the idiom already used for the .s and .ai namespace checks.
AIMETA_PRESENCE_PROBE = '@[{100h~type get x};`.aimeta.data;{0b}]'

# Optional keys, per aimeta's meta.schema.json. Anything not listed here is required by the
# schema and is left alone even when empty - a tier-2 document legitimately carries desc:"".
_TABLE_OPTIONAL = frozenset({"reference", "labels", "sampleData", "tags"})
_COLUMN_OPTIONAL = frozenset(
    {"desc", "semanticType", "foreignRef", "cardinality", "attributes", "label"}
)

_lock = threading.Lock()
_cache: dict[str, Any] = {"document": None, "fetched_at": 0.0}


def _is_empty(value: Any) -> bool:
    """True for the empty forms q's JSON encoder produces for an absent optional field."""
    if value is None or value == "" or value == [] or value == {}:
        return True
    # ("") - a one-element list holding only empties, which is what (uj/) padding yields
    # for a `labels` column on a table that declared none.
    if isinstance(value, list) and all(v in ("", None) for v in value):
        return True
    return False


def scrub_ipc_artifacts(doc: dict) -> dict:
    """Drop optional keys that carry no information, so both transports agree.

    Two separate sources of empty-but-present optional fields:

    - qIPC padding. aimeta's `(uj/)` uniform-table hydration gives every table row the union
      of all columns, so `.j.j .aimeta.data[]` emits `reference:""`, `labels:[""]` and
      `sampleData:null` on tables that declared none. `GET /meta` omits those keys.
    - Tier-2 documents. aimeta's own in-host synthesis writes `foreignRef:""` on every
      column, over HTTP as well as IPC.

    Applied to both transports, so a tier-2 document reads the same either way and the model
    never sees a column claiming an empty foreign reference. Idempotent; input untouched.
    """
    if not isinstance(doc, dict):
        return doc

    scrubbed = dict(doc)
    tables = []
    for table in scrubbed.get("tables") or []:
        entry = {
            key: value
            for key, value in table.items()
            if not (key in _TABLE_OPTIONAL and _is_empty(value))
        }
        entry["columns"] = [
            {
                key: value
                for key, value in column.items()
                if not (key in _COLUMN_OPTIONAL and _is_empty(value))
            }
            for column in table.get("columns") or []
        ]
        tables.append(entry)

    if "tables" in scrubbed:
        scrubbed["tables"] = tables
    return scrubbed


def tier_from_document(doc: Optional[dict]) -> int:
    """Derive the tier from `process.compileStatus`.

    Note `tier` is NOT a field on the wire - only the `kx-meta discover` CLI injects one.
    A "failed" status means compile errored and aimeta synthesised a names-only view, which
    is tier-2-equivalent in content.
    """
    if not doc:
        return TIER_NO_AIMETA
    status = (doc.get("process") or {}).get("compileStatus")
    return TIER_ANNOTATED if status is None else TIER_UNANNOTATED


def check_schema_version(doc: Optional[dict]) -> Optional[dict]:
    """Return the document, or None if its schemaVersion is newer than we support."""
    if not doc:
        return None
    found = doc.get("schemaVersion")
    if isinstance(found, int) and found > SUPPORTED_SCHEMA_VERSION:
        logger.error(
            f"aimeta schemaVersion {found} is newer than the supported version "
            f"{SUPPORTED_SCHEMA_VERSION}. Refusing to parse it - falling back to native "
            f"introspection. Upgrade the KDB-X MCP server to read this document."
        )
        return None
    return doc


def _fetch_ipc(conn) -> Optional[dict]:
    """Read the document via `.j.j .aimeta.data[]` over the existing qIPC connection."""
    try:
        if not conn(AIMETA_PRESENCE_PROBE).py():
            return None
        raw = conn(".j.j .aimeta.data[]").py()
    except Exception as error:
        logger.debug(f"aimeta qIPC fetch failed: {error}")
        return None

    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        document = json.loads(raw)
    except Exception as error:
        logger.warning(f"aimeta qIPC fetch returned malformed JSON: {error}")
        return None

    return scrub_ipc_artifacts(document)


def fetch_meta_document(conn=None, force: bool = False) -> Optional[dict]:
    """Return the aimeta document, or None when the process has no aimeta.

    TTL-cached. A negative result is cached too, so a process without aimeta does not pay a
    probe on every read.
    """
    from mcp_server.utils.kdbx import get_kdb_connection

    with _lock:
        age = time.monotonic() - _cache["fetched_at"]
        if not force and _cache["fetched_at"] and age < db_config.aimeta_cache_ttl:
            return _cache["document"]

        if conn is None:
            try:
                conn = get_kdb_connection()
            except Exception as error:
                logger.debug(f"aimeta fetch could not get a connection: {error}")
                return _cache["document"]

        document = check_schema_version(_fetch_ipc(conn))
        _cache.update({"document": document, "fetched_at": time.monotonic()})
        return document


def invalidate_meta_cache() -> None:
    with _lock:
        _cache.update({"document": None, "fetched_at": 0.0})
    logger.info("aimeta metadata cache cleared")


def reload_remote_metadata(conn) -> bool:
    """Ask the host to re-read its meta.json, then drop our cache.

    Returns True if `.aimeta.reload[]` ran. This is what makes annotate -> recompile ->
    reload a live loop instead of an MCP server restart.
    """
    try:
        if conn(AIMETA_PRESENCE_PROBE).py():
            conn(".aimeta.reload[]")
            reloaded = True
        else:
            reloaded = False
    except Exception as error:
        logger.warning(f"aimeta reload failed: {error}")
        reloaded = False

    invalidate_meta_cache()
    return reloaded


def detect_tier(conn) -> tuple[int, Optional[dict]]:
    """Startup probe. Returns (tier, document)."""
    document = fetch_meta_document(conn=conn, force=True)
    return tier_from_document(document), document


def tier_guidance(tier: int, document: Optional[dict] = None) -> str:
    """The startup log line for a tier, phrased so the user knows what to do next."""
    if tier == TIER_ANNOTATED:
        doc = document or {}
        return (
            f"KDB-X aimeta check: SUCCESS - annotated metadata available "
            f"({len(doc.get('tables') or [])} tables, "
            f"{len(doc.get('functions') or [])} functions, "
            f"{len(doc.get('references') or [])} references)"
        )
    if tier == TIER_UNANNOTATED:
        status = ((document or {}).get("process") or {}).get("compileStatus")
        detail = (
            "the annotation compile failed"
            if status == "failed"
            else "the q source carries no annotations"
        )
        return (
            f"KDB-X aimeta check: PARTIAL - aimeta is loaded but {detail}. Table and column "
            "names are available; descriptions, semantic types, foreign keys and function "
            "signatures are not. Annotate your q source and recompile to enable them - see "
            "https://github.com/KxSystems/aimeta"
        )
    return (
        "KDB-X aimeta check: NOT LOADED - falling back to native introspection "
        "(table and column names and types only). To enable richer metadata, run "
        "aimeta:use`kx.aimeta; aimeta.init[] in your KDB-X session and restart the MCP server"
    )
