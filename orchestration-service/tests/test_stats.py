from graph.stats import longest_chain


def test_longest_chain_counts_papers_not_edges():
    assert longest_chain([("A", "B"), ("B", "C"), ("C", "D"), ("A", "D")]) == 4


def test_longest_chain_survives_cycles():
    assert longest_chain([("A", "B"), ("B", "A"), ("B", "C")]) == 3


def test_empty():
    assert longest_chain([]) == 0
