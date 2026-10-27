from ingestion.mesh_hierarchy import (
    CATEGORY_NAMES,
    ancestor_tree_numbers,
    build_hierarchy,
    parent_tree_number,
    root_ui,
)


def test_parent_and_ancestors():
    assert parent_tree_number("D02.078.370.141.450") == "D02.078.370.141"
    assert parent_tree_number("C04") is None
    assert ancestor_tree_numbers(["D02.078.370"]) == {"D02", "D02.078"}


def test_categories_come_from_letters_not_terms():
    assert CATEGORY_NAMES["C"] == "Diseases"
    assert CATEGORY_NAMES["D"] == "Chemicals and Drugs"
    assert root_ui("E") == "TREE_E"


def make_index():
    return {
        "D02": ("D009930", "Organic Chemicals"),
        "D02.078": ("D000578", "Amidines"),
        "D02.078.370": ("D006146", "Guanidines"),
        "D02.078.370.141": ("D001645", "Biguanides"),
        "C04": ("D009369", "Neoplasms"),
    }


def test_build_hierarchy_links_up_to_root():
    corpus = {"D008687": ("Metformin", ["D02.078.370.141.450"]), "D009369": ("Neoplasms", ["C04"])}
    h = build_hierarchy(corpus, make_index())
    edges = set(h.broader)
    assert ("D008687", "D001645") in edges            # Metformin → Biguanides
    assert ("D009930", "TREE_D") in edges             # Organic Chemicals → top-level D
    assert ("D009369", "TREE_C") in edges             # Neoplasms → top-level C
    by_ui = {t.ui: t for t in h.terms}
    assert by_ui["D008687"].in_corpus and by_ui["D008687"].category == "D"
    assert not by_ui["D001645"].in_corpus             # ancestor-only node
    assert by_ui["TREE_C"].is_root and by_ui["TREE_C"].name == "Diseases"


def test_term_without_tree_numbers_is_kept_without_category():
    h = build_hierarchy({"D005260": ("Female", [])}, {})
    female = next(t for t in h.terms if t.ui == "D005260")
    assert female.tree_numbers == [] and female.category is None
    assert h.broader == []


def test_multiple_positions_give_multiple_parents_without_duplicates():
    corpus = {"D1": ("Drug", ["D02.078.370.141.450", "C04.1"]), "D2": ("Other", ["D02.078.370.141.9"])}
    h = build_hierarchy(corpus, make_index())
    assert ("D1", "D001645") in h.broader and ("D1", "D009369") in h.broader
    assert len(h.broader) == len(set(h.broader))
    assert next(t for t in h.terms if t.ui == "D1").categories == ["C", "D"]
