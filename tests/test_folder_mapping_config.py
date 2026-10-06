"""SPEC-ADDON-004: a sync-config with a bad path, a duplicate or overlapping remotes syncs nothing.

Also the path rule of SPEC-ADDON-003 as it shows in `GET /status`.
"""
from __future__ import annotations

import logging
import unicodedata

import pytest

from mapping_world import ABSENT, mworld, settle  # noqa: F401


# SPEC-ADDON-003: one status entry per mapping, in file order, carrying drive and path.
def test_status_lists_every_mapping_in_file_order_with_its_path(mworld):
    mworld.add_drive("Movies")
    mworld.add_drive("Photos")
    mworld.map("Movies")
    mworld.map("Photos", "b")
    mworld.map("Photos", "a")
    mworld.map("Photos", "")

    assert mworld.pairs() == [
        ("Movies", ""),
        ("Photos", "b"),
        ("Photos", "a"),
        ("Photos", ""),
    ]


NFD = unicodedata.normalize("NFD", "がぎ")


# SPEC-ADDON-003, path rule: the normalized form is what status reports.
@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        (ABSENT, ""),
        (None, ""),
        ("", ""),
        (".", ""),
        ("./", ""),
        ("仕事/録画", "仕事/録画"),
        ("仕事/録画/", "仕事/録画"),
        ("./仕事//録画", "仕事/録画"),
        ("仕事/./録画", "仕事/録画"),
        (" a /b ", " a /b "),
        ("a\\b", "a\\b"),
        ("Case/MiXed", "Case/MiXed"),
        (NFD, NFD),
    ],
    ids=[
        "absent", "null", "empty", "dot", "dot-slash", "plain", "trailing-slash",
        "dot-and-double-slash", "inner-dot", "whitespace-kept", "backslash-kept",
        "case-kept", "nfd-kept",
    ],
)
def test_status_carries_the_normalized_path(mworld, raw, normalized):
    mworld.add_drive("Photos")
    mworld.map("Photos", raw)

    assert mworld.pairs() == [("Photos", normalized)]


REJECTED_PATHS = [
    1,
    1.5,
    True,
    [],
    ["a"],
    {},
    {"a": "b"},
    "/",
    "/a",
    "//a",
    "..",
    "../a",
    "a/..",
    "a/../b",
    "./..",
    "a\0b",
]


# SPEC-ADDON-004 (I4): one rejected path rejects the whole file, schedule included.
@pytest.mark.parametrize("bad", REJECTED_PATHS, ids=[repr(p) for p in REJECTED_PATHS])
def test_a_rejected_path_rejects_the_whole_file(mworld, caplog, bad):
    mworld.add_drive("Photos")
    mworld.add_drive("Movies")
    mworld.schedule = "0 */6 * * *"
    mworld.map("Movies")
    mworld.map("Photos", bad)

    with caplog.at_level(logging.ERROR):
        status = mworld.manager.get_status()

    assert status.drives == []
    assert status.schedule is None
    assert any(r.levelno == logging.ERROR for r in caplog.records)


DUPLICATES = [
    ("a", "a"),
    ("a", "a/"),
    ("a/b", "./a//b"),
    (ABSENT, ""),
    (ABSENT, None),
    ("", "."),
]


# SPEC-ADDON-004 (I4): same drive and same normalized path twice.
@pytest.mark.parametrize(("first", "second"), DUPLICATES)
def test_a_duplicate_mapping_rejects_the_whole_file(mworld, caplog, first, second):
    mworld.add_drive("Photos")
    mworld.map("Photos", first, remote="r:one")
    mworld.map("Photos", second, remote="r:two")

    with caplog.at_level(logging.ERROR):
        pairs = mworld.pairs()

    assert pairs == []
    assert any(r.levelno == logging.ERROR for r in caplog.records)


# SPEC-ADDON-003: the identity is the pair, so these are distinct mappings.
@pytest.mark.parametrize(
    ("first", "second"),
    [
        (("Photos", "a"), ("Photos", "b")),
        (("Photos", "a"), ("Movies", "a")),
        (("Photos", "a"), ("Photos", "A")),
        (("Photos", ""), ("Photos", "a")),
    ],
)
def test_distinct_pairs_are_both_accepted(mworld, first, second):
    mworld.add_drive("Photos")
    mworld.add_drive("Movies")
    mworld.map(first[0], first[1], remote="r:one")
    mworld.map(second[0], second[1], remote="r:two")

    assert mworld.pairs() == [first, second]


OVERLAPPING = [
    ("gd:a", "gd:a"),
    ("gd:a", "gd:a/b"),
    ("gd:a/b", "gd:a"),
    ("gd:a", "gd:/a"),
    ("gd:a", "gd:a/"),
    ("gd:", "gd:x"),
    ("gd:/", "gd:x/y"),
    ("gd:", "gd:/"),
    ("gd:a//b", "gd:a/b/c"),
    ("gd:x/10:30", "gd:x"),
]

NOT_OVERLAPPING = [
    ("gd:a", "gd:ab"),
    ("gd:a", "GD:a"),
    ("gd:a", "gd:A"),
    ("gd:a/b", "gd:a/c"),
    ("gd:a", "gd2:a"),
    ("gd:a:b", "gd:a"),
]


# SPEC-ADDON-004 (I4): equal or nested remotes, whatever the drives.
@pytest.mark.parametrize("same_drive", [False, True], ids=["two-drives", "one-drive"])
@pytest.mark.parametrize(("r1", "r2"), OVERLAPPING)
def test_overlapping_remotes_reject_the_whole_file(mworld, caplog, r1, r2, same_drive):
    mworld.add_drive("Photos")
    mworld.add_drive("Movies")
    mworld.map("Photos", "a", remote=r1)
    mworld.map("Photos" if same_drive else "Movies", "b", remote=r2)

    with caplog.at_level(logging.ERROR):
        pairs = mworld.pairs()

    assert pairs == []
    assert any(r.levelno == logging.ERROR for r in caplog.records)


# SPEC-ADDON-004 (I4): remotes that do not overlap by the rule are accepted.
@pytest.mark.parametrize(("r1", "r2"), NOT_OVERLAPPING)
def test_remotes_that_do_not_overlap_are_accepted(mworld, r1, r2):
    mworld.add_drive("Photos")
    mworld.add_drive("Movies")
    mworld.map("Photos", "a", remote=r1)
    mworld.map("Movies", "b", remote=r2)

    assert mworld.pairs() == [("Photos", "a"), ("Movies", "b")]


def _error_text(caplog) -> str:
    parts = []
    for record in caplog.records:
        if record.levelno >= logging.ERROR:
            parts.append(record.getMessage())
            if record.exc_text:
                parts.append(record.exc_text)
    return "\n".join(parts)


# SPEC-ADDON-004, item 8: the ERROR line names the offending values.
@pytest.mark.parametrize(
    ("mappings", "offending"),
    [
        (
            [("Photos", "dup-folder-x", "r:one"), ("Photos", "dup-folder-x/", "r:two")],
            ["dup-folder-x"],
        ),
        (
            [("Photos", "a", "gd:overlap-root"), ("Movies", "b", "gd:overlap-root/kid")],
            ["gd:overlap-root", "gd:overlap-root/kid"],
        ),
        ([("Photos", "/abs-folder-x", "r:one")], ["/abs-folder-x"]),
    ],
    ids=["duplicate", "overlap", "invalid-path"],
)
def test_the_error_line_names_the_offending_values(mworld, caplog, mappings, offending):
    mworld.add_drive("Photos")
    mworld.add_drive("Movies")
    for drive, path, remote in mappings:
        mworld.map(drive, path, remote=remote)

    with caplog.at_level(logging.ERROR):
        assert mworld.manager.get_status().drives == []

    text = _error_text(caplog)
    for value in offending:
        assert value in text


# SPEC-ADDON-004 (I4): a rejected file syncs nothing from the schedule.
async def test_a_rejected_file_starts_nothing_on_schedule(mworld):
    mworld.add_drive("Photos")
    mworld.add_drive("Movies")
    mworld.map("Photos", "a", remote="gd:x")
    mworld.map("Movies", "", remote="gd:x/y")

    await mworld.manager._run_scheduled_sync()
    await settle()

    assert mworld.launches == []
    assert mworld.pairs() == []
