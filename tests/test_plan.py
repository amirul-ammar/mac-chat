"""Staging, applying and undoing changes — the parts that actually touch the disk."""
from pathlib import Path


def test_propose_refuses_operations_outside_the_root(workspace):
    session, root, _, outside = workspace
    (outside / "victim.txt").write_text("x")

    result = session.propose_changes(
        "escape", [{"action": "trash", "src": str(outside / "victim.txt")}]
    )

    assert "Nothing staged" in result
    assert session.pending is None
    assert (outside / "victim.txt").exists()


def test_propose_skips_missing_files_but_keeps_valid_ones(workspace):
    session, root, _, _ = workspace
    (root / "real.txt").write_text("x")

    session.propose_changes("mixed", [
        {"action": "move", "src": str(root / "real.txt"), "dst": str(root / "moved.txt")},
        {"action": "move", "src": str(root / "ghost.txt"), "dst": str(root / "nowhere.txt")},
    ])

    assert session.pending is not None
    assert len(session.pending.operations) == 1


def test_propose_does_not_touch_the_disk(workspace):
    session, root, _, _ = workspace
    (root / "stays.txt").write_text("x")

    session.propose_changes(
        "move it", [{"action": "move", "src": str(root / "stays.txt"),
                     "dst": str(root / "sub" / "stays.txt")}]
    )

    assert (root / "stays.txt").exists(), "staging must not move anything"
    assert not (root / "sub").exists()


def test_apply_then_undo_round_trip(workspace):
    session, root, _, _ = workspace
    (root / "a.txt").write_text("content a")
    (root / "b.txt").write_text("content b")

    session.propose_changes("tidy", [
        {"action": "new_folder", "dst": str(root / "archive")},
        {"action": "move", "src": str(root / "a.txt"), "dst": str(root / "archive" / "a.txt")},
        {"action": "move", "src": str(root / "b.txt"), "dst": str(root / "archive" / "b.txt")},
    ])
    done, errors = session.apply(session.pending)

    assert (done, errors) == (3, [])
    assert (root / "archive" / "a.txt").read_text() == "content a"
    assert not (root / "a.txt").exists()

    session.undo_last()

    assert (root / "a.txt").read_text() == "content a"
    assert (root / "b.txt").read_text() == "content b"
    assert not (root / "archive" / "a.txt").exists()


def test_trash_moves_to_trash_dir_and_undo_restores(workspace):
    session, root, trash, _ = workspace
    (root / "junk.log").write_text("noise")

    session.propose_changes("bin it", [{"action": "trash", "src": str(root / "junk.log")}])
    session.apply(session.pending)

    assert not (root / "junk.log").exists()
    assert (trash / "junk.log").read_text() == "noise", "trash must be recoverable"

    session.undo_last()
    assert (root / "junk.log").read_text() == "noise"


def test_apply_never_overwrites_an_existing_file(workspace):
    session, root, _, _ = workspace
    (root / "sub").mkdir()
    (root / "sub" / "dup.txt").write_text("i was here first")
    (root / "dup.txt").write_text("incoming")

    session.propose_changes("move", [
        {"action": "move", "src": str(root / "dup.txt"), "dst": str(root / "sub" / "dup.txt")},
    ])
    session.apply(session.pending)

    assert (root / "sub" / "dup.txt").read_text() == "i was here first"
    assert (root / "sub" / "dup (1).txt").read_text() == "incoming"


def test_undo_only_reverses_the_last_batch(workspace):
    session, root, _, _ = workspace
    (root / "one.txt").write_text("1")
    (root / "two.txt").write_text("2")

    session.propose_changes("first", [
        {"action": "move", "src": str(root / "one.txt"), "dst": str(root / "one-moved.txt")}])
    session.apply(session.pending)
    session.propose_changes("second", [
        {"action": "move", "src": str(root / "two.txt"), "dst": str(root / "two-moved.txt")}])
    session.apply(session.pending)

    session.undo_last()

    assert (root / "two.txt").exists(), "second batch reversed"
    assert (root / "one-moved.txt").exists(), "first batch left alone"


def test_propose_sort_groups_files_by_category(sample_files):
    session, root, _, _ = sample_files

    session.propose_sort(str(root), "type")
    session.apply(session.pending)

    assert (root / "Documents" / "notes.md").exists()
    assert (root / "Spreadsheets" / "data.csv").exists()
    assert (root / "Code" / "script.py").exists()
    assert (root / "Images" / "photo.png").exists()
    assert (root / "Video" / "clip.mp4").exists()


def test_propose_sort_is_reversible(sample_files):
    session, root, _, _ = sample_files
    before = sorted(p.name for p in root.iterdir())

    session.propose_sort(str(root), "type")
    session.apply(session.pending)
    session.undo_last()

    assert sorted(p.name for p in root.iterdir() if p.is_file()) == before
