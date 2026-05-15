"""HTML report generator — runner-agnostic, self-contained, dark-mode styled."""
import base64
from datetime import datetime
from html import escape
from pathlib import Path

from ..config import PROJECT_ROOT
from ..runners import get_runner


def _embed_screenshot(path_str: str | None) -> str:
    """Read a PNG from disk and inline it as a base64 <img>, or return ''."""
    if not path_str:
        return ""
    try:
        p = Path(path_str)
        if not p.is_file():
            return ""
        data = p.read_bytes()
    except OSError:
        return ""
    b64 = base64.b64encode(data).decode("ascii")
    return (
        '<div class="screenshot">'
        '<div class="screenshot-label">Screenshot</div>'
        f'<img src="data:image/png;base64,{b64}" alt="failure screenshot">'
        '</div>'
    )


def _rel_to_project(path_str: str | None) -> str | None:
    """Convert an absolute artifact path to a relative one for the HTML link.

    Why: traces/videos can be several MB — too big to base64-inline. We link them
    instead. The HTML is written to PROJECT_ROOT, so relative links work as long
    as the test-results/ folder ships alongside the HTML.
    """
    if not path_str:
        return None
    try:
        return str(Path(path_str).relative_to(PROJECT_ROOT))
    except ValueError:
        return path_str


def _artifact_links(failure: dict) -> str:
    """Render trace.zip and video.webm links for a failure card."""
    trace = _rel_to_project(failure.get("trace"))
    video = _rel_to_project(failure.get("video"))
    if not trace and not video:
        return ""
    parts: list[str] = []
    if video:
        parts.append(
            f'<a class="artifact-link" href="{escape(video)}" target="_blank" rel="noopener">'
            f'<span class="artifact-tag">VIDEO</span>{escape(video)}</a>'
        )
    if trace:
        parts.append(
            f'<a class="artifact-link" href="{escape(trace)}" target="_blank" rel="noopener" '
            f'title="Open with: npx playwright show-trace {escape(trace)}">'
            f'<span class="artifact-tag">TRACE</span>{escape(trace)}</a>'
        )
    return f'<div class="artifacts">{"".join(parts)}</div>'


def _sparkline(history: list[dict]) -> str:
    """Inline-SVG pass-rate bars, oldest left → newest right. Empty if <2 runs."""
    if len(history) < 2:
        return ""
    max_h, bar_w, gap = 32, 8, 2
    bars: list[str] = []
    for i, h in enumerate(history):
        rate = h.get("pass_rate", 0) or 0
        height = max(2.0, max_h * (rate / 100.0))
        if rate >= 90:
            color = "var(--green)"
        elif rate >= 70:
            color = "var(--yellow)"
        else:
            color = "var(--red)"
        x = i * (bar_w + gap)
        y = max_h - height
        bars.append(
            f'<rect x="{x}" y="{y:.1f}" width="{bar_w}" height="{height:.1f}" '
            f'fill="{color}" rx="1.5"><title>{escape(h.get("timestamp",""))} · '
            f'{h.get("passed",0)}/{h.get("total",0)} pass</title></rect>'
        )
    total_w = len(history) * (bar_w + gap)
    return f'<svg class="spark" viewBox="0 0 {total_w} {max_h}" width="{total_w}" height="{max_h}">{"".join(bars)}</svg>'


def _render_trend(history: list[dict], current_failed: int) -> str:
    """Render the trend section if at least 2 runs are archived."""
    if len(history) < 2:
        return ""
    spark = _sparkline(history)
    # history[-1] is the current run (just archived); use history[-2] as previous.
    prev = history[-2]
    prev_failed = prev.get("failed", 0) or 0
    delta = current_failed - prev_failed
    if delta > 0:
        delta_html = f'<span class="delta worse">▲ +{delta} failures vs last run</span>'
    elif delta < 0:
        delta_html = f'<span class="delta better">▼ {-delta} fewer failures vs last run</span>'
    else:
        delta_html = '<span class="delta same">— same failure count as last run</span>'
    return (
        '<div class="section-title">Trend</div>'
        '<div class="trend">'
        f'<div class="trend-spark">{spark}</div>'
        '<div class="trend-info">'
        f'<div class="trend-count">{len(history)} runs archived</div>'
        f'<div>{delta_html}</div>'
        '</div>'
        '</div>'
    )


def _render_test_meta(title: str | None, nodeid: str) -> str:
    """Two-line summary cell: docstring as the visible case name + nodeid as
    a small mono subtitle. Falls back to nodeid-only when no docstring."""
    nodeid_esc = escape(str(nodeid))
    if title and str(title).strip():
        title_esc = escape(str(title).strip())
        return (
            '<div class="test-meta">'
            f'<div class="test-title">{title_esc}</div>'
            f'<div class="test-nodeid">{nodeid_esc}</div>'
            '</div>'
        )
    return (
        '<div class="test-meta">'
        f'<div class="test-title test-title-fallback">{nodeid_esc}</div>'
        '</div>'
    )


def _render_steps_html(steps: list[dict]) -> str:
    """Render the action list as an ordered list.

    Each item has `api` (always shown, monospace accent) and `title` (the
    descriptive payload, may be empty). When `title` is empty — common for
    arg-less Maestro actions like `- launchApp` — we render the api alone
    without duplicating it in the muted column.
    """
    if not steps:
        return ""
    items: list[str] = []
    for s in steps:
        api = escape(str(s.get("api") or ""))
        title_raw = s.get("title")
        title = escape(str(title_raw)) if title_raw else ""
        items.append(
            f'<li><code class="step-api">{api}</code>'
            f'<span class="step-title">{title}</span></li>'
        )
    return f'<ol class="steps">{"".join(items)}</ol>'


def _render_pass_section(passes: list[dict]) -> str:
    """Build the collapsed Passed-tests group with one detail card per test.

    Passes are hidden by default so the report stays focused on failures.
    Cards include the step list and screenshot for transparency on what
    "passed" actually exercised.
    """
    if not passes:
        return ""
    cards: list[str] = []
    for t in passes:
        meta_html = _render_test_meta(t.get("title"), t.get("nodeid", "unknown"))
        dur = t.get("duration")
        dur_str = f"{dur:.3f}s" if isinstance(dur, (int, float)) else ""
        steps_html = _render_steps_html(t.get("steps") or [])
        shot_html = _embed_screenshot(t.get("screenshot"))
        cards.append(
            f'<details class="pass">'
            f'<summary>'
            f'<span class="pass-badge">PASS</span>'
            f'{meta_html}'
            f'<span class="fail-dur">{dur_str}</span>'
            f'</summary>'
            f'{steps_html}'
            f'{shot_html}'
            f'</details>'
        )
    return (
        '<div class="section-title">Passed</div>'
        '<details class="pass-group">'
        f'<summary><span class="pass-count">{len(passes)}</span> tests passed — click to expand</summary>'
        f'<div class="pass-list">{"".join(cards)}</div>'
        '</details>'
    )


def render_report() -> str:
    """Render the latest test report as a self-contained HTML string."""
    runner = get_runner()
    summary = runner.get_report_summary()
    # Prefer the rich details (with steps) when available; fall back to the
    # legacy failure-only path so non-pytest runners still render.
    all_details = runner.get_all_test_details() if hasattr(runner, "get_all_test_details") else []
    if all_details:
        failures = [t for t in all_details if t.get("outcome") == "failed"]
        passes = [t for t in all_details if t.get("outcome") == "passed"]
    else:
        failures = runner.get_failure_details() or []
        passes = []

    has_error = isinstance(summary, dict) and "error" in summary
    total = int(summary.get("total", 0) or 0) if not has_error else 0
    passed = int(summary.get("passed", 0) or 0) if not has_error else 0
    failed = int(summary.get("failed", 0) or 0) if not has_error else 0
    skipped = int(summary.get("skipped", 0) or 0) if not has_error else 0
    flaky_in_run = int(summary.get("flaky_in_run", 0) or 0) if not has_error else 0
    duration = summary.get("duration") if not has_error else None

    pass_rate = (passed / total * 100) if total else 0
    duration_str = f"{duration:.2f}s" if isinstance(duration, (int, float)) else "—"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if has_error:
        failure_html = f'<div class="empty">{escape(str(summary.get("error", "找不到報告")))}</div>'
    elif failed == 0 and total > 0:
        failure_html = '<div class="empty success">所有測試通過</div>'
    elif failed == 0 and total == 0:
        failure_html = '<div class="empty">尚未執行任何測試</div>'
    else:
        cards = []
        for i, f in enumerate(failures or []):
            if isinstance(f, dict) and "error" in f:
                continue
            nodeid = escape(str(f.get("nodeid", "unknown")))
            message = escape(str(f.get("message", "")))
            dur = f.get("duration")
            dur_str = f"{dur:.3f}s" if isinstance(dur, (int, float)) else ""
            open_attr = "open" if i < 3 else ""
            shot_html = _embed_screenshot(f.get("screenshot") if isinstance(f, dict) else None)
            links_html = _artifact_links(f if isinstance(f, dict) else {})
            steps_html = _render_steps_html(f.get("steps") or []) if isinstance(f, dict) else ""
            meta_html = _render_test_meta(
                f.get("title") if isinstance(f, dict) else None,
                f.get("nodeid", "unknown") if isinstance(f, dict) else "unknown",
            )
            cards.append(
                f'<details class="failure" {open_attr}>'
                f'<summary>'
                f'<span class="fail-badge">FAIL</span>'
                f'{meta_html}'
                f'<span class="fail-dur">{dur_str}</span>'
                f'</summary>'
                f'<pre>{message}</pre>'
                f'{steps_html}'
                f'{shot_html}'
                f'{links_html}'
                f'</details>'
            )
        failure_html = "\n".join(cards) if cards else '<div class="empty">無失敗詳情</div>'

    history = runner.get_history(limit=10) if hasattr(runner, "get_history") else []
    trend_html = _render_trend(history, failed)
    passed_html = _render_pass_section(passes)

    flaky_stat_html = (
        f'<div class="stat flaky"><div class="label">Flaky (retried)</div>'
        f'<div class="value">{flaky_in_run}</div></div>'
        if flaky_in_run > 0 else ""
    )

    out = TEMPLATE
    replacements = {
        "{{RUNNER_NAME}}": escape(runner.name),
        "{{TIMESTAMP}}": timestamp,
        "{{PROJECT_ROOT}}": escape(str(PROJECT_ROOT)),
        "{{TOTAL}}": str(total),
        "{{PASSED}}": str(passed),
        "{{FAILED}}": str(failed),
        "{{SKIPPED}}": str(skipped),
        "{{FLAKY_STAT}}": flaky_stat_html,
        "{{DURATION}}": duration_str,
        "{{PASS_RATE}}": f"{pass_rate:.1f}",
        "{{PASS_RATE_INT}}": str(int(pass_rate)),
        "{{TREND_SECTION}}": trend_html,
        "{{FAILURE_SECTION}}": failure_html,
        "{{PASSED_SECTION}}": passed_html,
        "{{YEAR}}": str(datetime.now().year),
    }
    for k, v in replacements.items():
        out = out.replace(k, v)
    return out


def write_report(output: str = "report.html") -> Path:
    """Render and write the HTML report to disk under PROJECT_ROOT."""
    target = PROJECT_ROOT / output
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_report(), encoding="utf-8")
    return target


TEMPLATE = """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Test Report — MK QA Master</title>
<style>
  :root {
    --bg: #0D1117;
    --bg-elevated: #161B22;
    --bg-surface: #21262D;
    --border: #30363D;
    --text: #E6EDF3;
    --text-muted: #8B949E;
    --text-dim: #7D8590;
    --accent: #7C7FF5;
    --accent-deep: #6B6EE0;
    --green: #3FB950;
    --yellow: #D29922;
    --red: #F85149;
    --mono: 'SF Mono', 'JetBrains Mono', Menlo, Consolas, monospace;
    --sans: -apple-system, BlinkMacSystemFont, 'Inter', 'PingFang TC', 'Microsoft JhengHei', sans-serif;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: var(--sans);
    line-height: 1.6;
    -webkit-font-smoothing: antialiased;
    padding: 32px 20px;
  }
  .wrap { max-width: 960px; margin: 0 auto; }

  /* Header */
  .header {
    display: flex; align-items: center; gap: 14px;
    padding-bottom: 20px; margin-bottom: 28px;
    border-bottom: 1px solid var(--border);
    flex-wrap: wrap;
  }
  .logo {
    width: 36px; height: 36px; border-radius: 7px;
    background: var(--accent); color: #fff;
    display: flex; align-items: center; justify-content: center;
    font-family: var(--mono); font-weight: 700; font-size: 14px;
  }
  .header h1 {
    margin: 0; font-size: 20px; font-weight: 600;
    letter-spacing: -0.01em;
  }
  .header .breadcrumb { color: var(--text-dim); font-weight: 400; }
  .header .meta {
    margin-left: auto; color: var(--text-muted);
    font-family: var(--mono); font-size: 12px;
    text-align: right;
  }
  .header .meta .runner-tag {
    display: inline-block; padding: 2px 8px;
    background: rgba(124, 127, 245, 0.12);
    color: var(--accent);
    border: 1px solid rgba(124, 127, 245, 0.3);
    border-radius: 4px;
    margin-right: 8px;
  }

  /* Stats grid */
  .stats {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 12px;
    margin-bottom: 24px;
  }
  .stat {
    background: var(--bg-elevated);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px 18px;
  }
  .stat .label {
    color: var(--text-muted); font-size: 11px;
    text-transform: uppercase; letter-spacing: 0.08em;
    font-family: var(--mono); font-weight: 500;
  }
  .stat .value {
    font-size: 30px; font-weight: 700;
    margin-top: 4px; line-height: 1.1;
    font-variant-numeric: tabular-nums;
  }
  .stat .value.small { font-size: 22px; color: var(--text-muted); }
  .stat.passed .value { color: var(--green); }
  .stat.failed .value { color: var(--red); }
  .stat.skipped .value { color: var(--yellow); }
  .stat.flaky .value { color: var(--accent); }

  /* Progress */
  .progress {
    background: var(--bg-elevated);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px 20px;
    margin-bottom: 28px;
  }
  .progress-label {
    display: flex; justify-content: space-between;
    margin-bottom: 10px; font-size: 14px;
    align-items: baseline;
  }
  .progress-label .pct {
    font-family: var(--mono); font-weight: 600;
    color: var(--green);
    font-size: 16px;
  }
  .progress-bar {
    height: 8px; background: rgba(248, 81, 73, 0.18);
    border-radius: 4px; overflow: hidden;
  }
  .progress-bar .fill {
    height: 100%; background: var(--green);
    transition: width 0.3s;
  }

  /* Section title */
  .section-title {
    font-size: 11px; font-weight: 600;
    color: var(--text-muted); text-transform: uppercase;
    letter-spacing: 0.1em; font-family: var(--mono);
    margin: 28px 0 12px;
  }

  /* Project line */
  .project {
    background: var(--bg-elevated);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 14px 18px;
    font-family: var(--mono); font-size: 13px;
    color: var(--text-muted);
    word-break: break-all;
  }

  /* Failure cards */
  .failure {
    background: var(--bg-elevated);
    border: 1px solid var(--border);
    border-left: 3px solid var(--red);
    border-radius: 8px;
    margin-bottom: 10px;
    overflow: hidden;
  }
  .failure summary {
    list-style: none;
    cursor: pointer;
    padding: 14px 18px;
    display: flex; align-items: center; gap: 12px;
    user-select: none;
  }
  .failure summary::-webkit-details-marker { display: none; }
  .failure summary::before {
    content: "▸"; color: var(--text-muted);
    transition: transform 0.15s; font-size: 12px;
    flex-shrink: 0;
  }
  .failure[open] summary::before { transform: rotate(90deg); }
  .fail-badge {
    background: rgba(248, 81, 73, 0.15);
    color: var(--red); border: 1px solid rgba(248, 81, 73, 0.3);
    padding: 2px 8px; border-radius: 4px;
    font-family: var(--mono); font-size: 11px; font-weight: 700;
    letter-spacing: 0.05em;
    flex-shrink: 0;
  }
  .fail-name {
    font-family: var(--mono); font-size: 13px; flex: 1;
    color: var(--text); word-break: break-all;
  }
  /* Two-line test header: docstring as title + nodeid as subtitle. */
  .test-meta { flex: 1; min-width: 0; }
  .test-title {
    color: var(--text); font-size: 13px;
    font-weight: 500; line-height: 1.4;
    word-break: break-word;
  }
  .test-title-fallback {
    font-family: var(--mono); font-size: 12px;
    color: var(--text); word-break: break-all;
  }
  .test-nodeid {
    margin-top: 3px;
    font-family: var(--mono); font-size: 11px;
    color: var(--text-muted);
    word-break: break-all;
  }
  .fail-dur {
    color: var(--text-muted); font-family: var(--mono);
    font-size: 12px; flex-shrink: 0;
  }
  .failure pre {
    margin: 0; padding: 16px 20px;
    background: var(--bg);
    border-top: 1px solid var(--border);
    font-family: var(--mono); font-size: 12px;
    line-height: 1.6; overflow-x: auto;
    color: var(--text);
    white-space: pre-wrap; word-break: break-word;
  }
  /* Screenshot — shared by failure and pass cards.
     Why not .failure-scoped: pass cards were rendering with no img max-width
     constraint, so native ~1280px screenshots burst the 960px wrap. */
  .screenshot {
    padding: 14px 20px 18px;
    background: var(--bg);
    border-top: 1px solid var(--border);
  }
  .screenshot-label {
    color: var(--text-muted); font-family: var(--mono);
    font-size: 11px; font-weight: 600;
    text-transform: uppercase; letter-spacing: 0.08em;
    margin-bottom: 10px;
  }
  .screenshot img {
    display: block; max-width: 100%; height: auto;
    border-radius: 6px; border: 1px solid var(--border);
    background: #000;
  }
  .failure .artifacts {
    display: flex; flex-wrap: wrap; gap: 8px;
    padding: 12px 20px 16px; background: var(--bg);
    border-top: 1px solid var(--border);
  }
  /* Steps */
  .steps {
    margin: 0; padding: 12px 20px 16px 40px;
    background: var(--bg);
    border-top: 1px solid var(--border);
    font-family: var(--mono); font-size: 12px;
    line-height: 1.8; color: var(--text);
  }
  .steps li { margin: 0; padding: 1px 0; }
  .steps .step-api {
    color: var(--accent); font-weight: 600;
    margin-right: 10px; display: inline-block; min-width: 100px;
  }
  .steps .step-title { color: var(--text-muted); }

  /* Passed tests */
  .pass-group {
    background: var(--bg-elevated);
    border: 1px solid var(--border);
    border-radius: 10px;
    overflow: hidden;
  }
  .pass-group > summary {
    list-style: none; cursor: pointer;
    padding: 14px 20px;
    color: var(--text-muted); font-family: var(--mono);
    font-size: 13px; user-select: none;
  }
  .pass-group > summary::-webkit-details-marker { display: none; }
  .pass-group > summary::before {
    content: "▸"; color: var(--text-muted);
    transition: transform 0.15s; font-size: 12px;
    margin-right: 10px; display: inline-block;
  }
  .pass-group[open] > summary::before { transform: rotate(90deg); }
  .pass-count {
    color: var(--green); font-weight: 700;
    font-family: var(--mono); margin-right: 4px;
  }
  .pass-list { padding: 0 14px 14px; }
  .pass {
    background: var(--bg);
    border: 1px solid var(--border);
    border-left: 3px solid var(--green);
    border-radius: 6px;
    margin-bottom: 6px;
    overflow: hidden;
  }
  .pass summary {
    list-style: none; cursor: pointer;
    padding: 10px 16px;
    display: flex; align-items: center; gap: 10px;
    user-select: none;
  }
  .pass summary::-webkit-details-marker { display: none; }
  .pass summary::before {
    content: "▸"; color: var(--text-muted);
    transition: transform 0.15s; font-size: 12px;
    flex-shrink: 0;
  }
  .pass[open] summary::before { transform: rotate(90deg); }
  .pass-badge {
    background: rgba(63, 185, 80, 0.15);
    color: var(--green);
    border: 1px solid rgba(63, 185, 80, 0.3);
    padding: 2px 8px; border-radius: 4px;
    font-family: var(--mono); font-size: 11px; font-weight: 700;
    letter-spacing: 0.05em;
    flex-shrink: 0;
  }
  .artifact-link {
    display: inline-flex; align-items: center; gap: 8px;
    padding: 6px 10px; border-radius: 6px;
    background: var(--bg-surface); border: 1px solid var(--border);
    color: var(--text); text-decoration: none;
    font-family: var(--mono); font-size: 12px;
    transition: border-color 0.15s, background 0.15s;
  }
  .artifact-link:hover {
    border-color: var(--accent); background: rgba(124, 127, 245, 0.08);
  }
  .artifact-tag {
    display: inline-block; padding: 1px 6px; border-radius: 3px;
    background: rgba(124, 127, 245, 0.18); color: var(--accent);
    font-size: 10px; font-weight: 700; letter-spacing: 0.05em;
  }

  /* Trend */
  .trend {
    background: var(--bg-elevated); border: 1px solid var(--border);
    border-radius: 10px; padding: 16px 20px;
    display: flex; align-items: center; gap: 20px;
    flex-wrap: wrap;
  }
  .trend-spark { display: flex; align-items: flex-end; }
  .trend-spark svg.spark { display: block; }
  .trend-info { display: flex; flex-direction: column; gap: 4px; }
  .trend-count {
    color: var(--text-muted); font-family: var(--mono);
    font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em;
  }
  .delta { font-family: var(--mono); font-size: 13px; font-weight: 600; }
  .delta.worse { color: var(--red); }
  .delta.better { color: var(--green); }
  .delta.same { color: var(--text-muted); }

  /* Empty */
  .empty {
    background: var(--bg-elevated);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 32px 24px; text-align: center;
    color: var(--text-muted);
    font-family: var(--mono); font-size: 14px;
  }
  .empty.success {
    color: var(--green);
    border-color: rgba(63, 185, 80, 0.3);
    background: rgba(63, 185, 80, 0.06);
  }

  /* Footer */
  footer {
    margin-top: 48px; padding-top: 24px;
    border-top: 1px solid var(--border);
    text-align: center; color: var(--text-muted);
    font-size: 13px;
  }
  footer a { color: var(--accent); text-decoration: none; }
  footer a:hover { text-decoration: underline; }

  @media (max-width: 640px) {
    .header .meta { margin-left: 0; text-align: left; width: 100%; }
    .stat .value { font-size: 24px; }
  }
</style>
</head>
<body>
<div class="wrap">

  <header class="header">
    <div class="logo">MQM</div>
    <h1><span class="breadcrumb">MK QA Master /</span> Test Report</h1>
    <div class="meta">
      <span class="runner-tag">{{RUNNER_NAME}}</span>{{TIMESTAMP}}
    </div>
  </header>

  <div class="stats">
    <div class="stat"><div class="label">Total</div><div class="value">{{TOTAL}}</div></div>
    <div class="stat passed"><div class="label">Passed</div><div class="value">{{PASSED}}</div></div>
    <div class="stat failed"><div class="label">Failed</div><div class="value">{{FAILED}}</div></div>
    <div class="stat skipped"><div class="label">Skipped</div><div class="value">{{SKIPPED}}</div></div>
    {{FLAKY_STAT}}
    <div class="stat"><div class="label">Duration</div><div class="value small">{{DURATION}}</div></div>
  </div>

  <div class="progress">
    <div class="progress-label">
      <span>Pass Rate</span>
      <span class="pct">{{PASS_RATE}}%</span>
    </div>
    <div class="progress-bar"><div class="fill" style="width: {{PASS_RATE_INT}}%"></div></div>
  </div>

  {{TREND_SECTION}}

  <div class="section-title">Project</div>
  <div class="project">{{PROJECT_ROOT}}</div>

  <div class="section-title">Failures</div>
  {{FAILURE_SECTION}}

  {{PASSED_SECTION}}

  <footer>
    Generated by <strong style="color: var(--text);">MK QA Master</strong> ·
    <a href="https://github.com/kao273183/mk-qa-master">github.com/kao273183/mk-qa-master</a>
  </footer>

</div>
</body>
</html>
"""
