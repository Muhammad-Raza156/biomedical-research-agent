from ingestion.mesh_enrichment import enrich_mesh


class FakeMesh:
    def __init__(self):
        self.calls = 0

    async def tree_numbers(self, uis):
        self.calls += 1
        data = {"D008687": ("Metformin", ["D02.078"]), "D009369": ("Neoplasms", ["C04"])}
        return {ui: data[ui] for ui in uis if ui in data}

    async def descriptors_at(self, tns):
        self.calls += 1
        data = {"D02": ("D009930", "Organic Chemicals")}
        return {tn: data[tn] for tn in tns if tn in data}


NAMES = {"D008687": "Metformin", "D009369": "Neoplasms", "D005260": "Female"}


async def test_enrichment_builds_hierarchy_and_records_terms_without_tree_numbers(tmp_path):
    client = FakeMesh()
    hierarchy, stats = await enrich_mesh(NAMES, tmp_path / "cache.json", client)
    uis = {t.ui for t in hierarchy.terms}
    assert {"D008687", "D009369", "D005260", "D009930", "TREE_C", "TREE_D"} <= uis
    assert stats.without_tree_numbers == 1
    assert stats.calls_made == client.calls == 2


async def test_second_run_makes_zero_calls(tmp_path):
    cache = tmp_path / "cache.json"
    first, _ = await enrich_mesh(NAMES, cache, FakeMesh())
    client = FakeMesh()
    second, stats = await enrich_mesh(NAMES, cache, client)
    assert client.calls == 0 and stats.calls_made == 0
    assert second == first
