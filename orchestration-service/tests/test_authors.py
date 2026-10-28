from ingestion.authors import name_key, normalise_name, resolve_authors
from ingestion.models import AuthorRecord, PaperRecord


def author(pos, last, fore, orcid=None, aff=None):
    return AuthorRecord(
        position=pos,
        last_name=last,
        fore_name=fore,
        initials=fore[:1] if fore else None,
        orcid=orcid,
        affiliations=aff or [],
    )


def paper(pmid, *authors):
    return PaperRecord(pmid=pmid, title=f"paper {pmid}", authors=list(authors))


def ids_by_occurrence(resolution):
    return {(a.pmid, a.position): a.author_id for a in resolution.authorships}


def test_normalisation_and_key():
    assert normalise_name("Menéndez") == "menendez"
    assert normalise_name("Dell'Aniello") == "dell aniello"
    assert name_key(author(1, "Menéndez", "Javier A")) == "menendez|j"
    assert (
        name_key(AuthorRecord(position=1, collective_name="DPP Research Group")) is None
    )


def test_orcid_merges_even_across_name_variants():
    r = resolve_authors(
        [
            paper("1", author(1, "Smith", "Anna", orcid="0000-0000-0000-0001")),
            paper("2", author(1, "Smith-Jones", "Anna", orcid="0000-0000-0000-0001")),
        ]
    )
    ids = ids_by_occurrence(r)
    assert ids[("1", 1)] == ids[("2", 1)] == "orcid:0000-0000-0000-0001"
    assert {a.match_rule for a in r.authorships} == {"orcid"}


def test_shared_coauthor_merges_same_key():
    r = resolve_authors(
        [
            paper("1", author(1, "Lee", "Kim"), author(2, "Park", "Ji")),
            paper("2", author(1, "Lee", "K"), author(2, "Park", "Ji")),
        ]
    )
    ids = ids_by_occurrence(r)
    assert ids[("1", 1)] == ids[("2", 1)]
    lee = next(a for a in r.authors if a.author_id == ids[("1", 1)])
    assert any("shared_coauthor" in e for e in lee.merge_evidence)


def test_affiliation_overlap_merges():
    aff1 = ["Department of Oncology, Karolinska Institutet, Stockholm, Sweden"]
    aff2 = ["Karolinska Institutet, Solna, Stockholm"]
    r = resolve_authors(
        [
            paper("1", author(1, "Berg", "Eva", aff=aff1)),
            paper("2", author(1, "Berg", "E", aff=aff2)),
        ]
    )
    ids = ids_by_occurrence(r)
    assert ids[("1", 1)] == ids[("2", 1)]
    assert {a.match_rule for a in r.authorships} == {"affiliation_overlap"}


def test_no_evidence_means_separate_people():
    r = resolve_authors(
        [paper("1", author(1, "Wang", "Yi")), paper("2", author(1, "Wang", "Yi"))]
    )
    ids = ids_by_occurrence(r)
    assert ids[("1", 1)] != ids[("2", 1)]
    assert {a.match_rule for a in r.authorships} == {"singleton"}


def test_same_key_on_same_paper_never_merges():
    r = resolve_authors(
        [
            paper(
                "1",
                author(1, "Wang", "Yi"),
                author(2, "Wang", "Yu"),
                author(3, "Chen", "Li"),
            ),
            paper("2", author(1, "Wang", "Y"), author(2, "Chen", "Li")),
        ]
    )
    ids = ids_by_occurrence(r)
    assert ids[("1", 1)] != ids[("1", 2)]


def test_conflicting_full_first_names_never_merge():
    r = resolve_authors(
        [
            paper("1", author(1, "Smith", "John"), author(2, "Doe", "Ann")),
            paper("2", author(1, "Smith", "James"), author(2, "Doe", "Ann")),
        ]
    )
    ids = ids_by_occurrence(r)
    assert ids[("1", 1)] != ids[("2", 1)]


def test_transitive_chain_cannot_join_two_orcids():
    r = resolve_authors(
        [
            paper(
                "1",
                author(1, "Kim", "Su", orcid="0000-0000-0000-0001"),
                author(2, "Cho", "Mi"),
            ),
            paper(
                "2",
                author(1, "Kim", "S"),
                author(2, "Cho", "Mi"),
                author(3, "Han", "Bo"),
            ),
            paper(
                "3",
                author(1, "Kim", "Su", orcid="0000-0000-0000-0002"),
                author(2, "Han", "Bo"),
            ),
        ]
    )
    ids = ids_by_occurrence(r)
    assert ids[("1", 1)] != ids[("3", 1)]


def test_collective_authors_and_counts():
    r = resolve_authors(
        [
            paper(
                "1",
                AuthorRecord(position=1, collective_name="DPP Research Group"),
                author(2, "Doe", "Ann"),
                author(3, "Roe", "Bo"),
            ),
            paper(
                "2",
                AuthorRecord(position=1, collective_name="DPP Research Group"),
                author(2, "Doe", "Ann"),
                author(3, "Roe", "Bo"),
            ),
        ]
    )
    ids = ids_by_occurrence(r)
    assert ids[("1", 1)] == ids[("2", 1)] == "collective:dpp-research-group"
    assert r.multi_paper_count() == 2  # Doe and Roe; the collective is not counted


def test_ids_are_deterministic():
    papers = [
        paper("20", author(1, "Lee", "Kim"), author(2, "Park", "Ji")),
        paper("10", author(1, "Lee", "K"), author(2, "Park", "Ji")),
    ]
    assert resolve_authors(papers) == resolve_authors(list(reversed(papers)))
    assert ids_by_occurrence(resolve_authors(papers))[("10", 1)] == "name:lee|k:10/1"
