# Ingestion report — corpus `v1`

Generated from the live run `ingest-v1` (completed 2026-10-30) with
`python orchestration-service/scripts/ingestion_report.py INGESTION_PMIDS.json`, plus the hand-written sections below.

## Corpus definition (from `.env`)

| Setting | Value |
|---|---|
| `CORPUS_QUERY` (PubMed esearch `term`) | `metformin[majr] AND neoplasms[majr] AND diabetes mellitus, type 2[mh] AND (cohort studies[mh] OR meta-analysis[pt])` |
| Date filter | `datetype=pdat`, `mindate=2005`, `maxdate=2025` |
| `CORPUS_MAX_RESULTS` | 400 (the query returned 275, so nothing was truncated) |
| `CORPUS_CUTOFF_YEAR` | 2016 |
| `CORPUS_VERSION` | `v1`; workflow id `ingest-v1`; the PMID list is pinned in the staging folder on the first run |

**Why the stored years run 2006–2026, not 2005–2025:** esearch filters on `pdat`, which counts the *electronic*
publication date. We store the journal issue's `<PubDate>` as `pub_year` + `pub_date` with a `date_precision`
(day 67, month 169, year 39 papers). One paper e-published in 2025 is in a 2026 issue, and one 2005 e-publication is in a 2006 issue.

## Acceptance criteria (live)

| Criterion | Minimum | Ours |
|---|---|---|
| Papers | 150–400 | 275 |
| Date span | 10 years | 2006–2026 (20 years) |
| Before / after 2016 | 40 / 40 | 77 / 198 |
| Intra-corpus CITES | 120 | 678 |
| Longest chain | 4 | 13 |
| Authors with 2+ papers | 25 | 135 |
| MeSH top-level trees | 3 | 14 (A, B, C, D, E, F, G, H, I, J, L, M, N, Z) |
| Papers with retraction/erratum/comment | 1 | 27 (retracted: 1) |

## Citations by source

- XML ReferenceList internal edges: 678; elink internal edges: 674
- both: 674, xml only: 4, elink only: 0
- dangling (outside corpus, skipped): xml 5154, elink 5080

## Node / relationship counts

```json
{
  "nodes": {
    "MeshTerm": 1099,
    "Author": 1649,
    "Paper": 275,
    "Journal": 146,
    "Notice": 36
  },
  "relationships": {
    "BROADER": 1418,
    "AUTHORED": 1877,
    "PUBLISHED_IN": 275,
    "MENTIONS": 3664,
    "COMMENTED_IN": 30,
    "CITES": 678,
    "COMMENTS_ON": 2,
    "CORRECTED_BY": 3,
    "RETRACTED_BY": 1
  }
}
```

## Skipped records

- none

## Pinned PMID list (275)

<details><summary>show</summary>

16443869, 17669713, 18555837, 19564453, 19717820, 19918015, 20947488, 20980414, 21148757, 21241523, 21251068, 21298495, 21800293, 21812892, 21949223, 22100960, 22183212, 22266734, 22386769, 22421948, 22448244, 22456867, 22502769, 22523334, 22643536, 22778198, 22789400, 22847511, 23137049, 23137378, 23244125, 23419783, 23566618, 23755906, 23853056, 23864581, 23966577, 24051965, 24133632, 24258613, 24496803, 24509842, 24612291, 24647047, 24837144, 24898303, 24920786, 24921909, 24944108, 24959880, 24985407, 25030683, 25041125, 25093492, 25201464, 25303400, 25329671, 25336750, 25400459, 25502158, 25534180, 25623201, 25644512, 25667109, 25684493, 25708557, 25762476, 25820555, 25913129, 26021280, 26063645, 26091848, 26255448, 26256299, 26331456, 26377195, 26446233, 26459254, 26513009, 26683519, 26721670, 26775128, 26861738, 26886616, 27026681, 27079349, 27124061, 27137425, 27159666, 27318643, 27423029, 27430290, 27486978, 27494848, 27496094, 27510392, 27519177, 27547763, 27587088, 27643646, 27775209, 27870711, 27903961, 27908558, 27926481, 28031313, 28071672, 28103573, 28166101, 28190681, 28271076, 28272215, 28274936, 28393359, 28409158, 28438547, 28445955, 28521859, 28539171, 28643317, 28645427, 28723808, 28811714, 28918830, 28985579, 29246826, 29320995, 29412502, 29465545, 29482528, 29558957, 29599368, 29618465, 29655834, 29716927, 29802690, 29808777, 29857030, 29860724, 29940252, 29956875, 29970546, 30016011, 30045293, 30055585, 30084749, 30173870, 30220322, 30281009, 30572481, 30663219, 30753459, 30803422, 30808843, 30881522, 30896668, 30950925, 31009857, 31025306, 31300371, 31345259, 31409137, 31470817, 31567536, 31593243, 31693512, 31699707, 31770138, 31787317, 31828167, 31908803, 32060719, 32100113, 32156062, 32182614, 32196659, 32369516, 32406122, 32507907, 32532664, 32532851, 32541629, 32581289, 32592092, 32605879, 32720184, 32798709, 32862601, 32925714, 32958585, 32968891, 32980534, 33058005, 33065527, 33105165, 33279415, 33464703, 33516777, 33516778, 33517287, 33639681, 33759443, 33836207, 33911196, 33976288, 34116220, 34332136, 34347359, 34428740, 34439890, 34620743, 34680039, 34713480, 34779535, 34893792, 34922421, 35140015, 35220243, 35269852, 35533029, 35585577, 35714677, 35846337, 35859114, 35901016, 36008432, 36139140, 36187118, 36260549, 36382924, 36422298, 36422484, 36444502, 36513569, 36533539, 36547172, 36583006, 36604960, 36662841, 36689497, 36708293, 36829129, 36974927, 37077173, 37099785, 37125965, 37679943, 37870939, 37889443, 37933831, 37987968, 38030210, 38114915, 38140692, 38215022, 38246042, 38291943, 38389430, 38444070, 38515954, 38518387, 38520039, 38615182, 38935094, 39202556, 39774829, 40239400, 40243198, 40336178, 40548761, 40572709, 40578405, 40731791, 40737255, 41178701

</details>

## Author identity (disambiguation)

PubMed has no author ids, so every (paper, author position) is an *occurrence*. Occurrences are clustered with union-find
(`orchestration-service/src/ingestion/authors.py`), in this order:

1. **Same ORCID → same person** (223 of 1,649 authors carry an ORCID; id `orcid:<orcid>`).
2. **Same name key (`last|first-initial`) + a shared co-author** on another paper.
3. **Same name key + affiliation overlap** (≥ 2 shared non-stopword affiliation tokens).

**Merges are blocked when:**
- the two clusters have different ORCIDs;
- their full given names conflict (`Javier` ≠ `Juan`, while initials-only names are compatible);
- the two occurrences are on the same paper.

Unmerged authors get the id `name:<key>:<pmid>/<position>`. Every merge stores its rule and evidence on the
`(:Author)` node (`merge_evidence`). Collective authors (3) are kept as `collective: true` nodes and excluded from
person statistics.

**Measured quality** (plan 07, `eval/author_pairs.csv`, hand-labelled):
- 29 candidate pairs with the same name key, sampled from both strata (520 merged / 337 kept-separate pairs in the corpus).
- TP 15, FP 0, FN 0, TN 14 → **precision 1.00, recall 1.00** on this sample.
- Caveats:
  - The sample is small.
  - Two labels were swapped by hand at first (`tseng|c`, `lee|m`) and were corrected after checking the papers. Human labelling error is real at this scale.
  - Known blind spot: two different people with the same initials who share a co-author would be merged.
  - Initials-only names with no co-author or affiliation evidence stay split, so recall depends on that evidence.

## Messy data seen in this corpus

- **Missing abstracts:** 3 papers (stored without `abstract`; the agent treats them as title-only).
- **MedlineDate / partial dates:** 1 paper had only a `<MedlineDate>` (kept verbatim in `pub_date_raw`); partial dates
  keep their precision (`date_precision`) — no day or month is invented.
- **Collective authors:** 3 (e.g. study groups), with no name key.
- **Retraction / errata / comments:**
  - 36 `(:Notice)` nodes on 27 papers;
  - 1 retraction (PMID 35269852, *Int J Mol Sci*).
- **Citations:** both XML `ReferenceList` and NCBI elink were used.
  - Edges kept only when both ends are in the corpus (678).
  - About 5,100 references point outside the corpus and are counted but skipped.
