"""Defect hunt B: store checks 2, 3, 5, 6 on any campaign store (read-only).

    uv run python .cache/hunt/B/store_check.py --root .cache/pilot --truths 20
    uv run python .cache/hunt/B/store_check.py --root .cache/minipilot \
        --ids dev-008,... --seeds 1

The store is first COPIED to a temp dir (opening a ResultStore creates
directories and opens the SQLite ledger; the source is never opened).

2. every llm-session cell is found by ``stored_sessions``; session_config
   digest unchanged for harness/1 cells.
3. budgets: per record, charged experiments/fits match tool calls, refusals
   uncharged, reading/detail counts match the record.
5. void policy: new (voids.session_void) vs stored flag.
6. store invariants: digest == key digest == filename; ledger rows == detail
   files, identical readings; append-only triggers refuse UPDATE/DELETE/
   REPLACE; put() of different bytes refused; score cells reference existing
   sessions; key fields present.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from pilot import (
    build_parser,
    load_split,
    pointproc_environment,
    reconfigure_stdout,
    select,
    truth_entries,
)

FAILS: list[str] = []


def fail(msg: str) -> None:
    FAILS.append(msg)
    print("FAIL:", msg)


def main() -> None:
    reconfigure_stdout()
    p = build_parser()
    p.add_argument("--sample", type=int, default=0, help="0: all records")
    args = p.parse_args()

    from sciagent.campaign.plan import (
        METRIC_VERSION,
        CampaignConfig,
        Unit,
        score_key,
        session_config_digest,
        session_key,
        session_text,
    )
    from sciagent.campaign.rescore import stored_sessions
    from sciagent.campaign.store import ResultStore, cell_bytes, key_from_json
    from sciagent.campaign.voids import decoded, session_void
    from sciagent.core.errors import AppendOnlyViolationError, RegistryConflictError
    from sciagent.campaign.plan import CampaignError
    from sciagent.glm.fit import FIT_VERSION
    from sciagent.harness.live import HARNESS_VERSION
    from sciagent.harness.record import SessionRecord

    src = args.root.resolve()
    tmp = Path(tempfile.mkdtemp(prefix="storecheck-", dir=HERE))
    root = tmp / "store"
    shutil.copytree(src, root)
    print(f"source {src}\ncopy   {root}")

    records = select(load_split(args.split), args.truths, args.ids)
    env = pointproc_environment(load_split(args.split))
    seeds = tuple(int(s) for s in args.seeds.split(","))
    control = tuple(int(s) for s in args.control_seeds.split(",") if int(s) in seeds)
    config = CampaignConfig(
        seeds=seeds,
        control_seeds=control,
        max_turns=args.max_turns,
        control_truths=args.b10f_truths,
    )
    truths = truth_entries(records)
    by_id = {t.id: t for t in truths}

    # ---------------- 6a: detail files vs ledger ---------------------------
    details = sorted((root / "details").glob("*.json"))
    cells: dict[str, dict] = {}
    for path in details:
        raw = path.read_bytes()
        cell = json.loads(raw)
        key = key_from_json(cell["key"])
        if not (str(key.digest) == cell["digest"] == path.stem):
            fail(f"6 digest mismatch {path.name}")
        reading = {k: float.fromhex(v) for k, v in cell["reading"].items()}
        if cell_bytes(key, reading, cell["detail"]) != raw:
            fail(f"6 detail bytes not canonical {path.name}")
        cells[path.stem] = cell
    con = sqlite3.connect(
        f"file:{(root / 'ledger.sqlite').as_posix()}?mode=ro", uri=True
    )
    rows = con.execute(
        "SELECT sequence,digest,env_version,config,data_version,metric_version,seed,reading FROM cells ORDER BY sequence"
    ).fetchall()
    triggers = [
        r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
    ]
    con.close()
    ledger_digests = [r[1] for r in rows]
    print(
        f"\n[6] detail files {len(details)}, ledger rows {len(rows)}, triggers {sorted(triggers)}"
    )
    if set(ledger_digests) != set(cells):
        fail(
            f"6 ledger/detail sets differ: only-ledger {len(set(ledger_digests) - set(cells))} only-detail {len(set(cells) - set(ledger_digests))}"
        )
    if len(set(ledger_digests)) != len(ledger_digests):
        fail("6 duplicate digests in ledger")
    seqs = [r[0] for r in rows]
    if seqs != sorted(seqs):
        fail("6 sequence not monotone")
    mism = 0
    for seq, dg, envv, cfg, dv, mv, seed, reading in rows:
        c = cells.get(dg)
        if c is None:
            continue
        k = c["key"]
        if (envv, dv, mv, seed) != (
            k["env_version"],
            k["data_version"],
            k["metric_version"],
            k["seed"],
        ):
            mism += 1
        if json.loads(cfg) != k["config"]:
            mism += 1
        lr = json.loads(reading)
        cr = c["reading"]
        if {
            kk: float.fromhex(v) if isinstance(v, str) else v for kk, v in lr.items()
        }.keys() != cr.keys():
            mism += 1
    print(
        f"[6] ledger rows whose key/reading fields disagree with the detail file: {mism}"
    )
    if mism:
        fail(f"6 {mism} ledger/detail field mismatches")

    kinds = Counter(
        (
            c["key"]["config"].get("kind"),
            c["key"]["env_version"],
            c["key"]["metric_version"],
        )
        for c in cells.values()
    )
    for k, v in sorted(kinds.items()):
        print(f"    {v:4d} {k}")
    need = {
        "baseline": {
            "fit_config",
            "kind",
            "system",
            "truth",
            "truth_digest",
            "system_config",
        },
        "llm-session": {
            "fit_config",
            "kind",
            "system",
            "truth",
            "truth_digest",
            "session_config",
        },
        "llm-score": {"fit_config", "kind", "system", "truth", "session"},
    }
    for dg, c in cells.items():
        kind = c["key"]["config"].get("kind")
        missing = need.get(kind, set()) - set(c["key"]["config"])
        if missing:
            fail(f"6 cell {dg[:12]} kind {kind} missing key fields {missing}")
        if kind == "llm-score" and c["key"]["config"]["session"] not in cells:
            fail(
                f"6 score {dg[:12]} references absent session {c['key']['config']['session'][:12]}"
            )
        if kind == "baseline":
            txt = c["detail"].get("system_config")
            if (
                txt is None
                or hashlib.sha256(txt.encode()).hexdigest()
                != c["key"]["config"]["system_config"]
            ):
                fail(f"6 baseline {dg[:12]} system_config text does not hash to key")
        if kind == "llm-session":
            txt = c["detail"].get("session_config")
            if (
                txt is None
                or hashlib.sha256(txt.encode()).hexdigest()
                != c["key"]["config"]["session_config"]
            ):
                fail(f"6 session {dg[:12]} session_config text does not hash to key")

    # 6b: append-only enforcement on the COPY's ledger
    con = sqlite3.connect(root / "ledger.sqlite")
    for stmt in (
        "UPDATE cells SET reading='{}' WHERE sequence=1",
        "DELETE FROM cells WHERE sequence=1",
        "INSERT OR REPLACE INTO cells SELECT * FROM cells WHERE sequence=1",
    ):
        try:
            con.execute(stmt)
            con.commit()
            fail(f"6 raw sqlite accepted: {stmt}")
        except sqlite3.DatabaseError as e:
            print(f"[6] raw sqlite refused ({type(e).__name__}: {e}): {stmt[:40]}")
    con.close()

    store = ResultStore(root)
    first = next(iter(cells.values()))
    k0 = key_from_json(first["key"])
    r0 = {k: float.fromhex(v) for k, v in first["reading"].items()}
    try:
        store.put(k0, r0, first["detail"])  # identical bytes: accepted (faithful rerun)
        print("[6] put() of identical bytes accepted (idempotent)")
    except RegistryConflictError as e:
        fail(f"6 identical put refused: {e}")
    try:
        store.put(k0, {**r0, "status": 99.0}, first["detail"])
        fail("6 put() of a different reading accepted")
    except RegistryConflictError as e:
        print(f"[6] put() of different bytes refused: {str(e)[:90]}")
    try:
        store.ledger.append(k0, reading={**r0, "status": 99.0})
        fail("6 ledger.append of a different reading for an existing digest accepted")
    except (RegistryConflictError, AppendOnlyViolationError) as e:
        print(f"[6] ledger.append of a different reading refused: {type(e).__name__}")

    # ---------------- 2: stored_sessions ----------------------------------
    sess_cells = {
        dg: c
        for dg, c in cells.items()
        if c["key"]["config"].get("kind") == "llm-session"
    }
    print(
        f"\n[2] llm-session cells in store: {len(sess_cells)}; HARNESS_VERSION={HARNESS_VERSION}; FIT_VERSION={FIT_VERSION}"
    )
    found = stored_sessions(store, env, config, truths)
    print(f"[2] stored_sessions found {len(found)} units")
    found_digests = {str(k.digest) for k, _ in found.values()}
    notfound = [dg for dg in sess_cells if dg not in found_digests]
    for dg in notfound:
        c = sess_cells[dg]["key"]["config"]
        print(
            f"    NOT FOUND {dg[:12]} {c['system']} {c['truth']} s{sess_cells[dg]['key']['seed']}"
        )
    if notfound:
        fail(f"2 {len(notfound)} session cells not found by stored_sessions")
    changed = 0
    at_current = 0
    for dg, c in sess_cells.items():
        f = c["key"]["config"]
        try:
            arm = config.arm_of(f["system"])
        except CampaignError:
            continue
        if f["session_config"] != session_config_digest(arm, config):
            changed += 1
            print(f"    session_config digest changed: {dg[:12]} {f['system']}")
        if c["detail"]["session_config"] != session_text(arm, config):
            changed += 1
        t = by_id.get(f["truth"])
        if t is not None:
            if f["truth_digest"] != t.digest:
                fail(f"2 truth digest changed for {f['truth']}")
            cur = session_key(
                Unit(t.id, int(c["key"]["seed"]), f["system"]), t, env, config
            )
            at_current += str(cur.digest) == dg
    print(
        f"[2] session_config digest/text changed: {changed}; sessions at the CURRENT address: {at_current} (others are re-referenced by enumeration)"
    )
    if changed:
        fail(f"2 {changed} session_config digests/texts changed")
    # score address under current versions for each found session: never collides with a stored old score
    old_scores = {
        dg for dg, c in cells.items() if c["key"]["config"].get("kind") == "llm-score"
    }
    collide = sum(
        str(score_key(u, k, env, config).digest) in old_scores
        and c["key"]["metric_version"] != METRIC_VERSION
        for u, (k, c) in found.items()
    )
    print(
        f"[2] new score keys colliding with stored older-version score cells: {collide}"
    )

    # ---------------- 5: void policy -------------------------------------
    print("\n[5] void policy")
    tally = Counter()
    rows5 = []
    for u, (k, c) in sorted(found.items()):
        reading = decoded(c["reading"])
        det = c["detail"]["session"]
        v = session_void(root, reading, det)
        tally[(v.stored_void, v.void)] += 1
        if v.stored_void or v.void:
            rows5.append(
                (
                    u,
                    v,
                    det.get("void_reasons") or det.get("error"),
                    reading.get("status"),
                )
            )
    print(f"    (stored_void, policy_void) counts: {dict(sorted(tally.items()))}")
    print(
        f"    stored void total {sum(n for (s, _), n in tally.items() if s)}; policy void total {sum(n for (_, p), n in tally.items() if p)}"
    )
    for u, v, stored, status in rows5:
        print(
            f"    {u.system:14s} {u.truth_id} s{u.seed} status={status} stored={v.stored_void} policy={v.void} "
            f"attempts={v.unknown_tool_attempts} stored_reasons={stored} policy_reasons={list(v.reasons)}"
        )
    # do reclassified sessions get a score queued? (no score cell at the current address yet is expected pre-rescore)
    reclass = [u for u, v, *_ in rows5 if v.reclassified]
    print(f"    reclassified (stored void, policy not void): {len(reclass)}")

    # ---------------- 3: budgets -----------------------------------------
    print("\n[3] budgets")
    items = sorted(found.items())
    if args.sample:
        items = items[: args.sample]
    checked = 0
    for u, (k, c) in items:
        det = c["detail"]["session"]
        reading = decoded(c["reading"])
        if reading.get("status") != 1.0:
            continue
        path = root / det["record_dir"] / det["record"]
        rec = SessionRecord.load(path)
        cfgs = rec.of_kind("config")
        budgets = cfgs[0].body["budgets"]
        tools = {t["name"]: t for t in cfgs[0].body["tools"]}
        if budgets != {"experiments": config.experiments, "fits": config.fits, "submit": 1}:
            print(f"    note: budgets {budgets}")
        calls = sorted(
            (e.body for e in rec.of_kind("tool_call")), key=lambda b: b["call"]
        )
        used = {b: 0 for b in budgets}
        n_refused_exhaust = 0
        problems = []
        for i, b in enumerate(calls):
            if b["call"] != i:
                problems.append(f"call index {b['call']} != {i}")
            spec = tools.get(b["name"])
            bud = None if spec is None else spec["budget"]
            exp_charge = bud if (bud is not None and not b["is_error"]) else None
            if b["charged"] != exp_charge:
                problems.append(
                    f"call {i} {b['name']} is_error={b['is_error']} charged={b['charged']} expected {exp_charge}"
                )
            if b["is_error"] and b["charged"] is not None:
                problems.append(f"call {i} refused but charged")
            if b["charged"] is not None:
                used[b["charged"]] += 1
            if dict(b["budgets_after"]) != used:
                problems.append(
                    f"call {i} budgets_after {b['budgets_after']} != running {used}"
                )
            if "budget exhausted" in b["text"] and b["is_error"]:
                n_refused_exhaust += 1
                if used.get(bud, -1) < budgets.get(bud, 0):
                    problems.append(
                        f"call {i} exhaustion refusal while used {used} < limit"
                    )
            for name, lim in budgets.items():
                if used[name] > lim:
                    problems.append(f"call {i} over budget {name} {used[name]}>{lim}")
        charged_fits = [b for b in calls if b["charged"] == "fits"]
        charged_exp = [b for b in calls if b["charged"] == "experiments"]
        if len(charged_fits) != reading.get("fits_used"):
            problems.append(
                f"fits_used {reading.get('fits_used')} != charged fit calls {len(charged_fits)}"
            )
        if len(charged_exp) != reading.get("experiments_used"):
            problems.append(
                f"experiments_used {reading.get('experiments_used')} != charged experiment calls {len(charged_exp)}"
            )
        if len(det.get("fitted", [])) != len(charged_fits):
            problems.append(
                f"detail.fitted {len(det.get('fitted', []))} != charged fit calls {len(charged_fits)}"
            )
        if len(calls) != reading.get("tool_calls"):
            problems.append(
                f"reading tool_calls {reading.get('tool_calls')} != record tool_call entries {len(calls)}"
            )
        cnt = Counter(b["name"] for b in calls)
        if dict(sorted(cnt.items())) != det.get("tool_calls"):
            problems.append(
                f"detail.tool_calls {det.get('tool_calls')} != {dict(sorted(cnt.items()))}"
            )
        # charged fits must carry a fit record index, sequential
        idx = [int((b.get("record") or {}).get("fit", -1)) for b in charged_fits]
        if idx != sorted(idx) or len(set(idx)) != len(idx):
            problems.append(f"fit record indices not sequential: {idx}")
        checked += 1
        if problems:
            fail(f"3 {u.system} {u.truth_id} s{u.seed}: {problems[:4]}")
    print(f"[3] records checked: {checked}")

    store.close()
    print("\nSUMMARY:", "PASS" if not FAILS else f"{len(FAILS)} FAIL(s)")
    for f in FAILS:
        print("  -", f)
    print(f"(temp copy left at {tmp}; delete when done)")


if __name__ == "__main__":
    main()
