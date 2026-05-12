"""URL → testable modules + candidate TCs.

Heuristic analyzer: opens a URL with Playwright, probes the DOM for forms,
nav, dialogs, labeled sections, and CTA buttons, and emits a structured JSON
that the MCP client (AI editor) consumes to synthesize actual tests via
`generate_test`. Runner-agnostic and side-effect-free on the target site.
"""
import re
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
    return {
        "url": url,
        "page_title": page_title,
        "scanned_at": datetime.now().isoformat(timespec="seconds"),
        "module_count": len(modules),
        "modules": modules,
        "api_endpoint_count": len(endpoints),
        "api_endpoints": endpoints,
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

  return { forms, navs, dialogs, sections, ctas };
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
