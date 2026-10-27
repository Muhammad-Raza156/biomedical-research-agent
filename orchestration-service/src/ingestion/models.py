"""Typed records produced by the PubMed XML parser. JSON-serialisable for staging."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

RETRACTION_TYPES = {"RetractionIn"}
RETRACTED_PUBLICATION_TYPE = "Retracted Publication"


class PubDate(BaseModel):
    """Publication date with explicit precision (never invent missing month/day)."""

    year: int
    month: int | None = None
    day: int | None = None
    precision: Literal["year", "month", "day"]
    raw: str | None = None  # original MedlineDate text, if that's what we parsed

    @property
    def iso(self) -> str:
        """ISO-8601 string truncated to the known precision."""
        if self.precision == "day":
            return f"{self.year:04d}-{self.month:02d}-{self.day:02d}"
        if self.precision == "month":
            return f"{self.year:04d}-{self.month:02d}"
        return f"{self.year:04d}"


class AuthorRecord(BaseModel):
    """One entry of AuthorList, in order. Either a person or a collective."""

    position: int
    last_name: str | None = None
    fore_name: str | None = None
    initials: str | None = None
    collective_name: str | None = None
    orcid: str | None = None
    affiliations: list[str] = Field(default_factory=list)

    @property
    def is_collective(self) -> bool:
        """True for group authors such as 'DPP Research Group'."""
        return self.collective_name is not None

    @property
    def name_as_written(self) -> str:
        """Name exactly as PubMed lists it (stored on the AUTHORED relationship)."""
        if self.collective_name:
            return self.collective_name
        return " ".join(part for part in (self.last_name, self.fore_name or self.initials) if part)


class JournalRecord(BaseModel):
    """Journal identity; `key` is the natural key used for MERGE in Neo4j."""

    key: str
    key_type: Literal["issn_l", "print", "electronic", "nlm"]
    title: str | None = None
    issn_linking: str | None = None
    issn_print: str | None = None
    issn_electronic: str | None = None


class MeshHeadingRecord(BaseModel):
    """A MeSH descriptor attached to a paper."""

    ui: str
    name: str
    major: bool
    qualifiers: list[str] = Field(default_factory=list)


class CorrectionRecord(BaseModel):
    """CommentsCorrections entry: RetractionIn, ErratumIn, RetractionOf, CommentIn, …"""

    ref_type: str
    pmid: str | None = None
    source: str = ""


class PaperRecord(BaseModel):
    """Everything we keep about one PubmedArticle."""

    pmid: str
    title: str
    abstract: str | None = None
    pub_date: PubDate | None = None
    doi: str | None = None
    publication_types: list[str] = Field(default_factory=list)
    journal: JournalRecord | None = None
    authors: list[AuthorRecord] = Field(default_factory=list)
    mesh: list[MeshHeadingRecord] = Field(default_factory=list)
    xml_references: list[str] = Field(default_factory=list)
    corrections: list[CorrectionRecord] = Field(default_factory=list)

    @property
    def retracted(self) -> bool:
        """True if PubMed marks this paper as retracted (either signal is enough)."""
        return (RETRACTED_PUBLICATION_TYPE in self.publication_types
                or any(c.ref_type in RETRACTION_TYPES for c in self.corrections))


class SkippedRecord(BaseModel):
    """A record we could not use, with the reason (reported, never silently dropped)."""

    pmid: str | None = None
    reason: str


class ParseResult(BaseModel):
    """Output of parsing one efetch batch."""

    papers: list[PaperRecord] = Field(default_factory=list)
    skipped: list[SkippedRecord] = Field(default_factory=list)
