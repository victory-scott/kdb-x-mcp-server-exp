import sys

# AppSettings is constructed at mcp_server import time with cli_parse_args=True, so it would
# otherwise try to parse pytest's own argv and exit. Must run before any mcp_server import.
sys.argv = sys.argv[:1]

import json  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    with open(FIXTURES / name) as handle:
        return json.load(handle)


@pytest.fixture
def tier3_reference() -> dict:
    """The canonical document shape: annotated host, captured from GET /meta.

    We do not read /meta at runtime - qIPC is the only transport - but aimeta's HTTP body is
    the reference form, so it is what the scrub is asserted against.
    Source: aimeta demos/authoring/host-annotated.q.
    """
    return load_fixture("meta_tier3_reference.json")


@pytest.fixture
def tier3_ipc() -> dict:
    """Same host over .j.j .aimeta.data[]. Carries the uniform-table padding artifacts."""
    return load_fixture("meta_tier3_ipc.json")


@pytest.fixture
def tier2() -> dict:
    """aimeta loaded, source unannotated. Captured from aimeta demos/authoring/host.q.

    Identical over both transports - the padding only appears when some table declares an
    optional column, which no tier-2 table does.
    """
    return load_fixture("meta_tier2.json")


class FakeResult:
    """Stands in for a pykx result object, which callers unwrap with .py()."""

    def __init__(self, value):
        self._value = value

    def py(self):
        return self._value


class FakeNamespace:
    def __init__(self, values: dict):
        for key, value in values.items():
            setattr(self, key, FakeResult(value))


class FakeConn:
    """Minimal stand-in for kx.SyncQConnection.

    `handlers` maps a q expression to either a literal result or a callable taking the
    positional args the production code passes.
    """

    def __init__(self, handlers: dict, tables: list[str] | None = None, pt: list[str] | None = None):
        self.handlers = handlers
        self._tables = tables or []
        self.Q = FakeNamespace({"pt": pt or []})
        self.calls: list[tuple] = []

    def __call__(self, expr, *args):
        self.calls.append((expr, args))
        if expr not in self.handlers:
            raise KeyError(f"FakeConn has no handler for: {expr}")
        handler = self.handlers[expr]
        return FakeResult(handler(*args) if callable(handler) else handler)

    def tables(self, _=None):
        return FakeResult(self._tables)


@pytest.fixture
def fake_conn_factory():
    return FakeConn
