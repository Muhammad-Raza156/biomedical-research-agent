"""staging volumn layout and crash-safe file writes."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

def atomic_write_bytes(path:Path, data: bytes) -> None:
    """write bytes so that readers see either the old file or the complete new one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def atomic_write_text(path: Path, text: str) -> None:
    """UTF-8 text version of atomic_write_bytes."""
    atomic_write_bytes(path, text.encode("utf-8"))

def read_json(path: Path) -> Any:
    """Read a JSON file written by atomic_write_text."""
    return json.loads(path.read_text(encoding="utf-8"))

class StagingArea:
    """where every stage keeps its raw and intermediate files for one corpus version."""

    def __init__(self, root: Path, corpus_version: str) -> None:
        self.root = root
        self.version_dir = root / corpus_version
        self.pmids_file = self.version_dir / "pmids.json"
        self.corpus_config_file = self.version_dir / "corpus_config.json"
        self.skipped_file = self.version_dir / "parsed" / "skipped.json"
        self.authors_file = self.version_dir / "authors.json"
        self.citations_file = self.version_dir / "citations.json"
        self.hierarchy_file = self.version_dir / "mesh_hierarchy.json"
        self.mesh_cache_file = root / "mesh_cache.json"

    def xml_batch(self, index:int) -> Path:
        """Raw efetch XML for batch `index`."""
        return self.version_dir / "xml" / f"batch_{index:04d}.xml"

    def elink_batch(self, index: int) -> Path:
        """Raw elink XML for batch `index`."""
        return self.version_dir / "elink" / f"batch_{index:04d}.xml"

    def parsed_batch(self, index: int) -> Path:
        """Load-ready parsed JSON for batch `index`."""
        return self.version_dir / "parsed" / f"batch_{index:04d}.json"