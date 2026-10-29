"""The gate and the D push of the at-stop-rule controls (criteria.md v8 §5, §7).

Kept apart from controls.py so the procedure can be read and tested on its
own. Interface used by controls.py:

- ``Paths`` (where $R, $D, $WT and the origin URL are), ``GateError`` (exit 10);
- ``gate(paths, phase, control, freeze, d0_commit, env, ...) -> GateResult``:
  the fetch into the private ref under ``git.lock``, then every check at O;
- ``push_d0(...)``: calibrate's D build and push (plumbing, temporary index);
- ``check_env`` and ``check_runtime``: the runtime of §7;
- ``GateToken``: built only by ``checks``; the draw functions require it for
  the real control ids;
- ``GateResult.status`` and ``GateResult.record(...)``: what D0 and the score
  files record.

Every git call is ``git -C <repo>`` with captured output; every gh call goes
through an injectable reader (``GhReader``) so the tests never reach GitHub.
"""

from __future__ import annotations

import csv
import fcntl
import hashlib
import itertools
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from fnmatch import fnmatchcase
from importlib import metadata
from pathlib import Path
from typing import Any, Final

PHASES: Final = ("calibrate", "test", "score")
CONTROL_IDS: Final = ("ctlA1", "ctlB")
URL: Final = "https://github.com/ebinezer-rajaram/sciagent.git"
PRIVATE_REF: Final = "refs/fsd-controls/v2"
LEDGER_PATH: Final = "docs/v2/LEDGER.csv"
GH_REPO: Final = "repos/ebinezer-rajaram/sciagent"
H1_ARGS: Final = (
    "api",
    f"{GH_REPO}/branches/v2/protection",
    "--jq",
    "[.allow_force_pushes.enabled,.allow_deletions.enabled,.enforce_admins.enabled]",
)
H1_EXPECTED: Final = "[false,false,true]"
H2_KINDS: Final = ("force_push", "branch_deletion")
GIT_TIMEOUT_S: Final = 120.0
GH_TIMEOUT_S: Final = 60.0
PUSH_ATTEMPTS: Final = 20
PUSH_WAIT_S: Final = 30.0
BLAS_VARS: Final = ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS")
#: Run outputs (criteria §5 Sequence 5), relative to $D.
RUN_OUTPUT_DIRS: Final = ("__pycache__/", ".ruff_cache/")
RUN_OUTPUTS: Final = (
    "*-err.txt",
    "cpu-total.txt",
    "cpu-total.txt.tmp",
    "cpu-total.lock",
    "cpu-ledger.csv",
    "git.lock",
    "STOP",
    "D0-*.json",
    "runs-*.jsonl",
    "score-*.*",
)
FREEZE_RE: Final = re.compile(r"fsd-controls freeze ([0-9a-f]{40})")
CEILING_RE: Final = re.compile(
    r"fsd-controls (?:freeze [0-9a-f]{40} )?ceiling ([0-9]+(?:\.[0-9]+)?) CPU h"
)
SHA1_RE: Final = re.compile(r"[0-9a-f]{40}")


class GateError(Exception):
    """A gate check failed (exit 10). ``check`` names it."""

    def __init__(self, check: str, detail: str) -> None:
        super().__init__(f"gate refused [{check}]: {detail}")
        self.check = check


_TOKEN_KEY: Final = object()


class GateToken:
    """Proof that the gate passed at O. Built only by :func:`checks`."""

    __slots__ = ("o",)

    def __init__(self, o: str, key: object) -> None:
        if key is not _TOKEN_KEY:
            raise GateError("token", "a GateToken is built by the gate only")
        self.o = o


type GhReader = Callable[[Sequence[str]], str]


def gh_cli(args: Sequence[str]) -> str:
    """``gh <args>``; any failure or timeout refuses."""
    try:
        done = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=GH_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GateError("gh", f"gh {' '.join(args)}: {error}") from error
    if done.returncode != 0:
        raise GateError("gh", f"gh {' '.join(args)}: {done.stderr.strip()}")
    return done.stdout.strip()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def file_sha(path: Path, check: str) -> str:
    """sha256 of a file the gate needs; a missing or unreadable one refuses."""
    try:
        return sha256_file(path)
    except OSError as error:
        raise GateError(check, f"{path}: {error}") from error


def git_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment of every gate git call: no inherited GIT_* variable
    (GIT_DIR, GIT_INDEX_FILE, ...), replace refs ignored, plus ``extra``."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    env.update(extra or {})
    return env


@dataclass(frozen=True)
class Paths:
    repo: Path
    drafts_rel: str
    worktree: Path
    worktree_head: str
    url: str = URL

    @property
    def drafts(self) -> Path:
        return self.repo / self.drafts_rel

    def d0_rel(self, control: str) -> str:
        return f"{self.drafts_rel}/D0-{control}.json"


# --------------------------------------------------------------------------
# git
# --------------------------------------------------------------------------


def git(
    repo: Path,
    *args: str,
    extra: Mapping[str, str] | None = None,
    stdin: str | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            check=False,
            env=git_env(extra),
            input=stdin,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise GateError("timeout", f"git {' '.join(args)}: {error}") from error
    except OSError as error:
        raise GateError("git", f"git {' '.join(args)}: {error}") from error


def git_out(repo: Path, check: str, *args: str, **kwargs: Any) -> str:
    done = git(repo, *args, **kwargs)
    if done.returncode != 0:
        raise GateError(check, f"git {' '.join(args)}: {done.stderr.strip()}")
    return done.stdout


def is_ancestor(repo: Path, a: str, b: str) -> bool:
    done = git(repo, "merge-base", "--is-ancestor", a, b)
    if done.returncode not in (0, 1):
        raise GateError("ancestry", f"merge-base {a} {b}: {done.stderr.strip()}")
    return done.returncode == 0


@contextmanager
def git_lock(paths: Paths) -> Iterator[None]:
    """Exclusive flock on $D/git.lock (both controls.py processes take it)."""
    with (paths.drafts / "git.lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def fetch(paths: Paths) -> str:
    """Fetch v2 into the private ref (call under git_lock); return O."""
    git_out(
        paths.repo,
        "fetch",
        "fetch",
        "--no-tags",
        "--no-write-fetch-head",
        paths.url,
        f"+refs/heads/v2:{PRIVATE_REF}",
        timeout=GIT_TIMEOUT_S,
    )
    o = git_out(
        paths.repo, "fetch", "rev-parse", "--verify", f"{PRIVATE_REF}^{{commit}}"
    ).strip()
    if not SHA1_RE.fullmatch(o):
        raise GateError("fetch", f"O is {o!r}")
    return o


# --------------------------------------------------------------------------
# Environment and runtime (§7)
# --------------------------------------------------------------------------


def check_env(env: Mapping[str, str], repo: Path) -> None:
    for var in BLAS_VARS:
        if env.get(var) != "1":
            raise GateError("env", f"{var} must be 1, is {env.get(var)!r}")
    prefix = env.get("PYTHONPYCACHEPREFIX")
    if not prefix:
        raise GateError("env", "PYTHONPYCACHEPREFIX is not set")
    p = Path(prefix)
    if not p.is_absolute() or not p.is_dir():
        raise GateError("env", f"PYTHONPYCACHEPREFIX {prefix!r} is not a directory")
    if p.resolve().is_relative_to(repo.resolve()):
        raise GateError("env", f"PYTHONPYCACHEPREFIX {prefix!r} is inside {repo}")


def check_runtime(env: Mapping[str, str], script: Path, paths: Paths) -> None:
    """Isolated mode, the bytecode settings, the script, the imports."""
    check_env(env, paths.repo)
    if not sys.flags.isolated:
        raise GateError("runtime", "run with python -I")
    if not sys.dont_write_bytecode:
        raise GateError("runtime", "sys.dont_write_bytecode is not set")
    if sys.pycache_prefix != env.get("PYTHONPYCACHEPREFIX"):
        raise GateError("runtime", "sys.pycache_prefix is not PYTHONPYCACHEPREFIX")
    if script.resolve() != (paths.drafts / "controls.py").resolve():
        raise GateError("runtime", f"{script} is not {paths.drafts / 'controls.py'}")
    check_imports(paths.worktree)


def check_imports(worktree: Path) -> None:
    """Every sciagent/environments module comes from $WT/src (§0)."""
    import environments
    import sciagent

    src = (worktree / "src").resolve()
    for mod in (sciagent, environments):
        if mod.__file__ is None or not Path(mod.__file__).resolve().is_relative_to(src):
            raise GateError("imports", f"{mod.__name__} from {mod.__file__}")
    for name, mod in sorted(sys.modules.items()):
        if name.split(".")[0] not in ("sciagent", "environments"):
            continue
        f = getattr(mod, "__file__", None)
        if f is None or not Path(f).resolve().is_relative_to(src):
            raise GateError("imports", f"{name} from {f}")


def lib_versions() -> tuple[str, str]:
    return metadata.version("numpy"), metadata.version("scipy")


# --------------------------------------------------------------------------
# History, rows, chain (§5)
# --------------------------------------------------------------------------


def check_history(gh: GhReader) -> tuple[str, tuple[str, str]]:
    """H1 (protection) and H2 (no force push, no deletion in the activity log)."""
    h1 = gh(H1_ARGS).strip()
    if h1 != H1_EXPECTED:
        raise GateError("H1", f"v2 protection is {h1!r}, not {H1_EXPECTED}")
    counts: list[str] = []
    for kind in H2_KINDS:
        path = f"{GH_REPO}/activity?ref=refs/heads/v2&activity_type={kind}&per_page=100"
        n = gh(("api", path, "--jq", "length")).strip()
        if n != "0":
            raise GateError("H2", f"{kind}: {n!r} activities, not 0")
        counts.append(n)
    return h1, (counts[0], counts[1])


def rows_ever_added(repo: Path, o: str) -> list[str]:
    """The items of every LEDGER row ever added on O's history."""
    out = git_out(
        repo,
        "rows",
        "log",
        "--full-history",
        "--no-merges",
        "-p",
        "--format=",
        o,
        "--",
        LEDGER_PATH,
    )
    items: list[str] = []
    for line in out.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        fields = next(csv.reader([line[1:]]), [])
        if len(fields) >= 2:
            items.append(fields[1])
    return items


def ceiling_hours(items: Sequence[str]) -> float:
    """The largest c over ceiling rows (§6); none refuses."""
    found = [float(m.group(1)) for x in items if (m := CEILING_RE.fullmatch(x.strip()))]
    if not found:
        raise GateError("ceiling", "no fsd-controls ceiling row on O's history")
    return max(found)


def freeze_chain(
    repo: Path, o: str, drafts_rel: str, items: Sequence[str]
) -> tuple[tuple[str, ...], dict[str, list[str]], list[str]]:
    """C1-C3: the chain, each freeze's rows, and rows naming other shas."""
    chain = git_out(
        repo,
        "chain",
        "rev-list",
        "--full-history",
        "--no-merges",
        "--topo-order",
        "--reverse",
        o,
        "--",
        f"{drafts_rel}/controls.py",
    ).split()
    if not chain:
        raise GateError("C1", "no commit on O ever changed controls.py")
    for a, b in itertools.pairwise(chain):
        if not is_ancestor(repo, a, b):
            raise GateError("C2", f"freeze {a} is not an ancestor of {b}")
    rows: dict[str, list[str]] = {f: [] for f in chain}
    other: list[str] = []
    for item in items:
        named = FREEZE_RE.findall(item)
        if not named and "fsd-controls" in item:
            other.append(item)
        for sha in named:
            if sha in rows:
                rows[sha].append(item)
            else:
                other.append(item)
    for f in chain:
        if not rows[f]:
            raise GateError("C3", f"freeze {f} is named by no LEDGER row")
    return tuple(chain), rows, other


def message_sha(message: str, name: str) -> str:
    found = re.findall(
        rf"^{re.escape(name)} sha256: ([0-9a-f]{{64}})\s*$", message, re.M
    )
    if len(found) != 1:
        raise GateError("C5", f"F's message needs one '{name} sha256: <hex>' line")
    return str(found[0])


def is_run_output(rel: str) -> bool:
    if any(rel == d or rel.endswith("/" + d) for d in RUN_OUTPUT_DIRS):
        return True
    if any(f"/{d}" in "/" + rel for d in RUN_OUTPUT_DIRS):
        return True
    return "/" not in rel and any(fnmatchcase(rel, p) for p in RUN_OUTPUTS)


def check_status(paths: Paths) -> None:
    """C8: only ignored run outputs under $P."""
    out = git_out(
        paths.repo, "C8", "status", "--porcelain", "--ignored", "--", paths.drafts_rel
    )
    prefix = paths.drafts_rel + "/"
    for line in out.splitlines():
        path = line[3:]
        if not line.startswith("!! ") or not path.startswith(prefix):
            raise GateError("C8", f"not an ignored run output: {line!r}")
        if not is_run_output(path[len(prefix) :]):
            raise GateError("C8", f"not a run output: {line!r}")


def check_worktree(paths: Paths) -> None:
    """C9: $WT's HEAD and a clean src (ignored __pycache__/ only)."""
    head = git_out(paths.worktree, "C9", "rev-parse", "HEAD").strip()
    if head != paths.worktree_head:
        raise GateError("C9", f"$WT HEAD is {head}, not {paths.worktree_head}")
    out = git_out(
        paths.worktree, "C9", "status", "--porcelain", "--ignored", "--", "src"
    )
    for line in out.splitlines():
        if not (line.startswith("!! ") and line.endswith("__pycache__/")):
            raise GateError("C9", f"$WT src is not clean: {line!r}")


def d0_history(repo: Path, o: str, f: str, rel: str) -> list[tuple[str, bool]]:
    """Every X touching D0-<C>.json on O, with whether it descends from F."""
    xs = git_out(repo, "status", "rev-list", "--full-history", o, "--", rel).split()
    return [(x, is_ancestor(repo, f, x)) for x in xs]


def d_candidates(repo: Path, o: str, f: str, rel: str) -> list[str]:
    """Non-merge commits on O touching D0-<C>.json, under F, holding the file."""
    xs = git_out(
        repo, "D", "rev-list", "--full-history", "--no-merges", o, "--", rel
    ).split()
    return [
        x
        for x in xs
        if is_ancestor(repo, f, x)
        and git(repo, "cat-file", "-e", f"{x}:{rel}").returncode == 0
    ]


def blob_bytes(repo: Path, spec: str) -> bytes:
    try:
        done = subprocess.run(
            ["git", "-C", str(repo), "cat-file", "blob", spec],
            capture_output=True,
            check=False,
            env=git_env(),
            timeout=GIT_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as error:
        raise GateError("timeout", f"cat-file {spec}") from error
    except OSError as error:
        raise GateError("git", f"cat-file {spec}: {error}") from error
    if done.returncode != 0:
        raise GateError("D", f"cat-file blob {spec}: {done.stderr.decode().strip()}")
    return done.stdout


def d0_first_line(control: str, f: str) -> str:
    return f"fsd-controls D0 {control} under {f}"


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class GateResult:
    freeze: str
    o: str
    chain: tuple[str, ...]
    chain_rows: tuple[tuple[str, tuple[str, ...]], ...]
    other_rows: tuple[str, ...]
    h1: str
    h2: tuple[str, str]
    d0_history: tuple[tuple[str, bool], ...]
    d0_commit: str | None
    ceiling_h: float
    criteria_sha256: str
    controls_sha256: str
    uv_lock_sha256: str
    worktree_head: str
    numpy: str
    scipy: str
    token: GateToken

    @property
    def exploratory(self) -> bool:
        return any(not descends for _, descends in self.d0_history)

    @property
    def status(self) -> str:
        return "exploratory deviation" if self.exploratory else "preregistered"

    def record(self, *, score: bool) -> dict[str, Any]:
        """D0's records (no O, no D, no time); the score files add O and D."""
        out: dict[str, Any] = {
            "freeze_commit": self.freeze,
            "freeze_chain": [
                {"freeze": f, "rows": list(r)} for f, r in self.chain_rows
            ],
            "other_rows": list(self.other_rows),
            "h1": self.h1,
            "h2": {"force_push": self.h2[0], "branch_deletion": self.h2[1]},
            "status_commits": [
                {"commit": x, "descends_from_F": d} for x, d in self.d0_history
            ],
            "criteria_sha256": self.criteria_sha256,
            "controls_sha256": self.controls_sha256,
            "uv_lock_sha256": self.uv_lock_sha256,
            "worktree_head": self.worktree_head,
            "numpy": self.numpy,
            "scipy": self.scipy,
        }
        if score:
            out["o"] = self.o
            out["d0_commit"] = self.d0_commit
        return out


def checks(
    paths: Paths,
    phase: str,
    control: str,
    freeze: str,
    d0_commit: str | None,
    env: Mapping[str, str],
    o: str,
    *,
    gh: GhReader,
    criteria_constant: str,
) -> GateResult:
    """Every check of §5 at O (the fetch is the caller's, under git_lock)."""
    repo = paths.repo
    if phase not in PHASES or control not in CONTROL_IDS:
        raise GateError("args", f"phase {phase!r}, control {control!r}")
    if (phase == "calibrate") != (d0_commit is None):
        raise GateError("args", "--d0-commit is given for test and score only")
    if not SHA1_RE.fullmatch(freeze):
        raise GateError("args", f"--freeze-commit must be a full sha, got {freeze!r}")
    check_env(env, repo)
    h1, h2 = check_history(gh)
    items = rows_ever_added(repo, o)
    chain, rows, other = freeze_chain(repo, o, paths.drafts_rel, items)
    if chain[-1] != freeze:
        raise GateError("C4", f"F is not the newest freeze ({chain[-1]})")
    f = freeze
    message = git_out(repo, "C5", "show", "-s", "--format=%B", f)
    criteria_sha = file_sha(paths.drafts / "criteria.md", "C5")
    controls_sha = file_sha(paths.drafts / "controls.py", "C5")
    if message_sha(message, "criteria.md") != criteria_sha:
        raise GateError("C5", "criteria.md on disk differs from F's message")
    if message_sha(message, "controls.py") != controls_sha:
        raise GateError("C5", "controls.py on disk differs from F's message")
    if criteria_sha != criteria_constant:
        raise GateError("C5", "criteria.md differs from controls.py's CRITERIA_SHA256")
    frozen = git_out(
        repo, "C6", "ls-tree", "-r", "--name-only", f, "--", paths.drafts_rel
    ).split()
    prefix = paths.drafts_rel + "/"
    for path in frozen:
        if not path.startswith(prefix) or is_run_output(path[len(prefix) :]):
            raise GateError("C6", f"F's tree holds a run output: {path}")
    for need in ("controls.py", "criteria.md"):
        if prefix + need not in frozen:
            raise GateError("C6", f"F does not hold {need}")
    for against in ((o,), ("HEAD",), ()):
        if git(repo, "diff", "--quiet", f, *against, "--", *frozen).returncode != 0:
            where = against[0] if against else "the working tree"
            raise GateError("C7", f"a frozen file differs between F and {where}")
    check_status(paths)
    check_worktree(paths)
    rel = paths.d0_rel(control)
    history = d0_history(repo, o, f, rel)
    candidates = d_candidates(repo, o, f, rel)
    uv_lock = file_sha(paths.worktree / "uv.lock", "C9")
    numpy_v, scipy_v = lib_versions()
    if phase == "calibrate":
        if candidates:
            raise GateError(
                "D-exists",
                f"a D under F exists: {' '.join(candidates)}; restore with "
                f"git -C {repo} show {candidates[0]}:{rel} "
                f"> {paths.drafts}/D0-{control}.json",
            )
    else:
        assert d0_commit is not None
        check_d(paths, control, f, d0_commit, candidates)
        check_d0_record(
            paths, control, f, criteria_sha, controls_sha, uv_lock, numpy_v, scipy_v
        )
    return GateResult(
        freeze=f,
        o=o,
        chain=chain,
        chain_rows=tuple((x, tuple(rows[x])) for x in chain),
        other_rows=tuple(other),
        h1=h1,
        h2=h2,
        d0_history=tuple(history),
        d0_commit=d0_commit,
        ceiling_h=ceiling_hours(items),
        criteria_sha256=criteria_sha,
        controls_sha256=controls_sha,
        uv_lock_sha256=uv_lock,
        worktree_head=paths.worktree_head,
        numpy=numpy_v,
        scipy=scipy_v,
        token=GateToken(o, _TOKEN_KEY),
    )


def check_d(
    paths: Paths, control: str, f: str, d: str, candidates: Sequence[str]
) -> None:
    repo = paths.repo
    rel = paths.d0_rel(control)
    if not SHA1_RE.fullmatch(d):
        raise GateError("args", f"--d0-commit must be a full sha, got {d!r}")
    if list(candidates) != [d]:
        raise GateError(
            "D", f"the D-candidates under F are {list(candidates)}, not [{d}]"
        )
    parents = git_out(repo, "D", "rev-list", "--parents", "-n", "1", d).split()
    if len(parents) != 2:
        raise GateError("D", f"D has {len(parents) - 1} parents")
    names = git_out(repo, "D", "show", "--name-only", "--format=", d).split()
    if names != [rel]:
        raise GateError("D", f"D must hold exactly {rel}, holds {names}")
    first = git_out(repo, "D", "log", "-1", "--format=%s", d).strip()
    if first != d0_first_line(control, f):
        raise GateError("D", f"D's first line is {first!r}")
    disk = paths.repo / rel
    if not disk.is_file():
        raise GateError("D", f"{disk} is missing")
    if (
        hashlib.sha256(disk.read_bytes()).hexdigest()
        != hashlib.sha256(blob_bytes(repo, f"{d}:{rel}")).hexdigest()
    ):
        raise GateError("D", f"{disk} differs from D's blob")


def check_d0_record(
    paths: Paths,
    control: str,
    f: str,
    criteria_sha: str,
    controls_sha: str,
    uv_lock: str,
    numpy_v: str,
    scipy_v: str,
) -> None:
    rel = paths.d0_rel(control)
    try:
        rec = json.loads((paths.repo / rel).read_text(encoding="ascii"))["records"]
        recorded = (
            rec["freeze_commit"],
            rec["criteria_sha256"],
            rec["controls_sha256"],
            rec["uv_lock_sha256"],
            rec["numpy"],
            rec["scipy"],
        )
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise GateError("D", f"{rel} is unreadable: {error}") from error
    now = (f, criteria_sha, controls_sha, uv_lock, numpy_v, scipy_v)
    if recorded != now:
        raise GateError("environment", f"D0 records {recorded}, now {now}")


def gate(
    paths: Paths,
    phase: str,
    control: str,
    freeze: str,
    d0_commit: str | None,
    env: Mapping[str, str],
    *,
    gh: GhReader = gh_cli,
    criteria_constant: str,
) -> GateResult:
    """Fetch O under git.lock, then every check at O."""
    with git_lock(paths):
        o = fetch(paths)
    return checks(
        paths,
        phase,
        control,
        freeze,
        d0_commit,
        env,
        o,
        gh=gh,
        criteria_constant=criteria_constant,
    )


# --------------------------------------------------------------------------
# D: build and push (calibrate; §5 "D")
# --------------------------------------------------------------------------


def objects_dir(repo: Path) -> Path:
    p = Path(git_out(repo, "D-build", "rev-parse", "--git-path", "objects").strip())
    return (p if p.is_absolute() else repo / p).resolve()


def build_and_push(
    paths: Paths,
    control: str,
    f: str,
    o: str,
    data: bytes,
    before_push: Callable[[], None] | None = None,
) -> str:
    """Build D = O plus D0-<C>.json and push it, without touching HEAD, the
    index, the working tree or $R/.git/objects.

    The index and every new object (D0's blob, the tree, D) live in a temporary
    directory outside $R; $R's objects are read through alternates. D0's bytes
    reach $R only when the step-4 fetch brings D back from v2 (s18b 1).
    """
    repo = paths.repo
    rel = paths.d0_rel(control)
    with tempfile.TemporaryDirectory(prefix="fsd-d0-") as tmp:
        t = Path(tmp)
        if t.resolve().is_relative_to(repo.resolve()):
            raise GateError("D-build", f"temporary directory {tmp} is inside {repo}")
        (t / "objects").mkdir()
        extra = {
            "GIT_INDEX_FILE": str(t / "index"),
            "GIT_OBJECT_DIRECTORY": str(t / "objects"),
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(objects_dir(repo)),
        }
        git_out(repo, "D-build", "read-tree", o, extra=extra)
        blob = git_out(
            repo,
            "D-build",
            "hash-object",
            "-w",
            "--stdin",
            extra=extra,
            stdin=data.decode("ascii"),
        ).strip()
        git_out(
            repo,
            "D-build",
            "update-index",
            "--add",
            "--cacheinfo",
            f"100644,{blob},{rel}",
            extra=extra,
        )
        tree = git_out(repo, "D-build", "write-tree", extra=extra).strip()
        h = hashlib.sha256(data).hexdigest()
        message = f"{d0_first_line(control, f)}\n\nD0-{control}.json sha256: {h}\n"
        d = git_out(
            repo,
            "D-build",
            "commit-tree",
            tree,
            "-p",
            o,
            "-F",
            "-",
            extra=extra,
            stdin=message,
        ).strip()
        if before_push is not None:
            before_push()
        try_push(paths, d, extra)  # a rejection is normal; step 4 decides
    return d


def try_push(paths: Paths, d: str, extra: Mapping[str, str]) -> bool:
    """``git push URL D:refs/heads/v2``, plain (never ``+``, never --force).

    A rejection or a timeout is not an error here: the refetch decides whether
    D landed, which also covers a push whose reply was lost. An OSError
    (no git) refuses.
    """
    try:
        done = subprocess.run(
            ["git", "-C", str(paths.repo), "push", paths.url, f"{d}:refs/heads/v2"],
            capture_output=True,
            text=True,
            check=False,
            env=git_env(extra),
            timeout=GIT_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return False
    except OSError as error:
        raise GateError("push", f"git push: {error}") from error
    return done.returncode == 0


def landed(repo: Path, d: str, o: str) -> bool:
    """D is on v2: its objects arrived with the fetch and it is an ancestor of O."""
    if git(repo, "cat-file", "-e", f"{d}^{{commit}}").returncode != 0:
        return False
    return is_ancestor(repo, d, o)


def push_d0(
    paths: Paths,
    control: str,
    freeze: str,
    env: Mapping[str, str],
    make_bytes: Callable[[GateResult], bytes],
    *,
    gh: GhReader = gh_cli,
    criteria_constant: str,
    sleep: Callable[[float], None] = time.sleep,
    before_push: Callable[[int], None] | None = None,
) -> tuple[str, bytes, GateResult]:
    """Steps 1-5 of §5 "D", at most PUSH_ATTEMPTS times; returns (D, bytes, gate).

    ``make_bytes`` renders D0 from the gate result of the attempt. Nothing is
    written to $D here. ``before_push`` is a test hook (a concurrent pusher).
    """
    for attempt in range(PUSH_ATTEMPTS):
        with git_lock(paths):
            o = fetch(paths)
            result = checks(
                paths,
                "calibrate",
                control,
                freeze,
                None,
                env,
                o,
                gh=gh,
                criteria_constant=criteria_constant,
            )
            data = make_bytes(result)
            hook = None
            if before_push is not None:

                def hook(attempt: int = attempt) -> None:
                    assert before_push is not None
                    before_push(attempt)

            d = build_and_push(paths, control, freeze, o, data, hook)
            o2 = fetch(paths)
            if landed(paths.repo, d, o2):
                return d, data, result
        sleep(PUSH_WAIT_S)
    raise GateError("push", f"D was not on v2 after {PUSH_ATTEMPTS} attempts")
