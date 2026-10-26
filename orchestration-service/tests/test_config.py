import pytest

from config import ConfigError, Settings


def test_defaults_and_types(local_env):
    s = Settings.from_env(local_env)
    assert s.corpus_cutoff_year == 2016
    assert s.fetch_batch_size == 50
    assert s.ncbi_rate == 2.5                # no NCBI_API_KEY → keyless rate
    assert s.postgres_dsn.startswith("postgresql://langgraph:")
    assert s.postgres_dsn.endswith("@localhost:15432/langgraph")


def test_api_key_switches_rate(local_env):
    s = Settings.from_env({**local_env, "NCBI_API_KEY": "abc"})
    assert s.ncbi_rate == 9.0


def test_missing_required_variable_fails_fast(local_env):
    del local_env["GRAPH_PASSWORD"]
    with pytest.raises(ConfigError, match="GRAPH_PASSWORD"):
        Settings.from_env(local_env)


def test_numbers_tolerate_whitespace(local_env):
    s = Settings.from_env({**local_env, "FETCH_BATCH_SIZE": " 40 "})
    assert s.fetch_batch_size == 40


def test_settings_are_frozen(local_env):
    s = Settings.from_env(local_env)
    with pytest.raises(AttributeError):
        s.corpus_version = "v2"  # type: ignore[misc]
