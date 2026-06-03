"""Self-improvement coach — post-run analysis → prioritized action plan.

Three lenses on the data:
  1. Suite quality   — flake / broken / slow_regression / stable_passing / new
                       (from HISTORY_DIR archived report.json snapshots)
                       v1.3.0 adds 4 Edge AI runner signals:
                         - latency_p95_exceeded_sla (🔴 high)
                         - fps_variance_across_runs (🟡 medium)
                         - iou_jitter_per_tc        (🟡 medium)
                         - coverage_gap_per_label   (🟡 medium)
                       These fire when test entries carry the v1.3
                       `edge_metrics` shape.
  2. MCP usability   — top tools, error rates, repeat patterns, chain patterns
                       (from telemetry tool-usage log)
  3. AI strategy     — generated test adoption + analyze_url coverage gaps
                       (from telemetry generation + modules logs)

Output: structured dict + markdown at OPTIMIZATION_PATH. The runner auto-writes
this after each archived run; AI editors read it via MCP resource.
"""
import json
import math
import os
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from ..config import (
    HISTORY_DIR,
    OPTIMIZATION_PATH,
    TOOL_USAGE_LOG,
    GENERATION_LOG,
    MODULES_LOG,
    PROJECT_ROOT,
)
from . import telemetry


# --- Public API --------------------------------------------------------------

def build_plan(history_limit: int = 10, telemetry_limit: int = 500) -> dict:
    history = _load_history(history_limit)
    suite = _analyze_suite(history)
    usability = _analyze_usability(telemetry_limit)
    strategy = _analyze_strategy(history, telemetry_limit)
    actions = _prioritize(suite, usability, strategy)
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "history_runs": len(history),
        "suite_quality": suite,
        "mcp_usability": usability,
        "test_strategy": strategy,
        "prioritized_actions": actions,
    }


def write_plan(plan: dict | None = None) -> Path:
    if plan is None:
        plan = build_plan()
    try:
        OPTIMIZATION_PATH.write_text(_to_markdown(plan), encoding="utf-8")
    except OSError:
        pass
    return OPTIMIZATION_PATH


# --- Layer 1: Suite quality --------------------------------------------------

def _load_history(limit: int) -> list[dict]:
    """Return oldest-first list of {file, data} from HISTORY_DIR."""
    if not HISTORY_DIR.exists():
        return []
    files = sorted(HISTORY_DIR.glob("*.json"), reverse=True)[:limit]
    runs: list[dict] = []
    for f in reversed(files):
        try:
            runs.append({"file": f.name, "data": json.loads(f.read_text(encoding="utf-8"))})
        except (OSError, json.JSONDecodeError):
            continue
    return runs


def _err_signature(longrepr: str) -> str:
    """Collapse a failure message into a stable signature (drop line numbers/memory addrs)."""
    if not longrepr:
        return ""
    tail = str(longrepr).strip()[-300:]
    tail = re.sub(r":\d+\b", ":N", tail)
    tail = re.sub(r"0x[0-9a-fA-F]+", "0xMEM", tail)
    tail = re.sub(r"\s+", " ", tail)
    return tail[:200]


def _analyze_suite(history: list[dict]) -> dict:
    if not history:
        return {"empty": True, "tests": []}

    by_test: dict[str, dict] = {}
    for run in history:
        for t in run["data"].get("tests", []) or []:
            nodeid = t.get("nodeid")
            if not nodeid:
                continue
            entry = by_test.setdefault(nodeid, {
                "outcomes": [], "durations": [], "error_sigs": [], "rerun_count": 0,
            })
            outcome = t.get("outcome", "?")
            # pytest-rerunfailures emits a "rerun" pre-record before the final
            # outcome — don't double-count it as a separate run, but track it
            # as an in-run flake signal.
            if outcome == "rerun":
                entry["rerun_count"] += 1
                continue
            entry["outcomes"].append(outcome)
            dur = (t.get("call") or {}).get("duration")
            if isinstance(dur, (int, float)):
                entry["durations"].append(float(dur))
            if outcome == "failed":
                entry["error_sigs"].append(_err_signature((t.get("call") or {}).get("longrepr", "")))

    tests: list[dict] = []
    for nodeid, e in by_test.items():
        outcomes = e["outcomes"]
        n = len(outcomes)
        passed = outcomes.count("passed")
        failed = outcomes.count("failed")
        skipped = outcomes.count("skipped")

        transitions = sum(
            1 for a, b in zip(outcomes, outcomes[1:])
            if a != b and {a, b}.issubset({"passed", "failed"})
        )
        flake_score = transitions / max(1, n - 1)

        durations = e["durations"]
        avg_dur = sum(durations) / len(durations) if durations else 0
        recent = durations[-3:] if durations else []
        recent_dur = sum(recent) / len(recent) if recent else 0
        dur_regression = (recent_dur / avg_dur - 1) if (avg_dur > 0 and recent_dur > 0) else 0

        rerun_count = e["rerun_count"]
        last3 = outcomes[-3:]
        last3_sigs = e["error_sigs"][-3:]
        if (
            len(last3) == 3 and last3.count("failed") == 3
            and len(set(last3_sigs)) == 1 and last3_sigs[0]
        ):
            category = "broken"
        elif flake_score >= 0.3 and n >= 3:
            category = "flaky"
        elif rerun_count >= 1 and n >= 2:
            # Tests that needed pytest-rerunfailures retry — flaky even if the
            # final outcome was passed.
            category = "flaky"
        elif n >= 5 and passed == n:
            category = "stable_passing"
        elif dur_regression >= 0.5 and avg_dur >= 1.0:
            category = "slow_regression"
        elif n == 1:
            category = "new"
        else:
            category = "normal"

        tests.append({
            "nodeid": nodeid,
            "category": category,
            "outcomes": "".join(o[0].upper() if o else "?" for o in outcomes),
            "runs": n,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "rerun_count": rerun_count,
            "flake_score": round(flake_score, 2),
            "avg_duration_sec": round(avg_dur, 2),
            "duration_regression": round(dur_regression, 2),
            "last_error_signature": e["error_sigs"][-1] if e["error_sigs"] else None,
        })

    return {
        "total_tests": len(tests),
        "by_category": dict(Counter(t["category"] for t in tests)),
        "tests": tests,
        "edge_signals": _analyze_edge_signals(history),
    }


# --- Edge AI Runner flake signals (v1.3.0) ----------------------------------

def _edge_metrics_of(test_entry: dict) -> dict | None:
    """Return the `edge_metrics` block if this test was run by the edge
    runner; else None. Shape: `{p95_latency_ms, fps, iou_per_frame: [...],
    labels_covered: [...]}` — additive optional field per
    docs/MIGRATION-1.x.md v1.2.1 → v1.3.0 entry.
    """
    em = test_entry.get("edge_metrics")
    return em if isinstance(em, dict) else None


def _stddev(values: list[float]) -> float:
    """Sample stddev. Returns 0 for ≤1 sample (no variance signal possible)."""
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1))


def _analyze_edge_signals(history: list[dict]) -> dict:
    """v1.3.0 — Edge runner-specific flake signals.

    Walks history's test entries looking for `edge_metrics` blocks
    (additive per-test field added in v1.3). Returns four signal lists:

      - latency_sla_breaches  — current run's p95 > EDGE_LATENCY_SLA_MS
      - fps_variance          — stddev(fps)/mean > 0.2 across history
      - iou_jitter            — stddev(iou_per_frame) > 0.1 across history
      - coverage_gaps         — labels asserted somewhere but missing
                                from tests' nodeids

    Output `{}` when no test entry has an `edge_metrics` block.
    """
    if not history:
        return {}

    sla_ms = float(os.environ.get("EDGE_LATENCY_SLA_MS", "40"))

    # Bucket by nodeid for the per-tc analyses.
    metrics_by_test: dict[str, list[dict]] = defaultdict(list)
    for run in history:
        for t in run["data"].get("tests", []) or []:
            em = _edge_metrics_of(t)
            if em is None:
                continue
            nodeid = t.get("nodeid")
            if nodeid:
                metrics_by_test[nodeid].append(em)

    if not metrics_by_test:
        return {}

    latency_sla_breaches: list[dict] = []
    fps_variance: list[dict] = []
    iou_jitter: list[dict] = []
    all_labels: set[str] = set()
    label_covered_by_nodeid: dict[str, set[str]] = defaultdict(set)

    for nodeid, runs in metrics_by_test.items():
        # latency_p95_exceeded_sla — current (last) run only
        latest = runs[-1]
        p95 = latest.get("p95_latency_ms")
        if isinstance(p95, (int, float)) and p95 > sla_ms:
            latency_sla_breaches.append({
                "nodeid": nodeid,
                "p95_latency_ms": float(p95),
                "sla_ms": sla_ms,
            })

        # fps_variance_across_runs — relative stddev > 0.2
        fps_values = [
            float(r["fps"]) for r in runs
            if isinstance(r.get("fps"), (int, float))
        ]
        if len(fps_values) >= 5:
            mean_fps = sum(fps_values) / len(fps_values)
            if mean_fps > 0:
                rel_stddev = _stddev(fps_values) / mean_fps
                if rel_stddev > 0.2:
                    fps_variance.append({
                        "nodeid": nodeid,
                        "relative_stddev": round(rel_stddev, 3),
                        "fps_window": fps_values,
                    })

        # iou_jitter_per_tc — stddev of per-frame iou > 0.1 (across runs)
        iou_samples: list[float] = []
        for r in runs:
            iou_list = r.get("iou_per_frame") or []
            iou_samples.extend(
                float(v) for v in iou_list
                if isinstance(v, (int, float))
            )
        if len(iou_samples) >= 5:
            iou_sd = _stddev(iou_samples)
            if iou_sd > 0.1:
                iou_jitter.append({
                    "nodeid": nodeid,
                    "iou_stddev": round(iou_sd, 3),
                    "sample_count": len(iou_samples),
                })

        # Track per-nodeid labels for the coverage gap signal.
        for r in runs:
            for label in (r.get("labels_covered") or []):
                if isinstance(label, str):
                    all_labels.add(label)
                    label_covered_by_nodeid[nodeid].add(label)

    # coverage_gap_per_label — labels asserted SOMEWHERE in edge_metrics
    # but not mentioned in any test's nodeid string. Catches the case
    # where annotations have a label but no test exists for it.
    coverage_gaps: list[dict] = []
    all_nodeids = " ".join(metrics_by_test.keys()).lower()
    for label in sorted(all_labels):
        if label.lower() not in all_nodeids:
            coverage_gaps.append({
                "label": label,
                "evidence": (
                    "appears in edge_metrics.labels_covered but no "
                    "test nodeid contains the label string"
                ),
            })

    return {
        "latency_sla_breaches": latency_sla_breaches,
        "fps_variance": fps_variance,
        "iou_jitter": iou_jitter,
        "coverage_gaps": coverage_gaps,
    }


# --- Layer 2: MCP usability --------------------------------------------------

def _analyze_usability(limit: int) -> dict:
    records = telemetry.read_recent(TOOL_USAGE_LOG, limit)
    if not records:
        return {"empty": True}

    by_tool = Counter(r.get("tool") for r in records if r.get("tool"))
    error_rate_by_tool: dict[str, float] = {}
    for tool, total in by_tool.items():
        err = sum(1 for r in records if r.get("tool") == tool and r.get("error_type"))
        error_rate_by_tool[tool] = round(err / total, 2) if total else 0.0

    repeat_counter = Counter(
        (r.get("tool"), r.get("args_hash")) for r in records
        if r.get("tool") and r.get("args_hash")
    )
    repeats = [
        {"tool": tool, "args_hash": ah, "count": c}
        for (tool, ah), c in repeat_counter.most_common(5)
        if c >= 3
    ]

    dur_by_tool: dict[str, list[int]] = defaultdict(list)
    for r in records:
        if isinstance(r.get("duration_ms"), int) and r.get("tool"):
            dur_by_tool[r["tool"]].append(r["duration_ms"])
    avg_duration_ms = {t: int(sum(v) / len(v)) for t, v in dur_by_tool.items() if v}

    pairs: Counter = Counter()
    for a, b in zip(records, records[1:]):
        ta, tb = a.get("tool"), b.get("tool")
        if ta and tb and ta != tb:
            pairs[(ta, tb)] += 1
    top_chains = [{"a": a, "b": b, "count": c} for (a, b), c in pairs.most_common(3) if c >= 2]

    return {
        "total_calls": len(records),
        "by_tool": dict(by_tool),
        "error_rate_by_tool": error_rate_by_tool,
        "avg_duration_ms": avg_duration_ms,
        "repeat_patterns": repeats,
        "top_chains": top_chains,
    }


# --- Layer 3: AI test strategy ----------------------------------------------

def _analyze_strategy(history: list[dict], limit: int) -> dict:
    generations = telemetry.read_recent(GENERATION_LOG, limit)
    discovered = telemetry.read_recent(MODULES_LOG, limit)
    if not generations and not discovered:
        return {"empty": True}

    latest_tests = (history[-1]["data"].get("tests", []) if history else []) or []

    generation_results: list[dict] = []
    for g in generations[-20:]:
        fname = g.get("filename", "")
        stem = Path(fname).stem if fname else ""
        matched_outcomes = [
            t.get("outcome") for t in latest_tests
            if (fname and (fname in t.get("nodeid", "") or stem in t.get("nodeid", "")))
        ]
        generation_results.append({
            "filename": fname,
            "ts": g.get("ts"),
            "source": g.get("source"),
            "appeared_in_latest_run": bool(matched_outcomes),
            "outcomes_in_latest_run": matched_outcomes,
        })

    discovered_names: set[str] = set()
    for d in discovered[-20:]:
        for n in d.get("module_names", []) or []:
            if n:
                discovered_names.add(n)
    try:
        existing_test_stems = {p.stem.lower() for p in PROJECT_ROOT.glob("**/test_*.py") if p.is_file()}
    except OSError:
        existing_test_stems = set()
    coverage_gaps: list[dict] = []
    for name in sorted(discovered_names):
        slug = re.sub(r"[^\w]+", "_", name.lower())
        if not slug:
            continue
        if not any(slug in t for t in existing_test_stems):
            coverage_gaps.append({"module_name": name, "slug": slug})

    adoption_rate = (
        sum(1 for g in generation_results if g["appeared_in_latest_run"])
        / max(1, len(generation_results))
    )

    return {
        "generations_tracked": len(generation_results),
        "adoption_rate": round(adoption_rate, 2),
        "generations": generation_results[-10:],
        "coverage_gaps": coverage_gaps[:10],
    }


# --- Prioritize across layers ------------------------------------------------

def _prioritize(suite: dict, usability: dict, strategy: dict) -> list[dict]:
    actions: list[dict] = []

    if not suite.get("empty"):
        for t in suite.get("tests", []):
            cat = t["category"]
            if cat == "broken":
                actions.append({
                    "priority": "high",
                    "category": "broken",
                    "target": t["nodeid"],
                    "evidence": f"連 3 次失敗、error signature 相同；outcomes={t['outcomes']}",
                    "suggestion": "穩定 selector 或檢查是否為真 bug；用 get_failure_details 看完整 trace",
                    "auto_action_hint": f'call get_failure_details(test_id="{t["nodeid"].split("::")[-1]}")',
                })
            elif cat == "flaky":
                rerun_note = f", rerun_count={t['rerun_count']}" if t.get("rerun_count") else ""
                actions.append({
                    "priority": "high",
                    "category": "flaky",
                    "target": t["nodeid"],
                    "evidence": f"flake_score={t['flake_score']}, outcomes={t['outcomes']}{rerun_note}",
                    "suggestion": "加 explicit wait（wait_for_response / locator wait）或檢查 race condition；考慮先標 xfail 隔離",
                })
            elif cat == "slow_regression":
                actions.append({
                    "priority": "medium",
                    "category": "slow_regression",
                    "target": t["nodeid"],
                    "evidence": f"avg={t['avg_duration_sec']}s，近 3 次 +{int(t['duration_regression']*100)}%",
                    "suggestion": "檢查新增 network/DB 操作；考慮 mock 或拆解",
                })
            elif cat == "stable_passing" and t["runs"] >= 10:
                actions.append({
                    "priority": "low",
                    "category": "stable_passing",
                    "target": t["nodeid"],
                    "evidence": f"{t['passed']}/{t['runs']} 連續通過",
                    "suggestion": "考慮從 daily smoke 移到 release tier 節省 CI 時間",
                })

        # v1.3.0 — Edge AI runner signals. Each lives next to the
        # category-driven actions above and uses the same action shape
        # so downstream consumers (HTML report, AI editor) don't need
        # special-casing.
        edge_signals = suite.get("edge_signals") or {}
        for entry in edge_signals.get("latency_sla_breaches", []):
            actions.append({
                "priority": "high",
                "category": "edge_latency_p95_exceeded_sla",
                "target": entry["nodeid"],
                "evidence": (
                    f"p95={entry['p95_latency_ms']:.1f}ms > "
                    f"SLA={entry['sla_ms']:.1f}ms"
                ),
                "suggestion": (
                    "推論延遲超 SLA — 換 GPU / 量化模型 / 降輸入解析度 / "
                    "拉 EDGE_LATENCY_SLA_MS 對應實際硬體門檻"
                ),
            })
        for entry in edge_signals.get("fps_variance", []):
            actions.append({
                "priority": "medium",
                "category": "edge_fps_variance_across_runs",
                "target": entry["nodeid"],
                "evidence": (
                    f"相對 stddev={entry['relative_stddev']*100:.1f}% "
                    f"across last {len(entry['fps_window'])} runs"
                ),
                "suggestion": (
                    "FPS 跨次抖動偏高 — 隔離環境負載 / 固定 CPU 親和度 / "
                    "排查共用 GPU 競爭資源"
                ),
            })
        for entry in edge_signals.get("iou_jitter", []):
            actions.append({
                "priority": "medium",
                "category": "edge_iou_jitter_per_tc",
                "target": entry["nodeid"],
                "evidence": (
                    f"IoU stddev={entry['iou_stddev']} across "
                    f"{entry['sample_count']} frames"
                ),
                "suggestion": (
                    "偵測穩定度差 — 確認資料前處理是否一致 / 模型版本固定 / "
                    "annotation 框是否正確"
                ),
            })
        for entry in edge_signals.get("coverage_gaps", []):
            actions.append({
                "priority": "medium",
                "category": "edge_coverage_gap_per_label",
                "target": entry["label"],
                "evidence": entry["evidence"],
                "suggestion": (
                    f'call generate_test(description="detect {entry["label"]} '
                    f'frames", label="{entry["label"]}", ...) for explicit '
                    "label coverage"
                ),
            })

    if not strategy.get("empty"):
        for gap in strategy.get("coverage_gaps", []):
            actions.append({
                "priority": "medium",
                "category": "coverage_gap",
                "target": gap["module_name"],
                "evidence": "由 analyze_url 偵測但 repo 內找不到對應 test_*.py",
                "suggestion": f'call generate_test(description="<module purpose>", filename="test_{gap["slug"]}.py")',
            })
        if strategy.get("adoption_rate", 0) < 0.5 and strategy.get("generations_tracked", 0) >= 3:
            actions.append({
                "priority": "medium",
                "category": "ai_adoption",
                "target": "generate_test pipeline",
                "evidence": f"近期生成的測試採用率僅 {int(strategy.get('adoption_rate',0)*100)}%",
                "suggestion": "TC 模板可能太空泛（# TODO 未補完）；考慮整合 analyze_url 的 selectors 預填模板",
            })

    if not usability.get("empty"):
        for rp in usability.get("repeat_patterns", []):
            if rp["count"] >= 3:
                actions.append({
                    "priority": "low",
                    "category": "mcp_repeat",
                    "target": rp["tool"],
                    "evidence": f"同一 args 連續呼叫 {rp['count']} 次",
                    "suggestion": "考慮加 cache 層或合併為一次性 tool",
                })
        for tool, rate in usability.get("error_rate_by_tool", {}).items():
            if rate >= 0.3:
                actions.append({
                    "priority": "medium",
                    "category": "mcp_error_prone",
                    "target": tool,
                    "evidence": f"錯誤率 {int(rate*100)}%",
                    "suggestion": "檢查 input validation 或補 error handling",
                })
        for chain in usability.get("top_chains", []):
            if chain["count"] >= 3:
                actions.append({
                    "priority": "low",
                    "category": "mcp_chain",
                    "target": f"{chain['a']} → {chain['b']}",
                    "evidence": f"連續呼叫 {chain['count']} 次",
                    "suggestion": "考慮包成 meta-tool 減少 round-trip",
                })

    order = {"high": 0, "medium": 1, "low": 2}
    actions.sort(key=lambda a: order.get(a["priority"], 9))
    return actions


# --- Markdown rendering ------------------------------------------------------

_PRIO_ICON = {"high": "🔴", "medium": "🟡", "low": "🟢"}


def _to_markdown(plan: dict) -> str:
    lines: list[str] = [
        f"# Optimization Plan — {plan['generated_at']}",
        "",
        f"_Based on {plan['history_runs']} archived runs._",
        "",
        "## Prioritized Actions",
        "",
    ]
    actions = plan.get("prioritized_actions", [])
    if not actions:
        lines.append("_目前沒有需要立即處理的事項。系統運行良好。_")
        lines.append("")
    else:
        for i, a in enumerate(actions, 1):
            icon = _PRIO_ICON.get(a["priority"], "•")
            lines.append(f"### {i}. {icon} {a['priority'].upper()} — {a['category']}")
            lines.append(f"- **Target**: `{a['target']}`")
            lines.append(f"- **Evidence**: {a['evidence']}")
            lines.append(f"- **Suggestion**: {a['suggestion']}")
            if a.get("auto_action_hint"):
                lines.append(f"- **Auto-action hint**: `{a['auto_action_hint']}`")
            lines.append("")

    suite = plan.get("suite_quality", {})
    if not suite.get("empty"):
        lines.append("## Suite Quality Summary")
        lines.append("")
        lines.append(f"- Tests tracked across history: **{suite.get('total_tests', 0)}**")
        for k, v in sorted(suite.get("by_category", {}).items()):
            lines.append(f"  - {k}: {v}")
        lines.append("")

    us = plan.get("mcp_usability", {})
    if not us.get("empty"):
        lines.append("## MCP Usability")
        lines.append("")
        lines.append(f"- Tool calls tracked: **{us.get('total_calls', 0)}**")
        top = sorted(us.get("by_tool", {}).items(), key=lambda x: -x[1])[:5]
        if top:
            lines.append("- Top tools:")
            for name, n in top:
                lines.append(f"  - `{name}`: {n}")
        chains = us.get("top_chains") or []
        if chains:
            lines.append("- Common chains:")
            for c in chains:
                lines.append(f"  - `{c['a']}` → `{c['b']}` × {c['count']}")
        lines.append("")

    st = plan.get("test_strategy", {})
    if not st.get("empty"):
        lines.append("## AI Test Generation")
        lines.append("")
        if "adoption_rate" in st:
            lines.append(f"- Adoption rate: **{int(st['adoption_rate']*100)}%** "
                         f"({st.get('generations_tracked', 0)} generated)")
        gaps = st.get("coverage_gaps", [])
        if gaps:
            lines.append(f"- Coverage gaps ({len(gaps)} module(s) without test files):")
            for g in gaps[:5]:
                lines.append(f"  - `{g['module_name']}`")
        lines.append("")

    return "\n".join(lines)
