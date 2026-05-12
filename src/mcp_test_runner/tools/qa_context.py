"""Read project-level QA knowledge so the MCP can escape monkey-testing.

The MCP server itself only sees the DOM (via analyze_url) — it has no
visibility into business rules, regression history, or user journeys.
That gap is what makes auto-generated tests feel generic.

This module reads a plain-markdown knowledge file (default
PROJECT_ROOT/qa-knowledge.md, overridable via QA_KNOWLEDGE_FILE env) and
exposes it so the client AI can pull it into its reasoning context AND
inject relevant slices as `business_context` into generate_test calls.

Convention: the file uses H2 headers (## Section name) to delimit topics
like 業務規則 / 歷史 Bug / 標準斷言文字 / User Journeys. The client can
fetch a single section by name when it needs just one slice.

When the file is absent we still return a useful payload: a built-in pack
of universal QA patterns (form validation, auth, network, a11y) so users
who haven't customized anything get a non-monkey-testing baseline.
"""
import re

from ..config import QA_KNOWLEDGE_PATH


# Universal QA patterns — applicable to almost any web app. Returned as the
# fallback when no qa-knowledge.md exists so the client AI always has a
# non-zero baseline to reason against.
_BUILTIN_DEFAULTS = """# (No project qa-knowledge.md — universal QA patterns as fallback)

> 這份是內建 fallback。要做專案專屬的 QA 知識，請執行 init_qa_knowledge tool
> 或手動建立 qa-knowledge.md 於受測專案根目錄。

## 通用 QA 模式 — 表單驗證
- 必填欄位空值送出應顯示對應 inline 錯誤訊息
- 格式錯誤 (email / phone / url) 應即時或送出時驗證並提示
- 邊界值：超過最大長度、Unicode、emoji、HTML 與 SQL 注入字串應安全處理
- Double-submit 防護：快速連擊送出按鈕不應重複建立資源

## 通用 QA 模式 — 認證 / 授權
- 無 session 訪問需登入頁面應重導登入
- token 過期應友善提示重新登入、保留先前 path 以便回跳
- 嘗試訪問他人資源（橫向越權）應回 403 / 404 by design
- 登出後私密資料 (cart / cache) 應清空

## 通用 QA 模式 — 網路狀態
- 後端 5xx 應顯示友善錯誤訊息、不暴露 stack trace
- Request timeout 後 loading 狀態應解除、提供重試
- 離線 / 弱網應有 graceful degradation（cached UI / 適當提示）

## 通用 QA 模式 — 無障礙 (A11y)
- Tab 鍵順序合理、focus 環可見
- Modal / dialog 開啟時 focus trap 在內部
- 圖片：資訊性需有 alt；裝飾性用 alt=""
- 顏色對比達 WCAG AA（4.5:1 文字、3:1 大字 / UI 元件）

## 你的業務規則（請於 qa-knowledge.md 自行填入）
- 領域邏輯、折扣計算、限購規則、會員等級、優惠券規範 ...

## 你的歷史 Bug / 回歸點（請於 qa-knowledge.md 自行填入）
- 過去修過的 bug ticket ID + 簡述 + 期望行為 + 回歸觸發條件 ...

## 你的標準斷言文字（請於 qa-knowledge.md 自行填入）
- 錯誤訊息標準文案（精確字元）、成功提示文字、CTA 標籤 ...

## 你的 User Journeys（請於 qa-knowledge.md 自行填入）
- 多步驟業務流程：登入 → 加購 → 套用優惠 → 結帳 → 訂單頁 ...
"""


_STARTER_TEMPLATE = """# QA Knowledge — {project_name}

> 給 mcp-test-runner 讀的領域知識。get_qa_context() 會把這份內容暴露給 AI，
> 用於決定要測什麼 + 把規則印進產出 test 的 `# Business context:` 區段。
> 規則：以 H2 (##) 區段為單位，client 可指定 section 拉取單一段。
>
> 下列項目皆為提示骨架，請依你的領域逐項替換。
> 也可參考 mcp-test-runner repo 內的 qa-knowledge.example.md。

## 業務規則
- TODO: 列出你產品的核心業務邏輯，例如：
  - 計費 / 折扣 / 點數換算規則
  - 會員等級條件與權益
  - 限制（限購、限領、限時、限地區）
  - 訂單 / 退款 / 取消政策

## 歷史 Bug / 回歸點
- TODO: 已修的關鍵 bug（會持續觀察的回歸點），格式建議：
  - BUG-XXX（已修）：簡述問題 → 觸發條件 → 期望行為 → fix reference

## 標準斷言文字
- TODO: UI 上需要逐字驗證的文案（避免「同義不同字」誤判），例如：
  - 錯誤訊息：「（精確字串）」
  - 成功提示：「（精確字串）」
  - CTA 標籤：「（精確字串）」

## User Journeys
- TODO: 描述跨多步驟的業務流程，例如：
  - happy-path：登入 → 主要操作 → 完成 → 驗證結果
  - failure-path：登入 → 操作失敗 → 驗證錯誤訊息與系統狀態

## 技術約束
- TODO: 測試需要知道的 infra 細節，例如：
  - Test env URL（UAT / staging / local）
  - Test user 帳密
  - Backend 特殊 header / cookie / query param
  - 固定隨機種子的方法（讓測試 deterministic）
"""


def load_context(section: str | None = None) -> dict:
    if not QA_KNOWLEDGE_PATH.is_file():
        # Fallback: serve universal QA patterns so a fresh install isn't useless.
        sections = _parse_sections(_BUILTIN_DEFAULTS)
        if section:
            s_low = section.lower()
            for name, content in sections.items():
                if name.lower() == s_low or s_low in name.lower():
                    return {
                        "path": str(QA_KNOWLEDGE_PATH),
                        "exists": False,
                        "using_builtin_defaults": True,
                        "section": name,
                        "content": content,
                        "hint": "這是內建 fallback。要專案專屬知識請執行 init_qa_knowledge。",
                    }
            return {
                "path": str(QA_KNOWLEDGE_PATH),
                "exists": False,
                "using_builtin_defaults": True,
                "section": section,
                "content": None,
                "available_sections": list(sections.keys()),
                "error": f"找不到 section: {section}（即使在 builtin defaults 中）",
            }
        return {
            "path": str(QA_KNOWLEDGE_PATH),
            "exists": False,
            "using_builtin_defaults": True,
            "hint": (
                f"未找到 {QA_KNOWLEDGE_PATH}，回傳內建通用 QA fallback。"
                "若要專案專屬知識，請執行 init_qa_knowledge tool。"
            ),
            "full_content": _BUILTIN_DEFAULTS,
            "sections": list(sections.keys()),
        }

    try:
        text = QA_KNOWLEDGE_PATH.read_text(encoding="utf-8")
    except OSError as e:
        return {
            "path": str(QA_KNOWLEDGE_PATH),
            "exists": False,
            "error": f"{type(e).__name__}: {e}",
        }

    sections = _parse_sections(text)
    if section:
        s_low = section.lower()
        for name, content in sections.items():
            if name.lower() == s_low or s_low in name.lower():
                return {
                    "path": str(QA_KNOWLEDGE_PATH),
                    "exists": True,
                    "section": name,
                    "content": content,
                }
        return {
            "path": str(QA_KNOWLEDGE_PATH),
            "exists": True,
            "section": section,
            "content": None,
            "available_sections": list(sections.keys()),
            "error": f"找不到 section: {section}",
        }
    return {
        "path": str(QA_KNOWLEDGE_PATH),
        "exists": True,
        "full_content": text,
        "sections": list(sections.keys()),
    }


def init_qa_knowledge(overwrite: bool = False) -> dict:
    """Scaffold a starter qa-knowledge.md at QA_KNOWLEDGE_PATH.

    Idempotent by default: refuses to overwrite an existing file unless
    overwrite=True. Returns the path + a hint pointing at what to edit next.
    """
    if QA_KNOWLEDGE_PATH.is_file() and not overwrite:
        try:
            existing_size = QA_KNOWLEDGE_PATH.stat().st_size
        except OSError:
            existing_size = -1
        return {
            "path": str(QA_KNOWLEDGE_PATH),
            "created": False,
            "existing_bytes": existing_size,
            "reason": "檔已存在；要覆蓋請傳 overwrite=true（建議先備份）",
        }
    try:
        QA_KNOWLEDGE_PATH.parent.mkdir(parents=True, exist_ok=True)
        content = _STARTER_TEMPLATE.format(project_name=QA_KNOWLEDGE_PATH.parent.name)
        QA_KNOWLEDGE_PATH.write_text(content, encoding="utf-8")
    except OSError as e:
        return {
            "path": str(QA_KNOWLEDGE_PATH),
            "created": False,
            "error": f"{type(e).__name__}: {e}",
        }
    return {
        "path": str(QA_KNOWLEDGE_PATH),
        "created": True,
        "bytes": len(content),
        "next_step": (
            "編輯這個檔案、把 TODO 換成你的業務規則 / 歷史 Bug / 標準斷言文字 / User Journeys。"
            "之後 get_qa_context 會直接讀你的版本（不再回 fallback）。"
        ),
    }


def _parse_sections(text: str) -> dict[str, str]:
    """Split markdown by ## H2 headers → {section_name: body_text}.

    Why H2-only: H1 is reserved for the document title, H3+ are nested detail.
    Treating H2 as the natural "topic" boundary matches how authors typically
    structure a knowledge doc.
    """
    sections: dict[str, str] = {}
    current_name: str | None = None
    current_lines: list[str] = []
    for line in text.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            if current_name is not None:
                sections[current_name] = "\n".join(current_lines).strip()
            current_name = m.group(1).strip()
            current_lines = []
        else:
            current_lines.append(line)
    if current_name is not None:
        sections[current_name] = "\n".join(current_lines).strip()
    return sections
