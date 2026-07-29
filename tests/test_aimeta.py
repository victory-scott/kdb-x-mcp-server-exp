"""Tier detection, the qIPC scrub, and the schema-version guard."""

import pytest

from mcp_server.utils.aimeta import (
    TIER_ANNOTATED,
    TIER_NO_AIMETA,
    TIER_UNANNOTATED,
    check_schema_version,
    scrub_ipc_artifacts,
    tier_from_document,
    tier_guidance,
)


class TestTierDetection:
    def test_no_document_is_tier_1(self):
        assert tier_from_document(None) == TIER_NO_AIMETA

    def test_annotated_host_has_no_compile_status(self, tier3_reference):
        assert "compileStatus" not in tier3_reference["process"]
        assert tier_from_document(tier3_reference) == TIER_ANNOTATED

    def test_unannotated_host_reports_empty(self, tier2):
        assert tier2["process"]["compileStatus"] == "empty"
        assert tier_from_document(tier2) == TIER_UNANNOTATED

    def test_failed_compile_is_also_tier_2(self):
        doc = {"process": {"name": "host", "compileStatus": "failed"}}
        assert tier_from_document(doc) == TIER_UNANNOTATED

    def test_tier_is_not_read_from_the_wire(self, tier3_reference):
        """`tier` is injected by the kx-meta CLI, never present in a /meta body."""
        assert "tier" not in tier3_reference
        assert tier_from_document({**tier3_reference, "tier": 1}) == TIER_ANNOTATED


class TestScrubIpcArtifacts:
    def test_ipc_form_really_is_padded(self, tier3_reference, tier3_ipc):
        """Guards the premise: without this the scrub would be testing nothing."""
        reference = {t["name"]: t for t in tier3_reference["tables"]}
        padded = {
            name: set(t) - set(reference[name])
            for name, t in {t["name"]: t for t in tier3_ipc["tables"]}.items()
        }
        assert padded["trade"] == {"reference", "labels"}
        assert padded["quote"] == {"reference", "labels", "sampleData"}

    def test_scrub_recovers_the_canonical_form(self, tier3_reference, tier3_ipc):
        scrubbed = scrub_ipc_artifacts(tier3_ipc)
        assert scrubbed["tables"] == tier3_reference["tables"]

    def test_scrub_keeps_real_values(self, tier3_ipc):
        scrubbed = scrub_ipc_artifacts(tier3_ipc)
        instrument = next(t for t in scrubbed["tables"] if t["name"] == "instrument")
        assert instrument["reference"] == "instrument"
        assert instrument["labels"] == ["name"]

        trade = next(t for t in scrubbed["tables"] if t["name"] == "trade")
        assert len(trade["sampleData"]) == 2
        sym = next(c for c in trade["columns"] if c["name"] == "sym")
        assert sym["foreignRef"] == "instrument.sym"
        assert sym["attributes"] == ["g"]

    def test_scrub_does_not_drop_required_empty_fields(self, tier2):
        """A tier-2 document legitimately carries desc:"" - that is data, not padding."""
        scrubbed = scrub_ipc_artifacts(tier2)
        assert all("desc" in table for table in scrubbed["tables"])
        assert all(table["private"] is False for table in scrubbed["tables"])

    def test_scrub_drops_empty_column_foreign_refs(self, tier2):
        scrubbed = scrub_ipc_artifacts(tier2)
        columns = [c for t in scrubbed["tables"] for c in t["columns"]]
        assert columns, "fixture should have columns"
        assert all("foreignRef" not in column for column in columns)

    def test_scrub_leaves_input_untouched(self, tier3_ipc):
        before = len(tier3_ipc["tables"][0])
        scrub_ipc_artifacts(tier3_ipc)
        assert len(tier3_ipc["tables"][0]) == before

    def test_scrub_tolerates_junk(self):
        assert scrub_ipc_artifacts({}) == {}
        assert scrub_ipc_artifacts({"tables": []}) == {"tables": []}


class TestSchemaVersionGuard:
    def test_current_version_passes(self, tier3_reference):
        assert check_schema_version(tier3_reference) is tier3_reference

    def test_newer_version_refused(self, tier3_reference):
        assert check_schema_version({**tier3_reference, "schemaVersion": 99}) is None

    def test_older_version_accepted(self, tier3_reference):
        doc = {**tier3_reference, "schemaVersion": 1}
        assert check_schema_version(doc) is doc


class TestTierGuidance:
    @pytest.mark.parametrize("tier", [1, 2, 3])
    def test_every_tier_produces_a_message(self, tier):
        assert tier_guidance(tier, None).strip()

    def test_tier_3_counts_what_was_found(self, tier3_reference):
        message = tier_guidance(3, tier3_reference)
        assert "3 tables" in message and "2 functions" in message and "1 references" in message

    def test_tier_2_distinguishes_failure_from_absence(self):
        empty = tier_guidance(2, {"process": {"compileStatus": "empty"}})
        failed = tier_guidance(2, {"process": {"compileStatus": "failed"}})
        assert "no annotations" in empty
        assert "compile failed" in failed

    def test_tier_1_tells_the_user_how_to_enable_it(self):
        assert "kx.aimeta" in tier_guidance(1, None)
