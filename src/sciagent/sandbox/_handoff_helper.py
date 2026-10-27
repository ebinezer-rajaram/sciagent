"""The program the ownership handoff's helper container runs.

Not executed on the host at run time: :func:`.runner.build_handoff_argv`
sends this file's source to ``python -I -B -c`` in a helper container from
the sandbox image, as container root holding only ``CHOWN``, ``DAC_OVERRIDE``
and ``FOWNER``, with only ``work/`` mounted. It is a module so the host's tests
can run the same logic on a temporary tree (``tests/sandbox``). Standard
library only: it runs on the image's interpreter, and ``-I`` keeps ``/work``
(the agent's files) off ``sys.path``.

Command line: ``OWNER MAX_ENTRIES MAX_PATH_BYTES ROOT``. It prints one line,
``ok`` or ``emptied``, and exits 0; anything else is a harness fault.

1. **Limits.** If the tree under ``ROOT`` holds more than ``MAX_ENTRIES``
   entries, or an entry whose path relative to ``ROOT`` is longer than
   ``MAX_PATH_BYTES``, everything under ``ROOT`` is deleted (``emptied``). The
   count stops at the first entry past a limit, so counting costs at most the
   limit however many files the code wrote, and the walk never goes deeper
   than the path limit allows. Deletion is GNU ``find -delete``, which handles
   any depth and width.
2. **Handoff.** Every entry, and ``ROOT`` itself, is given to ``OWNER``
   (``lchown``: a symbolic link is never followed); every entry that is not a
   link loses its POSIX ACLs (access and default) and gets
   :func:`fixed_mode`. Special files are left in place for the host's sweep,
   which removes and reports them.

The tree is static while this runs: the agent's container has been confirmed
gone before the helper starts, so no path can be swapped under it.
"""

from __future__ import annotations

import errno
import os
import stat
import subprocess
import sys

#: The extended attributes that carry POSIX ACLs.
ACL_ACCESS = "system.posix_acl_access"
ACL_DEFAULT = "system.posix_acl_default"

OK = "ok"
EMPTIED = "emptied"

_USAGE = "usage: OWNER(uid:gid) MAX_ENTRIES MAX_PATH_BYTES ROOT"


def fixed_mode(mode: int, *, is_dir: bool) -> int:
    """``chmod u+rwX,go-w``, and no set-uid or set-gid bit on a non-directory.

    ``X`` is execute for the owner on a directory or on a file someone may
    already execute. Set-id bits are cleared on files because a set-uid file
    owned by the host user would run as the host user for anyone who could
    reach it; a directory keeps set-gid, which only steers group inheritance.
    """
    m = stat.S_IMODE(mode) | stat.S_IRUSR | stat.S_IWUSR
    if is_dir or m & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
        m |= stat.S_IXUSR
    m &= ~(stat.S_IWGRP | stat.S_IWOTH)
    if not is_dir:
        m &= ~(stat.S_ISUID | stat.S_ISGID)
    return m


def within_limits(root: str, max_entries: int, max_path_bytes: int) -> bool:
    """Whether the tree under ``root`` is within both limits (module docstring).

    Lazy and depth-first: at most ``max_entries + 1`` entries are read, and a
    directory is entered only while its path is within ``max_path_bytes``, so
    at most ``max_path_bytes // 2 + 1`` directories are open at once.
    """
    count = 0
    stack = [(os.scandir(root), 0)]
    try:
        while stack:
            iterator, base = stack[-1]
            entry = next(iterator, None)
            if entry is None:
                iterator.close()
                stack.pop()
                continue
            count += 1
            length = base + len(os.fsencode(entry.name)) + (1 if base else 0)
            if count > max_entries or length > max_path_bytes:
                return False
            if entry.is_dir(follow_symlinks=False):
                stack.append((os.scandir(entry.path), length))
    finally:
        for iterator, _ in stack:
            iterator.close()
    return True


def _strip_acls(path: str, *, is_dir: bool) -> None:
    for name in (ACL_ACCESS, ACL_DEFAULT) if is_dir else (ACL_ACCESS,):
        try:
            os.removexattr(path, name, follow_symlinks=False)
        except OSError as error:
            if error.errno not in (errno.ENODATA, errno.EOPNOTSUPP):
                raise


def _hand(path: str, uid: int, gid: int) -> bool:
    """Give one entry to ``uid:gid``; return whether it is a real directory."""
    st = os.lstat(path)
    os.chown(path, uid, gid, follow_symlinks=False)
    if stat.S_ISLNK(st.st_mode):
        return False
    is_dir = stat.S_ISDIR(st.st_mode)
    _strip_acls(path, is_dir=is_dir)
    os.chmod(path, fixed_mode(st.st_mode, is_dir=is_dir))
    return is_dir


def hand_over(root: str, uid: int, gid: int) -> None:
    """Hand ``root`` and everything under it to ``uid:gid`` (module docstring)."""
    _hand(root, uid, gid)
    pending = [root]
    while pending:
        directory = pending.pop()
        for name in sorted(os.listdir(directory)):
            path = os.path.join(directory, name)
            if _hand(path, uid, gid):
                pending.append(path)


def empty(root: str) -> None:
    """Delete everything under ``root``, keeping ``root``."""
    subprocess.run(["find", root, "-mindepth", "1", "-delete"], check=True)


def main(argv: list[str]) -> int:
    """Apply the limits, hand the tree over, print ``ok`` or ``emptied``."""
    try:
        owner, entries, path_bytes, root = argv
        uid, gid = (int(part) for part in owner.split(":"))
        max_entries, max_path_bytes = int(entries), int(path_bytes)
        if min(uid, gid, max_entries, max_path_bytes) < 0:
            raise ValueError(owner)
    except ValueError:
        print(_USAGE, file=sys.stderr)
        raise SystemExit(2) from None
    status = OK
    if not within_limits(root, max_entries, max_path_bytes):
        empty(root)
        status = EMPTIED
    hand_over(root, uid, gid)
    print(status)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
