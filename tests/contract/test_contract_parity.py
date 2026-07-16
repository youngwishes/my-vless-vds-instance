from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from src.api.schemas import SnapshotDTO
from src.domain import canonical_snapshot_bytes, validate_snapshot


CONTRACT_DIR = Path(__file__).resolve().parents[2] / "docs" / "contracts" / "v1"
FIXTURE_DIR = CONTRACT_DIR / "fixtures"
PROVENANCE_PATH = CONTRACT_DIR / "PROVENANCE.sha256"
SOURCE_COMMIT = "507d152a3f3a404b9348ba3d57906f1dc225558c"
EXPECTED_HASHES = {
    "agent-v1.openapi.yaml": "cd06ef8d3db00d204a5e25bf8871c614899f0bf1fe86df8717ae3b19c75629dc",
    "fixtures/canonical-empty.canonical.json": "9d7b77084497930cf0439a2d2326e60a435c8b606e1785d06c56f09b25fb90e1",
    "fixtures/canonical-empty.json": "d714dce843453dd74a1a18abd7a82c2546ce44a391a9b0fa2f1442d04bf5e5be",
    "fixtures/canonical-two-accesses.canonical.json": "3c351495cf810ed9c06e06ad1436f1e1093413923cb7a2872df7a6243f1fa72f",
    "fixtures/canonical-two-accesses.json": "c528b3fa475682832ceb12b70b5862196c90b9a3d92d21dca860623acb33e732",
    "fixtures/invalid-revision-conflict.json": "ec7e25ab943abecc3d8a8ffc92b9e65eae2b0c7f5f07427224fed01c0b87cb59",
    "fixtures/invalid-unknown-major.json": "2f079731b82306d95acc78ab3b55035864e29582ee3c5f1260f16363f40cbae7",
    "fixtures/invalid-unsorted.json": "72e0dac59decd5343f158a99f33289e4e953f980a13b7857f03f1476d4931c0a",
    "fixtures/invalid-wrong-hash.json": "ce2c077c2da445ef23b5743bffc268845113b439a7f11b6914fe64e0b9a44b76",
    "snapshot-v1.schema.json": "9025514cde13d9de36c4a5dda8b4a953591bc566abc5fe8696c1a0143c3ab4c3",
}


def _load_json(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def test_copied_contract_sources_match_reviewed_c001_checksums() -> None:
    actual_hashes = {
        relative_path: hashlib.sha256((CONTRACT_DIR / relative_path).read_bytes()).hexdigest()
        for relative_path in EXPECTED_HASHES
    }

    assert actual_hashes == EXPECTED_HASHES
    provenance = PROVENANCE_PATH.read_text(encoding="utf-8")
    assert "source_repository=my-mtproto-backend" in provenance
    assert f"source_commit={SOURCE_COMMIT}" in provenance
    for relative_path, digest in EXPECTED_HASHES.items():
        assert f"{digest}  {relative_path}" in provenance


def test_canonical_fixtures_match_provider_canonicalizer_exactly() -> None:
    for name in ("canonical-empty.json", "canonical-two-accesses.json"):
        snapshot = SnapshotDTO.model_validate(_load_json(name))

        assert validate_snapshot(snapshot) is snapshot
        assert canonical_snapshot_bytes(snapshot) == (
            FIXTURE_DIR / name.replace(".json", ".canonical.json")
        ).read_bytes()
