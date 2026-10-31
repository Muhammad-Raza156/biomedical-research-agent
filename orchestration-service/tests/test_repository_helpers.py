import pytest

from graph.repository import VERIFY_TEMPLATES, author_key_from_text


@pytest.mark.parametrize(("text", "key"), [
    ("Smith J", "smith|j"), ("J. Smith", "smith|j"), ("John Smith", "smith|j"),
    ("Menéndez JA", "menendez|j"), ("Smith", None),
])
def test_author_key_from_text(text, key):
    assert author_key_from_text(text) == key


def test_every_assertion_kind_has_a_template():
    from agent.schemas import Assertion
    kinds = Assertion.model_fields["kind"].annotation.__args__
    assert set(kinds) == set(VERIFY_TEMPLATES)
