"""The sandbox is the whole security model, so it gets tested first and hardest."""
import pytest

from macchat import safety


def test_accepts_path_inside_root(workspace):
    _, root, _, _ = workspace
    target = root / "sub" / "file.txt"
    assert safety.resolve_in_roots(target, [str(root)]) == target.resolve()


def test_accepts_the_root_itself(workspace):
    _, root, _, _ = workspace
    assert safety.resolve_in_roots(root, [str(root)]) == root.resolve()


def test_rejects_path_outside_root(workspace):
    _, root, _, outside = workspace
    with pytest.raises(safety.Denied):
        safety.resolve_in_roots(outside / "secret.txt", [str(root)])


def test_rejects_parent_traversal(workspace):
    _, root, _, _ = workspace
    with pytest.raises(safety.Denied):
        safety.resolve_in_roots(root / ".." / ".." / "etc" / "hosts", [str(root)])


def test_rejects_symlink_pointing_out_of_the_sandbox(workspace):
    """A link inside the root must not become a way out of it."""
    _, root, _, outside = workspace
    (outside / "secret.txt").write_text("private")
    (root / "escape").symlink_to(outside)

    with pytest.raises(safety.Denied):
        safety.resolve_in_roots(root / "escape" / "secret.txt", [str(root)])


def test_rejects_absolute_system_paths(workspace):
    _, root, _, _ = workspace
    for path in ("/etc/passwd", "/System/Library", "/"):
        with pytest.raises(safety.Denied):
            safety.resolve_in_roots(path, [str(root)])


def test_is_in_roots_mirrors_resolve(workspace):
    _, root, _, outside = workspace
    assert safety.is_in_roots(root / "ok.txt", [str(root)]) is True
    assert safety.is_in_roots(outside / "no.txt", [str(root)]) is False


def test_unique_destination_leaves_free_names_alone(workspace):
    _, root, _, _ = workspace
    target = root / "free.txt"
    assert safety.unique_destination(target) == target


def test_unique_destination_never_overwrites(workspace):
    _, root, _, _ = workspace
    (root / "taken.txt").write_text("original")
    first = safety.unique_destination(root / "taken.txt")
    assert first.name == "taken (1).txt"

    first.write_text("second")
    second = safety.unique_destination(root / "taken.txt")
    assert second.name == "taken (2).txt"
    assert (root / "taken.txt").read_text() == "original"
