"""The handoff helper program, run on the host against a temporary tree.

:mod:`sciagent.sandbox._handoff_helper` is what the helper container runs
(as container root, with ``CHOWN``, ``DAC_OVERRIDE`` and ``FOWNER``). Its
logic does not depend on running in a container, so it is exercised here
directly, handing a tree to the current user (which needs no privilege). The
ACL half needs a filesystem with POSIX ACLs and the capabilities to strip
another owner's, so it is tested with real containers in
``test_sandbox_isolation.py``.
"""

from __future__ import annotations

import os
import resource
import stat
import struct
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from sciagent.sandbox import _handoff_helper as helper

OWNER = f"{os.getuid()}:{os.getgid()}"
HELPER = Path(helper.__file__).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("mode", "is_dir", "expected"),
    [
        (0o777, False, 0o755),  # go-w
        (0o000, False, 0o600),  # u+rw
        (0o040, False, 0o640),
        (0o111, False, 0o711),  # X: already executable by someone
        (0o644, False, 0o644),
        (0o4755, False, 0o755),  # no set-uid file handed to the host user
        (0o2755, False, 0o755),  # nor set-gid
        (0o000, True, 0o700),  # X on a directory
        (0o777, True, 0o755),
        (0o2775, True, 0o2755),  # a directory keeps set-gid (inheritance only)
    ],
)
def test_fixed_mode_is_u_plus_rwX_go_minus_w(
    mode: int, is_dir: bool, expected: int
) -> None:
    assert helper.fixed_mode(mode, is_dir=is_dir) == expected


def _tree(root: Path) -> None:
    (root / "a" / "b").mkdir(parents=True)
    (root / "a" / "b" / "f").write_text("x")
    (root / "g").write_text("y")
    os.chmod(root / "g", 0o777)
    os.chmod(root / "a" / "b" / "f", 0o4000)
    os.chmod(root / "a" / "b", 0o777)
    os.mkfifo(root / "fifo")
    os.symlink("/", root / "link")


def _run(
    root: Path, entries: int, path_bytes: int, capsys: pytest.CaptureFixture[str]
) -> str:
    assert helper.main([OWNER, str(entries), str(path_bytes), str(root)]) == 0
    return capsys.readouterr().out


def test_hands_a_tree_over_without_following_links(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    outside = tmp_path / "outside"
    outside.write_text("host")
    os.chmod(outside, 0o666)
    root = tmp_path / "work"
    root.mkdir()
    os.chmod(root, 0o777)
    _tree(root)
    os.symlink(outside, root / "a" / "out")
    assert _run(root, 100, 1024, capsys) == "ok\n"
    modes = {
        p.relative_to(root).as_posix(): stat.S_IMODE(p.lstat().st_mode)
        for p in root.rglob("*")
        if not p.is_symlink()
    }
    assert modes == {
        "a": 0o755,
        "a/b": 0o755,
        "a/b/f": 0o600,
        "fifo": 0o644,
        "g": 0o755,
    }
    assert stat.S_IMODE(root.stat().st_mode) == 0o755
    # The link was not followed: its target keeps its mode.
    assert stat.S_IMODE(outside.stat().st_mode) == 0o666
    # Special files are left for the host's sweep, which reports them.
    assert (root / "fifo").exists() and (root / "link").is_symlink()


def test_a_tree_within_the_limits_is_kept(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "work"
    root.mkdir()
    _tree(root)  # a, a/b, a/b/f, g, fifo, link: six entries
    assert _run(root, 6, 1024, capsys) == "ok\n"
    assert len(list(root.rglob("*"))) == 6


def test_a_tree_over_the_entry_limit_is_emptied(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "work"
    root.mkdir()
    _tree(root)
    assert _run(root, 5, 1024, capsys) == "emptied\n"
    assert list(root.iterdir()) == []
    assert stat.S_IMODE(root.stat().st_mode) == 0o755


def test_a_path_over_the_length_limit_empties_the_tree(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "work"
    root.mkdir()
    deep = root.joinpath(*["d"] * 10)  # "d/d/.../d": 19 bytes
    deep.mkdir(parents=True)
    assert _run(root, 100, 19, capsys) == "ok\n"
    assert deep.is_dir()
    (deep / "e").mkdir()  # 21 bytes
    assert _run(root, 100, 20, capsys) == "emptied\n"
    assert list(root.iterdir()) == []


def test_the_counting_stops_at_the_limit(tmp_path: Path) -> None:
    """Counting a tree of millions must cost the limit, not the tree."""
    root = tmp_path / "work"
    root.mkdir()
    for i in range(50):
        (root / f"f{i:02d}").touch()
    assert helper.within_limits(str(root), 50, 1024)
    assert not helper.within_limits(str(root), 49, 1024)


def test_counting_reads_no_entry_past_the_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The count stops at the first entry past the limit: of 1,000 files, a
    limit of 10 reads 11, however the directory orders them."""
    root = tmp_path / "work"
    root.mkdir()
    for i in range(1000):
        (root / f"f{i:04d}").touch()
    read = 0
    real_scandir = os.scandir

    def counting_scandir(path: str) -> Iterator[os.DirEntry[str]]:
        nonlocal read
        with real_scandir(path) as entries:
            for entry in entries:
                read += 1
                yield entry

    monkeypatch.setattr(os, "scandir", counting_scandir)
    assert not helper.within_limits(str(root), 10, 1024)
    assert read == 11


def _acl(uid: int) -> bytes:
    """``user::rwx user:<uid>:rwx group::r-x mask::rwx other::r-x``, as xattr."""
    undefined = 0xFFFFFFFF
    entries = [
        (0x01, 7, undefined),
        (0x02, 7, uid),
        (0x04, 5, undefined),
        (0x10, 7, undefined),
        (0x20, 5, undefined),
    ]
    return struct.pack("<I", 2) + b"".join(
        struct.pack("<HHI", tag, perm, who) for tag, perm, who in entries
    )


def test_the_handoff_strips_acls(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """On the host, as the files' owner (who may set and remove their ACLs):
    the fast counterpart of the container test in ``test_sandbox_isolation``."""
    root = tmp_path / "work"
    root.mkdir()
    (root / "w").write_text("x")
    (root / "d").mkdir()
    acl = _acl(12345)
    os.setxattr(root / "w", helper.ACL_ACCESS, acl)
    os.setxattr(root / "d", helper.ACL_ACCESS, acl)
    os.setxattr(root / "d", helper.ACL_DEFAULT, acl)
    os.setxattr(root, helper.ACL_DEFAULT, acl)
    assert _run(root, 100, 1024, capsys) == "ok\n"
    for path in (root, root / "w", root / "d"):
        assert os.listxattr(path, follow_symlinks=False) == [], path


def _chain(root: Path, depth: int, name: str = "d") -> None:
    """A chain ``depth`` directories deep, built by descriptor (no path limit)."""
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for _ in range(depth):
            os.mkdir(name, dir_fd=fd)
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY, dir_fd=fd)
            os.close(fd)
            fd = child
        os.close(os.open("bottom", os.O_WRONLY | os.O_CREAT, 0o644, dir_fd=fd))
    finally:
        os.close(fd)


def _remove(path: Path) -> None:
    """``rm -rf``: pytest's own cleanup (``shutil.rmtree``) recurses per level."""
    subprocess.run(["rm", "-rf", "--", str(path)], check=True)


def _few_open_files() -> None:
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))


@pytest.mark.slow  # about 4 s: building the 20,000 levels
def test_empty_takes_a_chain_far_deeper_than_open_files_allow(
    tmp_path: Path,
) -> None:
    """The helper's deletion must not keep a descriptor (or a scandir buffer)
    per level: the agent can nest directories for a whole call. Run as the
    container runs it (``python -I -B -c``), with 64 open files allowed, on
    20,000 levels beside a wide directory and a name the helper itself uses."""
    root = tmp_path / "work"
    root.mkdir()
    try:
        _chain(root, 20_000)
        (root / "f").write_text("y")
        os.symlink("/", root / "l")
        (root / "w").mkdir()
        for i in range(300):
            (root / "w" / f"{i}").touch()
        _chain(root / "w", 50, name="e")
        (root / ".sciagent-queue-0").write_text("a name the helper uses")
        out = subprocess.run(
            # A 60-byte path limit: counting opens at most 31 levels.
            [sys.executable, "-I", "-B", "-c", HELPER, OWNER, "100", "60", str(root)],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
            preexec_fn=_few_open_files,
        )
        assert out.returncode == 0, out.stderr[-2000:]
        assert out.stdout == "emptied\n"
        assert list(root.iterdir()) == []
    finally:
        _remove(root)


def test_empty_keeps_the_root_and_removes_every_kind_of_entry(
    tmp_path: Path,
) -> None:
    root = tmp_path / "work"
    root.mkdir()
    _tree(root)
    (root / "a" / "b" / "c").mkdir()
    helper.empty(str(root))
    assert root.is_dir() and list(root.iterdir()) == []
    helper.empty(str(root))  # an empty root is fine too
    assert list(root.iterdir()) == []


def test_refuses_a_malformed_command_line(tmp_path: Path) -> None:
    for argv in (
        [],
        ["0:0", "1", "1"],
        ["root", "1", "1", str(tmp_path)],
        ["0:0", "x", "1", str(tmp_path)],
        ["0:0", "1", "1", str(tmp_path), "extra"],
    ):
        with pytest.raises(SystemExit) as caught:
            helper.main(argv)
        assert caught.value.code == 2
