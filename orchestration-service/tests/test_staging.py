from ingestion.staging import StagingArea, atomic_write_text, read_json


def test_layout(tmp_path):
    area = StagingArea(tmp_path, "v1")
    assert area.pmids_file == tmp_path / "v1" / "pmids.json"
    assert area.xml_batch(3) == tmp_path / "v1" / "xml" / "batch_0003.xml"
    assert area.parsed_batch(0).name == "batch_0000.json"
    assert area.mesh_cache_file == tmp_path / "mesh_cache.json"


def test_atomic_write_creates_parents_and_leaves_no_tmp(tmp_path):
    target = tmp_path / "a" / "b" / "data.json"
    atomic_write_text(target, '{"x": 1}')
    assert read_json(target) == {"x": 1}
    assert [p.name for p in target.parent.iterdir()] == ["data.json"]


def test_atomic_write_replaces_existing(tmp_path):
    target = tmp_path / "f.json"
    atomic_write_text(target, "[1]")
    atomic_write_text(target, "[2]")
    assert read_json(target) == [2]
