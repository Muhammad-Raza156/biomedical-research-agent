"""Cached MeSH enrichment: ask NLM only for what the staging cache doesn't already know."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from clients.mesh import MeshLookup
from ingestion.mesh_hierarchy import (
    MeshHierarchy,
    ancestor_tree_numbers,
    build_hierarchy,
)
from ingestion.staging import atomic_write_text, read_json


@dataclass
class EnrichmentStats:
    """Numbers reported in the ingest stream and INGESTION_REPORT.md."""

    descriptors: int
    without_tree_numbers: int
    ancestors: int
    calls_made: int


# One SPARQL request per chunk; the cache is saved and progress reported after each one,
# so a retry resumes where it stopped and the activity heartbeats every few seconds.
CHUNK = 50


def _load_cache(path: Path) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    if not path.exists():
        return {}, {}
    data = read_json(path)
    return data["descriptors"], data["tree_index"]


def _save_cache(path: Path, descriptors: dict, tree_index: dict) -> None:
    atomic_write_text(
        path,
        json.dumps(
            {"descriptors": descriptors, "tree_index": tree_index}, sort_keys=True
        ),
    )


async def enrich_mesh(
    names_by_ui: dict[str, str],
    cache_path: Path,
    client: MeshLookup,
    on_progress: Callable[[str], None] | None = None,
) -> tuple[MeshHierarchy, EnrichmentStats]:
    """Resolve tree numbers + ancestors for every descriptor, using and updating the cache."""
    descriptors, tree_index = _load_cache(cache_path)  # ui → [tns]; tn → [ui, name]
    calls = 0
    progress = on_progress or (lambda message: None)

    missing_uis = sorted(ui for ui in names_by_ui if ui not in descriptors)
    for start in range(0, len(missing_uis), CHUNK):
        chunk = missing_uis[start : start + CHUNK]
        found = await client.tree_numbers(chunk)
        calls += 1
        for ui in chunk:
            descriptors[ui] = found.get(ui, (None, []))[
                1
            ]  # [] recorded so we never ask again
        _save_cache(cache_path, descriptors, tree_index)
        progress(f"tree numbers: {start + len(chunk)}/{len(missing_uis)} descriptors")

    needed = ancestor_tree_numbers(tn for ui in names_by_ui for tn in descriptors[ui])
    missing_tns = sorted(tn for tn in needed if tn not in tree_index)
    for start in range(0, len(missing_tns), CHUNK):
        chunk = missing_tns[start : start + CHUNK]
        found_tns = await client.descriptors_at(chunk)
        calls += 1
        for tn, (ui, name) in found_tns.items():
            tree_index[tn] = [ui, name]
        _save_cache(cache_path, descriptors, tree_index)
        progress(f"ancestors: {start + len(chunk)}/{len(missing_tns)} tree numbers")

    corpus_terms = {ui: (name, descriptors[ui]) for ui, name in names_by_ui.items()}
    hierarchy = build_hierarchy(
        corpus_terms, {tn: (v[0], v[1]) for tn, v in tree_index.items()}
    )
    stats = EnrichmentStats(
        descriptors=len(names_by_ui),
        without_tree_numbers=sum(1 for ui in names_by_ui if not descriptors[ui]),
        ancestors=sum(1 for t in hierarchy.terms if not t.in_corpus and not t.is_root),
        calls_made=calls,
    )
    return hierarchy, stats
