"""URL → testable modules + candidate TCs.

Heuristic analyzer with two entry points:
  - analyze_url(): opens a URL with Playwright, probes the DOM (web)
  - analyze_screen(): dumps current screen via `maestro hierarchy` (mobile)

Both emit the same shape — modules + candidate TCs — so the MCP client
(AI editor) consumes them uniformly to drive `generate_test`. Runner-
agnostic and side-effect-free on the target.
"""
import json as _json
import re
import shutil
import subprocess
from datetime import datetime
from typing import Any
from urllib.parse import urlparse


async def analyze_url(
    url: str,
    timeout_ms: int = 15000,
    auth_cookie: str | None = None,
) -> dict[str, Any]:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {
            "error": "需要 playwright async API：pip install playwright && playwright install chromium",
            "url": url,
        }

    api_calls: list[dict] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            context = await browser.new_context()
            cookies = _parse_cookie_string(auth_cookie, url) if auth_cookie else []
            if cookies:
                await context.add_cookies(cookies)
            page = await context.new_page()

            def on_request(req):
                if req.resource_type not in ("fetch", "xhr"):
                    return
                api_calls.append({
                    "method": req.method,
                    "url": req.url,
                    "resource_type": req.resource_type,
                })

            def on_response(resp):
                # Attach status to the last matching call without a status yet.
                for call in api_calls:
                    if call["url"] == resp.url and "status" not in call:
                        call["status"] = resp.status
                        ct = resp.headers.get("content-type", "")
                        call["content_type"] = ct.split(";")[0].strip() if ct else None
                        return

            page.on("request", on_request)
            page.on("response", on_response)

            try:
                await page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            except Exception as e:
                return {"error": f"打開頁面失敗: {type(e).__name__}: {e}", "url": url}

            # Give late XHRs a chance — bounded so the tool stays snappy.
            try:
                await page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass

            page_title = await page.title()
            structure = await page.evaluate(_DOM_PROBE_JS)
        finally:
            await browser.close()

    modules = _build_modules(structure or {})
    endpoints = _dedupe_endpoints(api_calls)
    layout_warnings = (structure or {}).get("layout_warnings", []) or []
    return {
        "url": url,
        "page_title": page_title,
        "scanned_at": datetime.now().isoformat(timespec="seconds"),
        "module_count": len(modules),
        "modules": modules,
        "api_endpoint_count": len(endpoints),
        "api_endpoints": endpoints,
        # Visible elements whose content escapes their container at the
        # current viewport — typical "跑版" signal (text overflow, hard-px
        # widths, etc.). Bounded to 20 entries in the probe.
        "layout_warning_count": len(layout_warnings),
        "layout_warnings": layout_warnings,
    }


def _parse_cookie_string(cookie_str: str, url: str) -> list[dict]:
    host = urlparse(url).hostname or ""
    cookies: list[dict] = []
    for part in cookie_str.split(";"):
        if "=" not in part:
            continue
        name, _, value = part.strip().partition("=")
        if name:
            cookies.append({"name": name, "value": value, "domain": host, "path": "/"})
    return cookies


def _dedupe_endpoints(calls: list[dict]) -> list[dict]:
    """Collapse duplicate (method, path) pairs and attach candidate TCs."""
    seen: dict[tuple[str, str], dict] = {}
    for c in calls:
        parsed = urlparse(c["url"])
        key = (c["method"].upper(), parsed.path or "/")
        if key in seen:
            continue
        entry = {
            "method": c["method"].upper(),
            "url": c["url"],
            "path": parsed.path or "/",
            "host": parsed.hostname or "",
            "resource_type": c.get("resource_type"),
            "status": c.get("status"),
            "content_type": c.get("content_type"),
        }
        entry["candidate_tcs"] = _api_candidate_tcs(entry)
        seen[key] = entry
    return list(seen.values())


def _api_candidate_tcs(endpoint: dict) -> list[str]:
    method = endpoint["method"]
    path = endpoint["path"]
    tcs: list[str] = []
    if method == "GET":
        tcs += [
            f"GET {path} 正常請求應回 2xx，response schema 符合契約",
            f"GET {path} 缺少 auth header 應回 401/403",
            f"GET {path} 帶不存在的 ID 應回 404",
            f"GET {path} 帶異常 query 參數應有 graceful 回應（不應 500）",
        ]
    elif method == "POST":
        tcs += [
            f"POST {path} payload 缺必填欄位應回 400 + 欄位錯誤訊息",
            f"POST {path} 合法 payload 應回 2xx 並建立資源",
            f"POST {path} 重複建立應依設計回 409 或 idempotent 2xx",
            f"POST {path} 缺少 auth header 應回 401/403",
            f"POST {path} payload 超過大小限制應回 413 或 400",
        ]
    elif method in ("PUT", "PATCH"):
        tcs += [
            f"{method} {path} 對不存在資源應回 404",
            f"{method} {path} 合法 payload 應更新並回 2xx",
            f"{method} {path} 部分欄位更新應保留未變動欄位（特別針對 PATCH）",
            f"{method} {path} 缺少 auth header 應回 401/403",
        ]
    elif method == "DELETE":
        tcs += [
            f"DELETE {path} 對不存在資源應回 404 或 idempotent 2xx",
            f"DELETE {path} 成功應移除資源，二次呼叫應回 404",
            f"DELETE {path} 缺少 auth header 應回 401/403",
        ]
    status = endpoint.get("status")
    if isinstance(status, int) and 400 <= status < 600:
        tcs.append(f"注意：載入時實際 status={status}，請先確認是否為已知問題或預期狀態")
    return tcs


_DOM_PROBE_JS = r"""
() => {
  const esc = (s) => (window.CSS && CSS.escape) ? CSS.escape(s) : (s || '').replace(/[^a-zA-Z0-9_-]/g, '_');
  const sel = (el) => {
    if (!el) return null;
    if (el.id) return '#' + esc(el.id);
    const t = el.getAttribute('data-testid');
    if (t) return `[data-testid="${t}"]`;
    const n = el.getAttribute('name');
    if (n && el.tagName === 'INPUT') return `${el.tagName.toLowerCase()}[name="${n}"]`;
    const a = el.getAttribute('aria-label');
    if (a) return `${el.tagName.toLowerCase()}[aria-label="${a}"]`;
    return el.tagName.toLowerCase();
  };
  const txt = (el) => (el && (el.innerText || el.textContent) || '').trim().slice(0, 80);
  const labelFor = (i) => {
    const id = i.getAttribute('id');
    if (id) {
      const l = document.querySelector(`label[for="${esc(id)}"]`);
      if (l) return txt(l);
    }
    const p = i.closest('label');
    if (p) return txt(p);
    return i.getAttribute('aria-label') || i.getAttribute('placeholder') || i.getAttribute('name') || '';
  };

  const forms = [...document.querySelectorAll('form')].map((f, i) => {
    const fields = [...f.querySelectorAll('input, textarea, select')]
      .filter(el => el.type !== 'hidden')
      .map(el => ({
        label: labelFor(el),
        selector: sel(el),
        type: el.tagName === 'INPUT' ? (el.type || 'text') : el.tagName.toLowerCase(),
        required: el.required || el.getAttribute('aria-required') === 'true',
      }));
    const sb = f.querySelector('button[type="submit"], input[type="submit"], button:not([type])');
    return {
      index: i, selector: sel(f),
      action: f.getAttribute('action') || null,
      method: (f.getAttribute('method') || 'get').toLowerCase(),
      fields,
      submit: sb ? { selector: sel(sb), text: txt(sb) } : null,
    };
  });

  const navs = [...document.querySelectorAll('nav, [role="navigation"]')].map((n, i) => ({
    index: i, selector: sel(n),
    label: n.getAttribute('aria-label') || '',
    links: [...n.querySelectorAll('a[href]')].map(a => ({
      text: txt(a), href: a.getAttribute('href'),
    })).filter(l => l.text || l.href).slice(0, 30),
  })).filter(n => n.links.length > 0);

  const dialogs = [...document.querySelectorAll('dialog, [role="dialog"], [role="alertdialog"]')].map((d, i) => ({
    index: i, selector: sel(d),
    label: d.getAttribute('aria-label') || txt(d.querySelector('h1,h2,h3,[role="heading"]')) || '',
    open: d.tagName === 'DIALOG' ? d.hasAttribute('open') : !d.hidden,
  }));

  const sections = [...document.querySelectorAll('section[aria-label], section[aria-labelledby], [role="region"][aria-label]')].map((s, i) => {
    const lbId = s.getAttribute('aria-labelledby');
    const labelled = lbId ? document.getElementById(lbId) : null;
    return {
      index: i, selector: sel(s),
      label: s.getAttribute('aria-label') || txt(labelled) || txt(s.querySelector('h1,h2,h3')) || '',
    };
  });

  const ctaPatterns = ['登入','登出','註冊','結帳','送出','提交','下一步','繼續','購買','加入購物車','搜尋','查詢','確認','取消','訂閱','Sign in','Sign up','Login','Logout','Submit','Continue','Next','Checkout','Subscribe','Buy','Add to cart','Search'];
  const ctas = [...document.querySelectorAll('button, [role="button"], a.button, a.btn')]
    .filter(b => !b.closest('form'))
    .map(b => ({ text: txt(b), selector: sel(b), tag: b.tagName.toLowerCase() }))
    .filter(b => b.text && ctaPatterns.some(p => b.text.includes(p)))
    .slice(0, 20);

  // Layout warnings: visible elements whose content overflows their container.
  // Threshold tuning: horizontal >2px is almost always a real break (text
  // 跑版, hard-px width, etc.). Vertical <=10px is usually line-height /
  // emoji baseline noise (emoji glyphs sit a few px above CJK x-height) so
  // we only flag vertical overflow once it exceeds that band. Skips
  // invisible elements + intentional scrollers (overflow: auto/scroll).
  const layout_warnings = [...document.querySelectorAll('body *')]
    .filter(el => {
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) return false;
      const cs = getComputedStyle(el);
      if (cs.visibility === 'hidden' || cs.display === 'none' || parseFloat(cs.opacity) === 0) return false;
      const dx = el.scrollWidth - el.clientWidth;
      const dy = el.scrollHeight - el.clientHeight;
      if (dx <= 2 && dy <= 10) return false;
      // Intentional scrollers (overflow: auto / scroll) are not bugs.
      if (cs.overflowX === 'auto' || cs.overflowX === 'scroll') return false;
      if (cs.overflowY === 'auto' || cs.overflowY === 'scroll') return false;
      return true;
    })
    .slice(0, 20)
    .map(el => {
      const r = el.getBoundingClientRect();
      return {
        selector: sel(el),
        tag: el.tagName.toLowerCase(),
        text_sample: txt(el).slice(0, 40),
        overflow_x: el.scrollWidth - el.clientWidth,
        overflow_y: el.scrollHeight - el.clientHeight,
        bbox: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) },
      };
    });

  return { forms, navs, dialogs, sections, ctas, layout_warnings };
}
"""


def _slug(text: str, fallback: str) -> str:
    if not text:
        return fallback
    s = re.sub(r"[^\w]+", "_", text.lower()).strip("_")
    return s or fallback


def _build_modules(structure: dict) -> list[dict]:
    modules: list[dict] = []

    for form in structure.get("forms") or []:
        fields = form.get("fields") or []
        basis = form.get("action") or " ".join(f.get("label", "") for f in fields if f.get("label")) or "form"
        name = f"{_slug(basis, 'form')}_form_{form['index']}"
        required = [f for f in fields if f.get("required")]
        has_password = any((f.get("type") or "").lower() == "password" for f in fields)
        has_email = any((f.get("type") or "").lower() == "email" for f in fields)

        tcs: list[str] = []
        if fields:
            tcs.append("所有必填欄位為空時送出，應顯示必填錯誤")
            for f in required[:3]:
                label = f.get("label") or f.get("selector") or "field"
                tcs.append(f"只填其他欄位、{label} 留空，應顯示該欄位必填錯誤")
            if has_email:
                tcs.append("Email 欄位填入格式錯誤的字串（無 @），應顯示格式錯誤")
            if has_password:
                tcs.append("Password 欄位輸入後應預設遮蔽（type=password）")
                tcs.append("Password 太短或不符合複雜度規則時應顯示錯誤")
            tcs.append("全部填入合法值後送出，應觸發成功流程（導頁或顯示成功訊息）")
        else:
            tcs.append("直接點擊送出，應有適當回應或無作用")

        modules.append({
            "kind": "form",
            "name": name,
            "selectors": {
                "container": form.get("selector"),
                "fields": fields,
                "submit": (form.get("submit") or {}).get("selector"),
            },
            "metadata": {
                "method": form.get("method"),
                "action": form.get("action"),
                "field_count": len(fields),
            },
            "candidate_tcs": tcs,
        })

    for nav in structure.get("navs") or []:
        label = nav.get("label") or f"nav_{nav['index']}"
        link_count = len(nav.get("links") or [])
        modules.append({
            "kind": "nav",
            "name": _slug(label, f"nav_{nav['index']}"),
            "selectors": {"container": nav.get("selector")},
            "links": nav.get("links"),
            "candidate_tcs": [
                f"nav 內每個連結（共 {link_count} 個）點擊後應導向對應 href",
                "在小螢幕寬度下 nav 應可摺疊／展開（若為 responsive）",
                "鍵盤 Tab 鍵應能依序聚焦每個 nav 連結",
            ],
        })

    for d in structure.get("dialogs") or []:
        label = d.get("label") or f"dialog_{d['index']}"
        modules.append({
            "kind": "dialog",
            "name": _slug(label, f"dialog_{d['index']}"),
            "selectors": {"container": d.get("selector")},
            "metadata": {"open_on_load": d.get("open")},
            "candidate_tcs": [
                "觸發 dialog 開啟後焦點應落入 dialog 內",
                "按 ESC 或點擊遮罩應關閉 dialog（如設計允許）",
                "dialog 開啟時背景滾動應被鎖定",
                "關閉後焦點應回到觸發按鈕",
            ],
        })

    for s in structure.get("sections") or []:
        label = s.get("label") or f"section_{s['index']}"
        modules.append({
            "kind": "section",
            "name": _slug(label, f"section_{s['index']}"),
            "selectors": {"container": s.get("selector")},
            "candidate_tcs": [f"{label} 區塊應正確渲染（非空、無 console error）"],
        })

    for cta in structure.get("ctas") or []:
        text = cta.get("text") or ""
        modules.append({
            "kind": "cta",
            "name": _slug(text, "cta"),
            "selectors": {"trigger": cta.get("selector")},
            "metadata": {"label_text": text, "tag": cta.get("tag")},
            "candidate_tcs": [
                f"點擊「{text}」應觸發對應動作（導頁／開 dialog／送 API）",
                f"「{text}」在 loading 狀態下應禁用以避免重複觸發",
            ],
        })

    return modules


# ---- analyze_screen (mobile) ----------------------------------------------

def analyze_screen(
    app_id: str | None = None,
    launch_app: bool = False,
    timeout_ms: int = 30000,
) -> dict[str, Any]:
    """Mobile equivalent of analyze_url. Captures current screen via
    `maestro hierarchy` and surfaces interactive elements as modules.

    Requires:
      - Maestro CLI installed (https://maestro.mobile.dev)
      - A simulator / emulator / device booted with the target app foregrounded

    Args:
      app_id: Optional. When `launch_app=True`, launches this bundle id first.
      launch_app: When True + app_id given, runs `launchApp` before hierarchy.
      timeout_ms: Subprocess timeout for the hierarchy dump.

    Returns same shape as analyze_url (`modules` + `candidate_tcs` per module),
    plus a `screen_summary` describing what was found.
    """
    if not shutil.which("maestro"):
        return {
            "error": "maestro CLI 找不到。安裝：curl -fsSL https://get.maestro.mobile.dev | bash",
        }

    # Optional: launch the app first so hierarchy reflects its starting screen.
    # We write the launch flow to a temp file because `maestro test -` (stdin)
    # behaved inconsistently across versions; temp-file is the well-trodden
    # path.
    if app_id and launch_app:
        import os as _os
        import tempfile
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8",
        )
        try:
            tmp.write(
                f"appId: {app_id}\n"
                "---\n"
                "- launchApp:\n"
                "    clearState: false\n"
                "- waitForAnimationToEnd:\n"
                "    timeout: 5000\n"
            )
            tmp.close()
            subprocess.run(
                ["maestro", "test", tmp.name],
                capture_output=True,
                text=True,
                timeout=timeout_ms / 1000 + 10,
            )
        except subprocess.TimeoutExpired:
            return {"error": "launch app 逾時"}
        except OSError as e:
            return {"error": f"無法啟動 app：{type(e).__name__}: {e}"}
        finally:
            try:
                _os.unlink(tmp.name)
            except OSError:
                pass

    # Pull current screen hierarchy.
    try:
        result = subprocess.run(
            ["maestro", "hierarchy"],
            capture_output=True,
            text=True,
            timeout=timeout_ms / 1000,
        )
    except subprocess.TimeoutExpired:
        return {"error": "maestro hierarchy 逾時 — simulator 沒回應或無 booted device"}
    except OSError as e:
        return {"error": f"執行 maestro 失敗：{type(e).__name__}: {e}"}

    if result.returncode != 0:
        return {
            "error": "maestro hierarchy 失敗",
            "stderr_tail": (result.stderr or "")[-500:],
        }

    # Strip preamble lines ("None:" / device label) — JSON starts at the first `{`.
    raw = result.stdout
    brace = raw.find("{")
    if brace < 0:
        return {"error": "hierarchy 輸出無 JSON 主體", "stdout_tail": raw[-500:]}
    try:
        tree = _json.loads(raw[brace:])
    except _json.JSONDecodeError as e:
        return {"error": f"JSON 解析失敗：{e}", "stdout_tail": raw[brace:brace + 500]}

    nodes = []
    _walk_screen(tree, nodes, depth=0)
    modules, summary = _build_screen_modules(nodes)

    return {
        "app_id": app_id,
        "scanned_at": datetime.now().isoformat(timespec="seconds"),
        "module_count": len(modules),
        "modules": modules,
        "screen_summary": summary,
    }


# Heuristics for filtering noise from analyze_screen output. Real-world
# iOS/Android hierarchies surface asset names (bg_*, ic_*, *_filled) and
# placeholder text (--, single ASCII chars) as accessibility labels. These
# rarely correspond to user-intended interactions and just dilute the
# candidate list. Patterns below were tuned against the union-ios home
# screen where the raw output mixed real buttons with asset identifiers.
_NOISE_PREFIX_RE = re.compile(r"^(bg_|ic_|icon_|img_|image_)")
_NOISE_SUFFIX_RE = re.compile(r"(_filled|_outline|_image|_logo|_brand_logo|_active|_inactive)$")
_NOISE_PUNCT_RE = re.compile(r"^[-_.,\s　]+$")
_NOISE_NUM_ONLY_RE = re.compile(r"^[\d.,\-\+%元$]+$")


def _is_noise_text(text: str) -> bool:
    """Return True for labels that look like asset names / placeholders
    rather than user-facing CTA copy."""
    t = (text or "").strip()
    if not t:
        return True
    # Single ASCII character (e.g. "x", "+") is almost never a real button
    # in a CJK app; single Chinese characters can be (e.g. 「我」) so we
    # only filter single-char when ASCII.
    if len(t) == 1 and t.isascii():
        return True
    if _NOISE_PUNCT_RE.match(t):
        return True
    if _NOISE_NUM_ONLY_RE.match(t):
        return True
    if _NOISE_PREFIX_RE.search(t):
        return True
    if _NOISE_SUFFIX_RE.search(t):
        return True
    return False


def _walk_screen(node: dict, out: list, depth: int) -> None:
    """Flatten the Maestro hierarchy tree into a list of attribute dicts.

    Maestro nests view containers heavily — we keep every node with any
    interactive signal (text / accessibilityText / hintText / resource-id)
    plus its bounds for downstream classification.
    """
    if not isinstance(node, dict) or depth > 60:
        return
    attrs = node.get("attributes") or {}
    if isinstance(attrs, dict):
        flat = {
            "text": (attrs.get("text") or "").strip(),
            "accessibility_text": (attrs.get("accessibilityText") or "").strip(),
            "hint_text": (attrs.get("hintText") or "").strip(),
            "title": (attrs.get("title") or "").strip(),
            "value": (attrs.get("value") or "").strip(),
            "resource_id": (attrs.get("resource-id") or "").strip(),
            "bounds": attrs.get("bounds") or "",
            "enabled": (attrs.get("enabled") or "false").lower() == "true",
            "focused": (attrs.get("focused") or "false").lower() == "true",
            "selected": (attrs.get("selected") or "false").lower() == "true",
            "checked": (attrs.get("checked") or "false").lower() == "true",
            "depth": depth,
        }
        # Keep nodes with at least one identifying signal, plus an enabled flag.
        if any([flat["text"], flat["accessibility_text"], flat["hint_text"],
                flat["title"], flat["resource_id"]]):
            out.append(flat)
    for child in node.get("children") or []:
        _walk_screen(child, out, depth + 1)


def _parse_bounds(b: str) -> tuple[int, int, int, int] | None:
    """`[x1,y1][x2,y2]` → (x, y, w, h) in screen pixels. None if unparseable."""
    m = re.match(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]", b or "")
    if not m:
        return None
    x1, y1, x2, y2 = map(int, m.groups())
    return x1, y1, max(0, x2 - x1), max(0, y2 - y1)


def _node_label(n: dict) -> str:
    """Human-readable label — first non-empty among text / a11yText / title / hint."""
    for k in ("text", "accessibility_text", "title", "hint_text"):
        if n.get(k):
            return n[k]
    return ""


def _build_screen_modules(nodes: list[dict]) -> tuple[list[dict], dict]:
    """Classify flattened nodes into modules + a screen-level summary.

    iOS Maestro hierarchy doesn't expose XCUIElement classes; we lean on
    field semantics: hintText → input, text+enabled → CTA candidate,
    selected toggling at low y → likely tab/segmented control. Bounded
    output (top N) to keep payload tractable.
    """
    inputs: list[dict] = []
    ctas: list[dict] = []
    selected_nodes: list[dict] = []
    label_only: list[dict] = []

    for n in nodes:
        bounds = _parse_bounds(n["bounds"])
        # Skip invisible / zero-area + iOS status bar (y < 50 covers signal /
        # battery / time / wifi indicators that aren't part of the app's UI).
        if bounds and (bounds[2] == 0 or bounds[3] == 0):
            continue
        if bounds and bounds[1] < 50:
            continue

        # Inputs: hint text reliably means a TextField/EditText.
        if n["hint_text"] or (n["focused"] and not n["text"]):
            inputs.append({**n, "_bounds": bounds})
            continue

        # CTAs: text + enabled is the obvious case (UIControl-style).
        # ALSO promote leaf-ish nodes with meaningful text + reasonable bounds
        # to CTA candidates — SwiftUI / RN buttons often appear with enabled=false
        # at the leaf level even though they're tappable. Threshold of 24x24 px
        # filters decorative micro-labels but keeps real buttons.
        # Noise filter drops asset-name labels (bg_*, *_filled) and placeholder
        # text ("--", single ASCII chars, pure digits/currency).
        label = n["text"] or n["accessibility_text"]
        if label and not _is_noise_text(label):
            if n["enabled"]:
                ctas.append({**n, "_bounds": bounds})
                continue
            if bounds and bounds[2] >= 24 and bounds[3] >= 24:
                ctas.append({**n, "_bounds": bounds, "_inferred": True})
                continue

        if n["selected"] and label:
            selected_nodes.append({**n, "_bounds": bounds})
        elif label:
            label_only.append({**n, "_bounds": bounds})

    # Dedup CTAs by label (keep first; iOS often nests duplicates per layer).
    seen = set()
    unique_ctas = []
    for c in ctas:
        key = _node_label(c)
        if key in seen:
            continue
        seen.add(key)
        unique_ctas.append(c)

    modules: list[dict] = []

    if inputs:
        fields = [
            {
                "label": _node_label(f) or f.get("hint_text") or "(unnamed input)",
                "hint": f.get("hint_text"),
                "resource_id": f.get("resource_id") or None,
            }
            for f in inputs[:10]
        ]
        modules.append({
            "kind": "form",
            "name": "screen_inputs",
            "selectors": {"fields": fields},
            "candidate_tcs": [
                "所有必填欄位為空時送出，應顯示必填錯誤",
                "輸入超長字串應安全處理（截斷或拒絕）",
                "Email / 數字等格式欄位輸入錯誤格式應提示",
                "鍵盤遮蔽輸入框時應 scroll 至可見",
            ],
        })

    for cta in unique_ctas[:15]:
        label = _node_label(cta)
        modules.append({
            "kind": "cta",
            "name": _slug(label, "cta"),
            "selectors": {
                "text": label,
                "resource_id": cta.get("resource_id") or None,
            },
            "metadata": {
                "label_text": label,
                "enabled": cta.get("enabled"),
                "bounds": cta.get("_bounds"),
            },
            "candidate_tcs": [
                f"點擊「{label}」應觸發對應動作（導頁／open modal／API call）",
                f"「{label}」在 loading 狀態下應禁用以避免重複觸發",
            ],
        })

    # Tab bar / segmented control inference: ≥ 2 selected-capable items at
    # similar y position near top or bottom of screen.
    if len(selected_nodes) >= 2:
        ys = sorted({n["_bounds"][1] for n in selected_nodes if n["_bounds"]})
        if ys:
            # cluster: nodes within 30px of each other → same row
            groups: list[list[dict]] = []
            for n in selected_nodes:
                placed = False
                if not n["_bounds"]:
                    continue
                for g in groups:
                    if any(abs(n["_bounds"][1] - m["_bounds"][1]) <= 30 for m in g):
                        g.append(n)
                        placed = True
                        break
                if not placed:
                    groups.append([n])
            for g in groups:
                if len(g) >= 2:
                    labels = [_node_label(m) for m in g if _node_label(m)]
                    if not labels:
                        continue
                    modules.append({
                        "kind": "tab_bar",
                        "name": "tab_bar",
                        "tabs": [{"label": l} for l in labels],
                        "candidate_tcs": [
                            f"切換每個 tab（共 {len(labels)} 個）應顯示對應內容",
                            "tab 選中狀態視覺應正確（高亮 / icon 變色）",
                            "切換 tab 後再切回原 tab 狀態應保留（如 scroll 位置）",
                        ],
                    })

    summary = {
        "input_count": len(inputs),
        "interactive_count": len(unique_ctas),
        "selected_count": len(selected_nodes),
        "label_only_count": len(label_only),
        "total_meaningful_nodes": len(inputs) + len(unique_ctas) + len(selected_nodes) + len(label_only),
    }
    return modules, summary
