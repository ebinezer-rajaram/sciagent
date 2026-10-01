"""SPEC §6.3 no. 8: sandbox isolation, tested with adversarial snippets.

Every test here runs real containers, so every test is ``slow`` and requests
the ``sandbox_image`` fixture, which fails (never skips) without Docker.

The adversarial snippets print one line per probe, ``BLOCKED <probe>`` or
``LEAK <probe> <detail>``, and the assertions read those lines. A probe that
reports neither is a test failure too: a snippet that crashed before reaching
a probe has proved nothing about it.
"""

from __future__ import annotations

import dataclasses
import os
import subprocess
import textwrap
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from sciagent.sandbox import (
    TIMEOUT_EXIT_CODE,
    Sandbox,
    SandboxLimits,
    SandboxResult,
    result_digest,
    write_dataset,
)

pytestmark = pytest.mark.slow

REPO = Path(__file__).resolve().parents[2]


def _run(sb: Sandbox, code: str) -> SandboxResult:
    return sb.run(textwrap.dedent(code))


def _probes(r: SandboxResult, names: list[str]) -> None:
    """Assert every named probe ran and reported BLOCKED."""
    lines = r.stdout.splitlines()
    leaks = [ln for ln in lines if ln.startswith("LEAK")]
    assert not leaks, f"isolation breach:\n{r.stdout}\n{r.stderr}"
    for n in names:
        assert f"BLOCKED {n}" in lines, (
            f"probe {n} did not report:\n{r.stdout}\n{r.stderr}"
        )


def _leftover(name: str) -> str:
    return subprocess.run(
        ["docker", "ps", "-a", "--filter", f"name={name}", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _with(sb: Sandbox, **limits: Any) -> Sandbox:
    return Sandbox(
        sb.run_dir,
        image=sb.image,
        limits=dataclasses.replace(sb.limits, **limits),
        seed=sb.seed,
    )


# --------------------------------------------------------------------------
# Basic behaviour
# --------------------------------------------------------------------------


def test_trivial_call_and_libraries(sandbox: Sandbox) -> None:
    r = _run(
        sandbox,
        """
        import numpy, scipy, pandas, statsmodels
        print(numpy.__version__, scipy.__version__)
        print("μ ok")
        """,
    )
    assert r.exit_code == 0, r.stderr
    assert r.stdout.splitlines() == [f"{np.__version__} 1.18.0", "μ ok"]
    assert not r.timed_out and not r.truncated


def test_nonzero_exit_and_syntax_error_are_captured(sandbox: Sandbox) -> None:
    r = _run(sandbox, "import sys\nprint('before')\nsys.exit(3)\n")
    assert (r.exit_code, r.stdout) == (3, "before\n")
    # 125-127 are Docker's own failure codes; from the snippet they are results.
    r = _run(sandbox, "import sys\nsys.exit(125)\n")
    assert r.exit_code == 125
    r = _run(sandbox, "def (:\n")
    assert r.exit_code == 1
    assert "SyntaxError" in r.stderr
    r = _run(sandbox, "raise KeyError('k')\n")
    assert r.exit_code == 1 and "KeyError" in r.stderr


def test_output_is_truncated_with_a_marker(sandbox: Sandbox) -> None:
    sb = _with(sandbox, output_bytes=1000)
    r = _run(sb, "import sys\nprint('x' * 100_000)\nsys.stderr.write('e' * 5000)\n")
    assert r.exit_code == 0
    assert r.truncated
    assert r.stdout.startswith("x" * 1000)
    assert "x" * 1001 not in r.stdout
    assert "[sciagent sandbox: stdout truncated at 1000 bytes]" in r.stdout
    assert "[sciagent sandbox: stderr truncated at 1000 bytes]" in r.stderr
    assert len(r.stdout) < 1200


def test_state_persists_only_through_work(sandbox: Sandbox, run_dir: Path) -> None:
    r = _run(sandbox, "import numpy as np\nnp.save('state.npy', np.arange(3))\nX = 1\n")
    assert r.exit_code == 0, r.stderr
    assert (run_dir / "work" / "state.npy").is_file()
    r = _run(
        sandbox,
        """
        import numpy as np
        print(np.load('/work/state.npy').tolist())
        print('X' in globals())
        """,
    )
    assert r.stdout == "[0, 1, 2]\nFalse\n", r.stderr


def test_dataset_is_readable_and_path_with_space_works(
    sandbox_image: str, tmp_path: Path
) -> None:
    run_dir = tmp_path / "run with space"
    run_dir.mkdir()
    write_dataset(run_dir, "obs", np.array([0.5, 1.5]), {"size": np.array([2.0, 3.0])})
    sb = Sandbox(run_dir, image=sandbox_image, limits=SandboxLimits(), seed=1)
    r = _run(
        sb,
        """
        import numpy as np, pandas as pd
        z = np.load('/data/obs.npz')
        print(z['times'].tolist(), z['mark_size'].tolist())
        print(pd.read_csv('/data/obs.csv').to_dict('list'))
        """,
    )
    assert r.exit_code == 0, r.stderr
    assert r.stdout == (
        "[0.5, 1.5] [2.0, 3.0]\n{'time': [0.5, 1.5], 'size': [2.0, 3.0]}\n"
    )


# --------------------------------------------------------------------------
# Determinism (SPEC §4.0 "Determinism with a sandbox"; §6.3 no. 9)
# --------------------------------------------------------------------------

_SEEDED = """
import os
import numpy as np
from scipy import optimize, stats
rng = np.random.default_rng(int(os.environ["SANDBOX_SEED"]))
z = np.load("/data/obs.npz")
t = np.concatenate([z["times"], rng.exponential(1.0, 500).cumsum()])
gaps = np.diff(np.sort(t))
nll = lambda lam: -stats.expon.logpdf(gaps, scale=1.0 / lam[0]).sum()
fit = optimize.minimize(nll, x0=[0.5], method="Nelder-Mead")
A = rng.standard_normal((200, 200))
print(repr(fit.x[0]), repr(float(np.linalg.eigvalsh(A @ A.T).sum())))
print(hash("abc"), sorted({"b", "a", "c"}))
"""


def test_same_code_data_seed_gives_same_stdout(
    sandbox_image: str, tmp_path: Path
) -> None:
    def once(name: str, seed: int) -> SandboxResult:
        d = tmp_path / name
        d.mkdir()
        write_dataset(
            d, "obs", np.array([0.1, 0.7, 1.9]), {"m": np.array([1.0, 2.0, 3.0])}
        )
        sb = Sandbox(d, image=sandbox_image, limits=SandboxLimits(), seed=seed)
        r = sb.run(_SEEDED)
        assert r.exit_code == 0, r.stderr
        return r

    a, b = once("a", 42), once("b", 42)
    assert a.stdout == b.stdout
    assert result_digest(a) == result_digest(b)
    # Positive control: the seed is actually reaching the code.
    c = once("c", 43)
    assert c.stdout != a.stdout
    assert result_digest(c) != result_digest(a)


# --------------------------------------------------------------------------
# Network
# --------------------------------------------------------------------------


def test_network_is_unreachable(sandbox: Sandbox) -> None:
    r = _run(
        sandbox,
        """
        import os, socket, urllib.request
        def probe(name, fn):
            try:
                out = fn()
            except OSError as e:
                print("BLOCKED", name)
            else:
                print("LEAK", name, out)
        tcp = lambda host, port: socket.create_connection((host, port), timeout=3)
        get = lambda url: urllib.request.urlopen(url, timeout=3).status
        probe("tcp-1.1.1.1", lambda: tcp("1.1.1.1", 53))
        probe("tcp-host-gw", lambda: tcp("192.168.65.254", 80))
        probe("dns", lambda: socket.getaddrinfo("example.com", 80))
        probe("dns-host", lambda: socket.getaddrinfo("host.docker.internal", 80))
        probe("http", lambda: get("http://example.com"))
        probe("https", lambda: get("https://pypi.org"))
        ifaces = sorted(os.listdir("/sys/class/net"))
        print("BLOCKED interfaces" if ifaces == ["lo"] else f"LEAK interfaces {ifaces}")
        """,
    )
    _probes(
        r,
        [
            "tcp-1.1.1.1",
            "tcp-host-gw",
            "dns",
            "dns-host",
            "http",
            "https",
            "interfaces",
        ],
    )


# --------------------------------------------------------------------------
# Filesystem
# --------------------------------------------------------------------------


def test_host_filesystem_is_invisible(sandbox: Sandbox, run_dir: Path) -> None:
    home = Path.home()
    repo_posix = REPO.as_posix()  # e.g. C:/dev/products/sciagent
    drive_rel = repo_posix.split(":", 1)[-1]  # /dev/products/sciagent
    candidates = [
        "/mnt/c",
        "/mnt/host",
        "/mnt/host/c",
        "/run/desktop/mnt/host/c",
        "/host",
        "/host_mnt",
        "/c",
        "C:/",
        "C:\\",
        repo_posix,
        f"/mnt/c{drive_rel}",
        f"/run/desktop/mnt/host/c{drive_rel}",
        f"/c{drive_rel}",
        home.as_posix(),
        str(home),
        run_dir.as_posix(),
        "/var/run/docker.sock",
        "/run/docker.sock",
    ]
    r = _run(
        sandbox,
        f"""
        import os
        for i, p in enumerate({candidates!r}):
            if os.path.exists(p):
                listing = os.listdir(p) if os.path.isdir(p) else ""
                print("LEAK", f"path{{i}}", p, listing)
            else:
                print("BLOCKED", f"path{{i}}")
        top = sorted(os.listdir("/"))
        # .dockerenv: Docker's empty marker file.
        allowed = {{"bin", "boot", "data", "dev", "etc", "home", "lib", "lib64",
                   "media", "mnt", "opt", "proc", "root", "run", "sbin", "srv",
                   "sys", "tmp", "usr", "var", "work", ".dockerenv"}}
        extra = [x for x in top if x not in allowed]
        print("BLOCKED root-listing" if not extra else f"LEAK root-listing {{extra}}")
        print("BLOCKED mnt-empty" if os.listdir("/mnt") == [] else "LEAK mnt-empty")
        bad = []
        ok_targets = ("/", "/data", "/work", "/tmp", "/etc/hosts", "/etc/hostname",
                      "/etc/resolv.conf", "/usr/sbin/docker-init")
        for line in open("/proc/mounts"):
            target, fstype = line.split()[1], line.split()[2]
            if target.startswith(("/proc", "/sys", "/dev")) or target in ok_targets:
                continue
            bad.append(target)
        print("BLOCKED mounts" if not bad else f"LEAK mounts {{bad}}")
        """,
    )
    names = [f"path{i}" for i in range(len(candidates))]
    _probes(r, [*names, "root-listing", "mnt-empty", "mounts"])


def test_secrets_next_to_the_run_are_unreachable(
    sandbox: Sandbox, run_dir: Path
) -> None:
    """The truth, held-out data and scorer live beside the run dir, never in it."""
    token = "SCIAGENT-SECRET-7f3a9c"
    (run_dir / "truth.json").write_text(f'{{"truth": "{token}"}}', encoding="utf-8")
    (run_dir.parent / "heldout.csv").write_text(f"{token}\n", encoding="utf-8")
    (run_dir.parent / "scorer").mkdir()
    (run_dir.parent / "scorer" / "score.py").write_text(
        f"# {token}\n", encoding="utf-8"
    )
    r = _run(
        sandbox,
        f"""
        import os
        token = {token!r}
        hits = []
        for root, dirs, files in os.walk("/", onerror=lambda e: None):
            if root in ("/proc", "/sys", "/dev") or root.startswith(
                ("/proc/", "/sys/", "/dev/", "/usr/local/lib/python3.12/site-packages")
            ):
                dirs[:] = []
                continue
            for f in files:
                p = os.path.join(root, f)
                try:
                    if os.path.islink(p) or os.path.getsize(p) > 2_000_000:
                        continue
                    with open(p, "rb") as fh:
                        if token.encode() in fh.read():
                            hits.append(p)
                except OSError:
                    pass
        for p in ("/work/../truth.json", "/data/../truth.json",
                  "/work/../../heldout.csv", "../truth.json", "../heldout.csv",
                  "/truth.json"):
            try:
                open(p).read()
                hits.append(p)
            except OSError:
                pass
        print("BLOCKED secret" if not hits else f"LEAK secret {{hits}}")
        """,
    )
    _probes(r, ["secret"])


def test_writes_outside_work_fail(sandbox: Sandbox, run_dir: Path) -> None:
    write_dataset(run_dir, "obs", np.array([1.0]), {})
    r = _run(
        sandbox,
        """
        import os
        targets = ["/x", "/etc/passwd",
                   "/usr/local/lib/python3.12/site-packages/evil.py",
                   "/data/obs.csv", "/data/new.txt", "/root/x", "/var/tmp/x"]
        for i, p in enumerate(targets):
            try:
                with open(p, "a") as f:
                    f.write("pwned")
                print("LEAK", f"write{i}", p)
            except OSError:
                print("BLOCKED", f"write{i}")
        try:
            os.remove("/data/obs.npz")
            print("LEAK remove-data")
        except OSError:
            print("BLOCKED remove-data")
        open("/tmp/t.sh", "w").write("#!/bin/sh\\necho ran\\n")
        os.chmod("/tmp/t.sh", 0o755)
        try:
            import subprocess
            subprocess.run(["/tmp/t.sh"], check=True)
            print("LEAK tmp-exec")
        except (OSError, subprocess.CalledProcessError):
            print("BLOCKED tmp-exec")
        open("/work/ok.txt", "w").write("fine")
        print("BLOCKED done")
        """,
    )
    _probes(r, [*[f"write{i}" for i in range(7)], "remove-data", "tmp-exec", "done"])
    assert (run_dir / "work" / "ok.txt").read_text() == "fine"
    assert b"pwned" not in (run_dir / "data" / "obs.csv").read_bytes()
    assert not (run_dir / "data" / "new.txt").exists()


def test_symlink_out_of_work_does_not_reach_the_host(
    sandbox: Sandbox, run_dir: Path
) -> None:
    r = _run(
        sandbox,
        """
        import os
        targets = ["/run/desktop/mnt/host/c", "/mnt/host/c", "C:/", "/"]
        for i, target in enumerate(targets):
            link = f"/work/esc{i}"
            os.symlink(target, link)
            try:
                entries = os.listdir(link)
            except OSError:
                print("BLOCKED", f"link{i}")
            else:
                ok = target == "/" and "work" in entries  # the container's own root
                print(f"BLOCKED link{i}" if ok else f"LEAK link{i} {entries[:5]}")
        os.makedirs("/work/sub")
        os.symlink("/data", "/work/sub/data_link")
        os.mkfifo("/work/fifo")
        open("/work/sub/keep.txt", "w").write("kept")
        """,
    )
    _probes(r, [f"link{i}" for i in range(4)])
    # The host must never be handed a link or FIFO it could follow or block on:
    # every one is removed after the call, and the agent is told.
    removed = ["esc0", "esc1", "esc2", "esc3", "fifo", "sub/data_link"]
    for rel in removed:
        assert not os.path.lexists(run_dir / "work" / rel), rel
        assert f"[sciagent sandbox: removed non-regular file work/{rel}]" in r.stderr
    assert (run_dir / "work" / "sub" / "keep.txt").read_text() == "kept"
    # And the run directory stays walkable on the host (WinError 1920 otherwise).
    assert sorted(p.name for p in (run_dir / "work").rglob("*")) == ["keep.txt", "sub"]


def test_environment_leaks_nothing_from_the_host(sandbox: Sandbox) -> None:
    r = _run(
        sandbox,
        "import os, json\nprint(json.dumps(dict(os.environ), sort_keys=True))\n",
    )
    assert r.exit_code == 0, r.stderr
    import json

    env: dict[str, str] = json.loads(r.stdout)
    image_env = {
        "PATH",
        "LANG",
        "GPG_KEY",
        "PYTHON_VERSION",
        "PYTHON_SHA256",
        "HOSTNAME",
    }
    ours = {
        "SANDBOX_SEED",
        "PYTHONHASHSEED",
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONIOENCODING",
        "HOME",
        "TZ",
        "LC_ALL",
    }
    assert set(env) <= image_env | ours, sorted(set(env) - image_env - ours)
    for name in env:
        assert not name.startswith(("ANTHROPIC", "CLAUDE", "USERNAME", "USERPROFILE"))
    host_values = {
        v for k, v in os.environ.items() if len(v) >= 6 and k not in ("LANG",)
    }
    leaked = sorted(k for k, v in env.items() if v in host_values)
    assert not leaked, leaked
    assert env["HOSTNAME"] == "sandbox"
    assert env["SANDBOX_SEED"] == str(sandbox.seed)


# --------------------------------------------------------------------------
# Privilege
# --------------------------------------------------------------------------


def test_runs_unprivileged(sandbox: Sandbox) -> None:
    r = _run(
        sandbox,
        """
        import ctypes, os, subprocess
        root = os.getuid() == 0 or os.geteuid() == 0
        print("LEAK nonroot" if root else "BLOCKED nonroot")
        status = dict(l.split(":", 1) for l in open("/proc/self/status") if ":" in l)
        caps = [k for k in ("CapEff", "CapPrm", "CapBnd", "CapAmb")
                if int(status[k].strip(), 16) != 0]
        print("BLOCKED caps" if not caps else f"LEAK caps {caps}")
        print("BLOCKED nnp" if status["NoNewPrivs"].strip() == "1" else "LEAK nnp")
        try:
            os.setuid(0)
            print("LEAK setuid")
        except PermissionError:
            print("BLOCKED setuid")
        p = subprocess.run(["mount", "-t", "tmpfs", "none", "/mnt"],
                           capture_output=True)
        print("BLOCKED mount" if p.returncode != 0 else "LEAK mount")
        libc = ctypes.CDLL(None, use_errno=True)
        CLONE_NEWUSER, CLONE_NEWNS = 0x10000000, 0x00020000
        rc = libc.unshare(CLONE_NEWUSER | CLONE_NEWNS)
        print("BLOCKED unshare" if rc != 0 else "LEAK unshare")
        p = subprocess.run(["su", "-c", "id", "root"], capture_output=True, input=b"")
        print("BLOCKED su" if p.returncode != 0 else "LEAK su")
        """,
    )
    _probes(r, ["nonroot", "caps", "nnp", "setuid", "mount", "unshare", "su"])


# --------------------------------------------------------------------------
# Resources
# --------------------------------------------------------------------------


def test_process_spam_is_bounded_by_pids_limit(sandbox: Sandbox) -> None:
    sb = _with(sandbox, pids=64)
    r = _run(
        sb,
        """
        import subprocess
        procs = []
        try:
            for _ in range(1000):
                procs.append(subprocess.Popen(["sleep", "60"]))
        except OSError:
            pass
        print(len(procs))
        for p in procs:
            p.kill()
        """,
    )
    assert r.exit_code == 0, r.stderr
    assert 0 < int(r.stdout) < 64


def test_fork_bomb_is_contained_and_removed(sandbox: Sandbox) -> None:
    sb = _with(sandbox, pids=64, wall_seconds=10, output_bytes=2000)
    t0 = time.monotonic()
    r = _run(sb, "import os\nwhile True:\n    os.fork()\n")
    assert time.monotonic() - t0 < 40
    assert r.exit_code != 0
    assert _leftover(sb.last_container_name or "") == ""


def test_memory_hog_is_killed(sandbox: Sandbox) -> None:
    sb = _with(sandbox, memory_mb=256)
    r = _run(
        sb,
        """
        chunks = []
        for _ in range(64):
            b = bytearray(64 * 1024 * 1024)
            b[::4096] = b"x" * len(b[::4096])
            chunks.append(b)
        print("survived", len(chunks))
        """,
    )
    assert "survived" not in r.stdout
    assert r.exit_code != 0
    assert r.exit_code == 137 or "MemoryError" in r.stderr, (r.exit_code, r.stderr)
    assert _leftover(sb.last_container_name or "") == ""


def test_infinite_loop_hits_timeout_and_container_is_removed(sandbox: Sandbox) -> None:
    sb = _with(sandbox, wall_seconds=5)
    t0 = time.monotonic()
    r = _run(sb, "print('start', flush=True)\nwhile True:\n    pass\n")
    elapsed = time.monotonic() - t0
    assert r.timed_out
    assert r.exit_code == TIMEOUT_EXIT_CODE
    assert 5 <= elapsed < 20
    assert r.stdout.startswith("start")
    name = sb.last_container_name
    assert name is not None and name.startswith("sciagent-sbx-")
    assert _leftover(name) == ""


def test_disk_file_size_is_bounded(sandbox: Sandbox, run_dir: Path) -> None:
    sb = _with(sandbox, max_file_mb=8)
    r = _run(
        sb,
        """
        try:
            with open("/work/big.bin", "wb") as f:
                for _ in range(64):
                    f.write(b"\\0" * (1 << 20))
                    f.flush()
            print("LEAK fsize")
        except OSError:
            print("BLOCKED fsize")
        """,
    )
    # RLIMIT_FSIZE raises SIGXFSZ (default: terminate) or EFBIG, depending on
    # the signal disposition; either way the file stays bounded.
    assert "LEAK" not in r.stdout
    assert (run_dir / "work" / "big.bin").stat().st_size <= 8 * (1 << 20)
