import dataclasses

import pytest
from temporalio.testing import ActivityEnvironment

from activities.ingestion import CorpusConfigMismatch, IngestionActivities
from activities.types import BatchInput
from config import Settings
from ingestion.staging import StagingArea

EFETCH = b"<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>1</PMID><Article><ArticleTitle>T</ArticleTitle></Article></MedlineCitation></PubmedArticle></PubmedArticleSet>"
ELINK = b"<eLinkResult><LinkSet><IdList><Id>1</Id></IdList></LinkSet></eLinkResult>"


class FakeNcbi:
    def __init__(self, pmids):
        self.pmids, self.calls = pmids, []

    async def esearch(self, term, mindate, maxdate, max_results):
        self.calls.append("esearch")
        return self.pmids

    async def efetch_xml(self, pmids):
        self.calls.append(("efetch", tuple(pmids)))
        return EFETCH

    async def elink_refs_xml(self, pmids):
        self.calls.append(("elink", tuple(pmids)))
        return ELINK


class FakeEvents:
    def __init__(self):
        self.events = []

    async def publish_ingest(self, run_id, type, content, meta=""):
        self.events.append((type, content))
        return "0-1"

    async def set_json(self, key, value, ttl_seconds=None):
        self.events.append(("json", key))


@pytest.fixture
def small_settings(local_env, tmp_path):
    return Settings.from_env(
        {
            **local_env,
            "STAGING_DIR": str(tmp_path),
            "FETCH_BATCH_SIZE": "2",
            "LINK_BATCH_SIZE": "2",
        }
    )


def make(settings, pmids):
    ncbi, events = FakeNcbi(pmids), FakeEvents()
    return (
        IngestionActivities(settings, ncbi, mesh=None, driver=None, events=events),
        ncbi,
        events,
    )


async def test_discover_pins_and_reuses_the_pmid_list(small_settings):
    acts, ncbi, _ = make(small_settings, ["1", "2", "3"])
    env = ActivityEnvironment()
    first = await env.run(acts.discover, "v1")
    assert (first.items, first.batches) == (3, 2)
    ncbi.pmids = ["1", "2", "3", "4"]  # PubMed changed…
    second = await env.run(acts.discover, "v1")  # …but the pinned list wins
    assert second.items == 3 and ncbi.calls.count("esearch") == 1


async def test_discover_refuses_changed_config_without_new_version(
    small_settings, local_env, tmp_path
):
    acts, _, _ = make(small_settings, ["1"])
    await ActivityEnvironment().run(acts.discover, "v1")
    changed = Settings.from_env(
        {
            **local_env,
            "STAGING_DIR": str(tmp_path),
            "FETCH_BATCH_SIZE": "2",
            "LINK_BATCH_SIZE": "2",
            "CORPUS_MAX_DATE": "2024",
        }
    )
    acts2, _, _ = make(changed, ["1"])
    with pytest.raises(CorpusConfigMismatch):
        await ActivityEnvironment().run(acts2.discover, "v1")


async def test_fetch_batch_never_refetches_staged_batches(small_settings):
    acts, ncbi, _events = make(small_settings, ["1", "2", "3"])
    env = ActivityEnvironment()
    await env.run(acts.discover, "v1")
    first = await env.run(acts.fetch_batch, BatchInput("v1", 1))
    second = await env.run(acts.fetch_batch, BatchInput("v1", 1))
    assert ncbi.calls.count(("efetch", ("3",))) == 1
    assert (first.cached, second.cached) == (0, 1)
    assert (
        StagingArea(small_settings.staging_dir, "v1").xml_batch(1).read_bytes()
        == EFETCH
    )


async def test_link_all_resumes_after_last_heartbeat(small_settings):
    acts, ncbi, _ = make(small_settings, ["1", "2", "3", "4", "5"])  # 3 link batches
    await ActivityEnvironment().run(acts.discover, "v1")
    env = ActivityEnvironment()
    env.info = dataclasses.replace(
        env.info, heartbeat_details=[0], attempt=2
    )  # batch 0 done before the crash
    beats = []
    env.on_heartbeat = lambda *details: beats.append(details[0])
    result = await env.run(acts.link_all, "v1")
    linked = [c[1] for c in ncbi.calls if c[0] == "elink"]
    assert linked == [("3", "4"), ("5",)]  # batch 0 (PMIDs 1,2) not requested again
    assert beats == [1, 2] and "resumed from batch 1" in result.detail
