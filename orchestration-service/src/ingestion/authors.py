"""Author disambiguation: ORCID first, then name key + shared co-author or affiliation evidence."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import combinations

from pydantic import BaseModel, Field

from ingestion.models import AuthorRecord, PaperRecord

_AFF_STOPWORDS = frozenset(
    {
        "department",
        "departments",
        "university",
        "universit",
        "hospital",
        "school",
        "medicine",
        "medical",
        "center",
        "centre",
        "institute",
        "institut",
        "research",
        "sciences",
        "science",
        "faculty",
        "division",
        "clinical",
        "health",
        "college",
        "unit",
        "laboratory",
        "general",
        "national",
        "public",
        "street",
        "road",
        "email",
        "electronic",
        "address",
        "china",
        "japan",
        "korea",
        "italy",
        "france",
        "germany",
        "canada",
        "united",
        "states",
        "kingdom",
        "usa",
    }
)
_WORD = re.compile(r"[a-z]{4,}")


def normalise_name(text: str | None) -> str:
    """Lowercase ASCII words: 'Menéndez' → 'menendez', "Dell'Aniello" → 'dell aniello'."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text)
    ascii_text = "".join(
        ch for ch in decomposed if not unicodedata.combining(ch)
    ).lower()
    return " ".join(re.sub(r"[^a-z]+", " ", ascii_text).split())


def name_key(author: AuthorRecord) -> str | None:
    """'last|first-initial', or None for collective authors / authors without a last name."""
    if author.is_collective or not author.last_name:
        return None
    last = normalise_name(author.last_name)
    first = normalise_name(author.fore_name or author.initials)
    return f"{last}|{first[:1]}" if last else None


def _given_name(author: AuthorRecord) -> str | None:
    """Full first given name ('javier'), or None when only initials are known ('a e')."""
    tokens = normalise_name(author.fore_name).split()
    return tokens[0] if tokens and len(tokens[0]) > 1 else None


def _affiliation_tokens(affiliations: list[str]) -> set[str]:
    words = _WORD.findall(normalise_name(" ".join(affiliations)))
    return {w for w in words if w not in _AFF_STOPWORDS}


class AuthorEntity(BaseModel):
    """One disambiguated person (or collective) → one (:Author) node."""

    author_id: str
    display_name: str
    orcid: str | None = None
    name_key: str
    collective: bool = False
    name_variants: list[str] = Field(default_factory=list)
    merge_evidence: list[str] = Field(default_factory=list)
    pmids: list[str] = Field(default_factory=list)


class Authorship(BaseModel):
    """One author slot on one paper → one [:AUTHORED] relationship."""

    pmid: str
    position: int
    author_id: str
    name_as_written: str
    affiliations: list[str] = Field(default_factory=list)
    match_rule: str


class AuthorResolution(BaseModel):
    """Result of dismabiguation over the whole corpus."""

    authors: list[AuthorEntity]
    authorships: list[Authorship]

    def by_paper(self) -> dict[str, list[Authorship]]:
        """Authorships grouped by PMID (for per batch loading)"""
        grouped: dict[str, list[Authorship]] = defaultdict(list)
        for authorship in self.authorships:
            grouped[authorship.pmid].append(authorship)
        return dict(grouped)

    def multi_paper_count(self) -> int:
        """Disambiguated (non-collective) authors with two or more papers — an acceptance criterion."""
        return sum(1 for a in self.authors if not a.collective and len(a.pmids) >= 2)


@dataclass
class _Occurrence:
    occ_id: str  # "pmid/position"
    pmid: str
    author: AuthorRecord
    key: str
    given: str | None
    coauthors: set[str]
    aff_tokens: set[str]


@dataclass
class _Clusters:
    """Union-find over occurrences, keeping each cluster's ORCIDs and given names."""

    parent: dict[str, str] = field(default_factory=dict)
    orcids: dict[str, set[str]] = field(default_factory=dict)
    givens: dict[str, set[str]] = field(default_factory=dict)

    def add(self, occ: _Occurrence, orcid: str | None) -> None:
        self.parent[occ.occ_id] = occ.occ_id
        self.orcids[occ.occ_id] = {orcid} if orcid else set()
        self.givens[occ.occ_id] = {occ.given} if occ.given else set()

    def find(self, x: str) -> str:
        while self.parent[x] != x:
            self.parent[x] = self.parent[
                self.parent[x]
            ]  # path halving keeps trees flat
            x = self.parent[x]
        return x

    def try_union(self, a: str, b: str) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        if self.orcids[ra] and self.orcids[rb] and self.orcids[ra] != self.orcids[rb]:
            return False
        if (
            self.givens[ra]
            and self.givens[rb]
            and not (self.givens[ra] & self.givens[rb])
        ):
            return False
        keep, drop = sorted((ra, rb))
        self.parent[drop] = keep
        self.orcids[keep] |= self.orcids.pop(drop)
        self.givens[keep] |= self.givens.pop(drop)
        return True


def _occ_sort_key(occ_id: str) -> tuple[int, int]:
    pmid, pos = occ_id.split("/")
    return int(pmid), int(pos)


def resolve_authors(
    papers: list[PaperRecord], use_orcid: bool = True
) -> AuthorResolution:
    """Cluster every author occurrence into people; record the rule behind every merge."""
    papers = sorted(papers, key=lambda p: int(p.pmid))
    occurrences: list[_Occurrence] = []
    collectives: list[tuple[PaperRecord, AuthorRecord]] = []
    for paper in papers:
        keys_on_paper = {name_key(a) for a in paper.authors} - {None}
        for a in paper.authors:
            key = name_key(a)
            if key is None:
                if a.is_collective or a.name_as_written:
                    collectives.append((paper, a))
                continue
            occurrences.append(
                _Occurrence(
                    occ_id=f"{paper.pmid}/{a.position}",
                    pmid=paper.pmid,
                    author=a,
                    key=key,
                    given=_given_name(a),
                    coauthors=keys_on_paper - {key},
                    aff_tokens=_affiliation_tokens(a.affiliations),
                )
            )

    clusters = _Clusters()
    for occ in occurrences:
        clusters.add(occ, occ.author.orcid if use_orcid else None)
    rule_of: dict[str, str] = {}
    evidence: list[
        tuple[str, str]
    ] = []  # (occ_id, text) — attached to the final cluster later

    def merge(a: _Occurrence, b: _Occurrence, rule: str, detail: str) -> None:
        if clusters.try_union(a.occ_id, b.occ_id):
            rule_of.setdefault(a.occ_id, rule)
            rule_of.setdefault(b.occ_id, rule)
            evidence.append((a.occ_id, f"{a.occ_id} ~ {b.occ_id}: {rule} ({detail})"))

    # Rule 1: same ORCID → same person.
    if use_orcid:
        by_orcid: dict[str, list[_Occurrence]] = defaultdict(list)
        for occ in occurrences:
            if occ.author.orcid:
                by_orcid[occ.author.orcid].append(occ)
        for orcid, group in sorted(by_orcid.items()):
            for a, b in zip(group, group[1:]):
                merge(a, b, "orcid", orcid)

    # Rule 2: same name key + (shared co-author | affiliation overlap), never on the same paper.
    by_key: dict[str, list[_Occurrence]] = defaultdict(list)
    for occ in occurrences:
        by_key[occ.key].append(occ)
    for key in sorted(by_key):
        for a, b in combinations(by_key[key], 2):
            if a.pmid == b.pmid:
                continue
            shared = sorted(a.coauthors & b.coauthors)
            if shared:
                merge(a, b, "shared_coauthor", shared[0])
                continue
            common = sorted(a.aff_tokens & b.aff_tokens)
            if len(common) >= 2:
                merge(a, b, "affiliation_overlap", ", ".join(common[:3]))

    members: dict[str, list[_Occurrence]] = defaultdict(list)
    for occ in occurrences:
        members[clusters.find(occ.occ_id)].append(occ)
    evidence_by_root: dict[str, list[str]] = defaultdict(list)
    for occ_id, text in evidence:
        evidence_by_root[clusters.find(occ_id)].append(text)

    authors: list[AuthorEntity] = []
    authorships: list[Authorship] = []
    for root in sorted(members, key=_occ_sort_key):
        group = members[root]
        orcids = sorted(clusters.orcids[root])
        first = min((o.occ_id for o in group), key=_occ_sort_key)
        author_id = f"orcid:{orcids[0]}" if orcids else f"name:{group[0].key}:{first}"
        variants = Counter(o.author.name_as_written for o in group)
        display = max(variants, key=lambda name: (len(name), variants[name], name))
        authors.append(
            AuthorEntity(
                author_id=author_id,
                display_name=display,
                orcid=orcids[0] if orcids else None,
                name_key=group[0].key,
                name_variants=sorted(variants),
                merge_evidence=evidence_by_root[root],
                pmids=sorted({o.pmid for o in group}, key=int),
            )
        )
        for occ in group:
            authorships.append(
                Authorship(
                    pmid=occ.pmid,
                    position=occ.author.position,
                    author_id=author_id,
                    name_as_written=occ.author.name_as_written,
                    affiliations=occ.author.affiliations,
                    match_rule=rule_of.get(occ.occ_id, "singleton"),
                )
            )

    collective_groups: dict[str, list[tuple[PaperRecord, AuthorRecord]]] = defaultdict(
        list
    )
    for paper, a in collectives:
        collective_groups[
            "collective:" + normalise_name(a.name_as_written).replace(" ", "-")
        ].append((paper, a))
    for author_id, group in sorted(collective_groups.items()):
        authors.append(
            AuthorEntity(
                author_id=author_id,
                display_name=group[0][1].name_as_written,
                name_key=author_id,
                collective=True,
                name_variants=sorted({a.name_as_written for _, a in group}),
                pmids=sorted({p.pmid for p, _ in group}, key=int),
            )
        )
        for paper, a in group:
            authorships.append(
                Authorship(
                    pmid=paper.pmid,
                    position=a.position,
                    author_id=author_id,
                    name_as_written=a.name_as_written,
                    affiliations=a.affiliations,
                    match_rule="collective",
                )
            )

    authorships.sort(key=lambda s: (int(s.pmid), s.position))
    return AuthorResolution(authors=authors, authorships=authorships)
