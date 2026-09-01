"""Query sanitising and formatting helpers."""
import pytest

from macchat import tools


@pytest.mark.parametrize("raw, expected", [
    ("budget", '"budget"'),
    ("q3 budget", '"q3" "budget"'),
    ('drop "quotes"', '"drop" "quotes"'),
    ("a", ""),                       # single characters are dropped
    ("", ""),
])
def test_fts_query_quotes_terms(raw, expected):
    assert tools.fts_query(raw) == expected


@pytest.mark.parametrize("raw", [
    'x" OR docs_fts MATCH "',
    "budget); DROP TABLE files;--",
    "*/**",
])
def test_fts_query_strips_syntax_that_could_break_the_match(raw):
    """FTS5 MATCH is its own little language — nothing hostile should survive."""
    out = tools.fts_query(raw)
    assert '"' not in out.replace('" "', " ").strip('"')
    assert ";" not in out and "*" not in out and "(" not in out


def test_search_content_rejects_a_query_with_no_usable_terms(workspace):
    session, _, _, _ = workspace
    assert "Error" in session.search_content(query="!!")


def test_search_content_refuses_folders_outside_the_root(workspace):
    session, _, _, outside = workspace
    assert "outside the allowed folders" in session.search_content(query="anything",
                                                                   folder=str(outside))


def test_read_file_refuses_paths_outside_the_root(workspace):
    session, _, _, outside = workspace
    (outside / "secret.txt").write_text("private")
    assert "outside the allowed folders" in session.read_file(path=str(outside / "secret.txt"))


def test_list_folder_refuses_paths_outside_the_root(workspace):
    session, _, _, outside = workspace
    assert "outside the allowed folders" in session.list_folder(path=str(outside))


@pytest.mark.parametrize("size, expected", [
    (0, "0B"), (512, "512B"), (2048, "2.0KB"), (5 * 1024**2, "5.0MB"),
])
def test_human_size(size, expected):
    assert tools.human_size(size) == expected
