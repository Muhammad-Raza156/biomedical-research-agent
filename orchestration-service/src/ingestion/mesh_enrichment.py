"""Cached MeSH enrichment: ask NLM only for what the staging cache doesn't already know."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from clients.mesh import MeshLookup
from ingestion.mesh_hierarchy import MeshHierarchy, ancestor_tree_numbers, build_hierarchy
from ingestion.staging import atomic_write_text, read_json


@dataclass
class EnrichmentStats:
    """Numbers reported in the ingest stream and INGESTION_REPORT.md."""

    descriptors: int
    without_tree_numbers: int
    ancestors: int
    calls_made: int


def _load_cache(path: Path) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    if not path.exists():
        return {}, {}
    data = read_json(path)
    return data["descriptors"], data["tree_index"]


def _save_cache(path: Path, descriptors: dict, tree_index: dict) -> None:
    atomic_write_text(path, json.dumps({"descriptors": descriptors, "tree_index": tree_index},
                                       sort_keys=True))


async def enrich_mesh(names_by_ui: dict[str, str], cache_path: Path, client: MeshLookup,
                      on_progress: Callable[[str], None] | None = None,
                      ) -> tuple[MeshHierarchy, EnrichmentStats]:
    """Resolve tree numbers + ancestors for every descriptor, using and updating the cache."""
    descriptors, tree_index = _load_cache(cache_path)   # ui → [tns]; tn → [ui, name]
    calls = 0
    progress = on_progress or (lambda message: None)

    missing_uis = sorted(ui for ui in names_by_ui if ui not in descriptors)
    if missing_uis:
        found = await client.tree_numbers(missing_uis)
        calls += 1  # one lookup round (the client batches internally)
        for ui in missing_uis:
            descriptors[ui] = found.get(ui, (None, []))[1]   # [] recorded so we never ask again
        _save_cache(cache_path, descriptors, tree_index)
        progress(f"resolved tree numbers for {len(missing_uis)} descriptors")

    needed = ancestor_tree_numbers(tn for ui in names_by_ui for tn in descriptors[ui])
    missing_tns = sorted(tn for tn in needed if tn not in tree_index)
    if missing_tns:
        found_tns = await client.descriptors_at(missing_tns)
        calls += 1  # one lookup round (the client batches internally)
        for tn, (ui, name) in found_tns.items():
            tree_index[tn] = [ui, name]
        _save_cache(cache_path, descriptors, tree_index)
        progress(f"resolved {len(found_tns)} ancestor tree numbers")

    corpus_terms = {ui: (name, descriptors[ui]) for ui, name in names_by_ui.items()}
    hierarchy = build_hierarchy(corpus_terms, {tn: (v[0], v[1]) for tn, v in tree_index.items()})
    stats = EnrichmentStats(
        descriptors=len(names_by_ui),
        without_tree_numbers=sum(1 for ui in names_by_ui if not descriptors[ui]),
        ancestors=sum(1 for t in hierarchy.terms if not t.in_corpus and not t.is_root),
        calls_made=calls,
    )
    return hierarchy, stats
