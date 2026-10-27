"""Pure MeSH tree logic: parents, ancestors, categories and BROADER edges."""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel

# Top-level MeSH trees (https://meshb.nlm.nih.gov/treeView). Categories come from here only.
CATEGORY_NAMES: dict[str, str] = {
    "A": "Anatomy",
    "B": "Organisms",
    "C": "Diseases",
    "D": "Chemicals and Drugs",
    "E": "Analytical, Diagnostic and Therapeutic Techniques, and Equipment",
    "F": "Psychiatry and Psychology",
    "G": "Phenomena and Processes",
    "H": "Disciplines and Occupations",
    "I": "Anthropology, Education, Sociology, and Social Phenomena",
    "J": "Technology, Industry, and Agriculture",
    "K": "Humanities",
    "L": "Information Science",
    "M": "Named Groups",
    "N": "Health Care",
    "V": "Publication Characteristics",
    "Z": "Geographicals",
}


def root_ui(letter: str) -> str:
    """Synthetic UI of the node representing a whole top-level tree."""
    return f"TREE_{letter}"


def parent_tree_number(tn: str) -> str | None:
    """'C04.588.274' → 'C04.588'; a top-level descriptor like 'C04' has no parent tree number."""
    return tn.rsplit(".", 1)[0] if "." in tn else None


def ancestor_tree_numbers(tns: Iterable[str]) -> set[str]:
    """Every proper prefix of every tree number (the descriptors above it)."""
    result: set[str] = set()
    for tn in tns:
        parent = parent_tree_number(tn)
        while parent:
            result.add(parent)
            parent = parent_tree_number(parent)
    return result


class MeshTermNode(BaseModel):
    """One (:MeshTerm) node to load."""

    ui: str
    name: str
    tree_numbers: list[str]
    category: str | None
    categories: list[str]
    in_corpus: bool
    is_root: bool = False


class MeshHierarchy(BaseModel):
    """Nodes plus (child_ui, parent_ui) BROADER edges."""

    terms: list[MeshTermNode]
    broader: list[tuple[str, str]]


def _letters(tns: list[str]) -> list[str]:
    return sorted({tn[0] for tn in tns})


def build_hierarchy(corpus_terms: dict[str, tuple[str, list[str]]],
                    tree_index: dict[str, tuple[str, str]]) -> MeshHierarchy:
    """Build nodes and BROADER edges from corpus descriptors and the tree-number index."""
    nodes: dict[str, MeshTermNode] = {}
    known_tns: dict[str, set[str]] = {}   # ui → tree numbers we know for it

    for ui, (name, tns) in corpus_terms.items():
        known_tns[ui] = set(tns)
        nodes[ui] = MeshTermNode(ui=ui, name=name, tree_numbers=sorted(tns), category=None,
                                 categories=[], in_corpus=True)

    # Every ancestor tree number on a corpus term's path becomes (part of) a node.
    path_tns = ancestor_tree_numbers(tn for _, tns in corpus_terms.values() for tn in tns)
    for tn in path_tns:
        if tn not in tree_index:
            continue  # unknown ancestor: edge below it will skip to nothing (logged by caller)
        ui, name = tree_index[tn]
        known_tns.setdefault(ui, set()).add(tn)
        if ui not in nodes:
            nodes[ui] = MeshTermNode(ui=ui, name=name, tree_numbers=[], category=None,
                                     categories=[], in_corpus=False)

    edges: set[tuple[str, str]] = set()
    used_roots: set[str] = set()
    for ui, tns in known_tns.items():
        for tn in tns:
            parent_tn = parent_tree_number(tn)
            if parent_tn is None:
                used_roots.add(tn[0])
                edges.add((ui, root_ui(tn[0])))
            elif parent_tn in tree_index and tree_index[parent_tn][0] != ui:
                edges.add((ui, tree_index[parent_tn][0]))

    for ui, node in nodes.items():
        tns = sorted(known_tns.get(ui, set()))
        node.tree_numbers = tns
        node.categories = _letters(tns)
        node.category = node.categories[0] if node.categories else None

    for letter in sorted(used_roots):
        nodes[root_ui(letter)] = MeshTermNode(
            ui=root_ui(letter), name=CATEGORY_NAMES.get(letter, letter), tree_numbers=[letter],
            category=letter, categories=[letter], in_corpus=False, is_root=True)

    return MeshHierarchy(terms=sorted(nodes.values(), key=lambda n: n.ui), broader=sorted(edges))
