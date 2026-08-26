"""Where this checkout lives on disk.

Domain-independent, and deliberately outside ``core``: it performs I/O, which
``core`` may not. It imports nothing from ``environments``, so invariant 1 holds.

The one thing this module knows that a caller cannot work out for itself is
that **a worktree is not the tree its derived artefacts belong to**. Every
concurrent session gets its own worktree under ``.claude/worktrees/``, and an
artefact keyed on content -- an empirical table, a byte-frozen data snapshot --
is identical in all of them. Resolving to the main tree is what lets a new
worktree start warm instead of rebuilding or re-downloading what a sibling
already has.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

__all__ = ["data_root", "repo_root"]


def repo_root() -> Path:
    """Return the main worktree's root, falling back to this tree's own.

    Guarantees an absolute, resolved path, and never raises: a caller with no
    git, no repository, or a tree that does not look like this project gets
    this checkout's root rather than an exception.

    The main tree is found through ``git rev-parse --git-common-dir``, which is
    the one participant that knows: a worktree's ``.git`` is a file pointing
    into the main repository, and the common dir's parent is the main worktree.
    An earlier attempt used an environment variable set in
    ``.claude/settings.local.json``; that file is untracked, so no worktree
    checkout could ever contain it and every worktree silently took the cold
    path. Asking git needs no configuration and has nothing to forget.
    """
    here = Path(__file__).resolve().parents[2]
    try:
        common = subprocess.run(
            ("git", "rev-parse", "--git-common-dir"),
            cwd=here,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return here
    if not common:
        return here
    # `here / common` yields `common` unchanged when it is absolute, which is the
    # worktree case; in the main tree git answers the relative `.git`.
    root = (here / common).resolve().parent
    # Landmark check: git answers about whatever repository encloses this
    # directory. A sciagent tree vendored inside another repo -- or one whose own
    # `.git` is missing -- would otherwise resolve to the *outer* repository's
    # root, silently and nowhere near the tree it belongs to.
    if not (root / "pyproject.toml").is_file() or not (root / "src").is_dir():
        return here
    return root


def data_root(name: str, override_var: str) -> Path:
    """Return the shared directory holding external data set ``name``.

    Guarantees an absolute path under the main worktree, so every worktree of
    this repository resolves to one copy. ``override_var`` names an environment
    variable that, when set and non-empty, replaces the whole answer -- for a
    caller who keeps a large snapshot outside the checkout entirely.

    Nothing is created here. A caller that needs the directory to exist says so
    itself, because a *reader* wants a missing directory to stay missing rather
    than to be conjured empty and then reported as an absent snapshot.
    """
    override = os.environ.get(override_var)
    if override:
        return Path(override).expanduser().resolve()
    return repo_root() / "data" / name
