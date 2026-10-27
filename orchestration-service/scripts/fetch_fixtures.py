"""Download the real PubMed records used as parser fixtures (run once, commit the output)."""

import time
from pathlib import Path

import httpx

PMIDS = ["41178701", "37099785", "40243198", "33516777", "35269852", "24647047", "40578405"]
OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "pubmed"
OUT.mkdir(parents=True, exist_ok=True)

with httpx.Client(timeout=60) as http:
    for pmid in PMIDS:
        response = http.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
                            params={"db": "pubmed", "id": pmid, "retmode": "xml"})
        response.raise_for_status()
        (OUT / f"{pmid}.xml").write_bytes(response.content)
        print("saved", pmid)
        time.sleep(0.5)  # stay under 3 requests/second
