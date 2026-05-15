"""Read project-level QA knowledge so the MCP can escape monkey-testing.

Two-layer model:
  - Universal QA methodology (ISTQB principles, BBT design techniques, test
    pyramid, regression strategy, mobile checklist, QA metrics) is bundled
    as the built-in fallback. Applicable to any testing project.
  - Domain knowledge (business rules, regression points, exact assertion
    strings, user journeys, infra constraints) is per-project — users
    create qa-knowledge.md (via init_qa_knowledge or manually).

When the project file exists, we return *only* the user's content (their
file is the source of truth). When it doesn't, we return the built-in
methodology + TODO placeholders pointing at domain sections to fill.

Convention: the file uses H2 headers (## Section name) to delimit topics.
The client can fetch a single section by name (case-insensitive, partial
match) when it needs just one slice.
"""
import re

from ..config import QA_KNOWLEDGE_PATH


# Universal QA methodology — distilled from ISTQB / Google / mobile QA
# standards. Engine-agnostic, domain-agnostic. Bundled so any fresh install
# carries world-class testing thinking without the user writing anything.
_UNIVERSAL_METHODOLOGY = """## ISTQB 七大測試原則
| # | 原則 | 說明 | 實際應用 |
|---|------|------|---------|
| 1 | **測試證明缺陷的存在** | 測試能發現 Bug，但不能證明沒有 Bug | 不要因為測試通過就假設零缺陷 |
| 2 | **窮盡測試不可能** | 不可能測試所有組合 | 用風險和優先度聚焦，不追求 100% 覆蓋 |
| 3 | **早期測試** | 越早測試，修復成本越低 | Shift-Left：需求階段就開始審查 |
| 4 | **缺陷聚集** | 80% 的 Bug 集中在 20% 的模組 | 高風險模組優先測試（登入、支付、SSO） |
| 5 | **殺蟲劑悖論** | 同樣的測試反覆跑，找不到新 Bug | 定期更新測試用例 + 探索性測試 |
| 6 | **測試依賴上下文** | 不同系統需要不同測試策略 | 電商 App vs 社交 App 的測試重點不同 |
| 7 | **「零缺陷」謬誤** | 沒有 Bug 不代表系統符合需求 | 功能正確 ≠ 使用者滿意，還需可用性測試 |

## 測試設計技術 — 黑箱
### 等價分割 (Equivalence Partitioning)
- 將輸入分成等價類，每類取一個代表值
- 同一類中的值，系統行為相同
- 範例：訂購數量 1-10
  - 無效分區：≤ 0（測試 0）
  - 有效分區：1-10（測試 5）
  - 無效分區：≥ 11（測試 11）

### 邊界值分析 (Boundary Value Analysis)
- Bug 最容易發生在邊界上
- 測試點：最小值、最小+1、中間值、最大-1、最大值
- 範例：數量 1-10 → 測試 0, 1, 2, 5, 9, 10, 11
- 與等價分割搭配：EP 選分區代表，BVA 選邊界

### 決策表測試 (Decision Table Testing)
- 適用：多條件組合影響不同結果
- 建構：n 個條件 → 2^n 種組合
- 範例：登入畫面（帳號正確 × 密碼正確）

  | 帳號 | 密碼 | 結果 |
  |------|------|------|
  | ✗ | ✗ | 錯誤 |
  | ✓ | ✗ | 錯誤 |
  | ✗ | ✓ | 錯誤 |
  | ✓ | ✓ | 登入成功 |

### 狀態轉換測試 (State Transition Testing)
- 適用：系統行為依賴歷史狀態
- 四要素：狀態、轉換、事件、動作
- 範例：ATM 密碼（3 次機會）

  | 狀態 | 正確 PIN | 錯誤 PIN |
  |------|---------|---------|
  | 初始 | 進入 | 第 2 次 |
  | 第 2 次 | 進入 | 第 3 次 |
  | 第 3 次 | 進入 | 鎖定 |

## 測試金字塔 (Google 70/20/10)
```
        /‾‾‾‾‾‾\\
       / E2E 10% \\      少量、慢、脆弱、昂貴
      /────────────\\
     / Integration  \\    中量、驗證模組互動
    /   20%          \\
   /──────────────────\\
  /   Unit Tests 70%   \\  大量、快、穩定、便宜
 /────────────────────────\\
```

### 反模式
- **冰淇淋甜筒**（倒金字塔）：大量 E2E、少量 Unit → 慢、脆弱
- **沙漏型**：多 Unit + 多 E2E、少 Integration → 缺少模組間驗證

### 各層重點
| 層 | 速度 | 穩定性 | 測什麼 | 工具範例 |
|----|------|--------|--------|---------|
| Unit | 毫秒 | 高 | 單一邏輯、邊界、錯誤處理 | pytest / XCTest / JUnit |
| Integration | 秒 | 中 | 模組間互動、API、DB | requests-mock / URLProtocol |
| E2E | 分鐘 | 低 | 關鍵業務流程 | Playwright / Cypress / XCUITest |

### 關鍵原則
- 高層測試發現 Bug → 必須補一個低層測試
- 盡量將測試往金字塔底層推
- E2E 只測「能賺錢或會賠錢」的流程

## Shift-Left 測試
### 定義
將測試活動提前到開發生命週期早期，而非等開發完成後才測試。

### 四種類型
| 類型 | 說明 |
|------|------|
| 傳統型 | V-Model，強調 Unit + Integration |
| 增量型 | 分模組逐步測試 |
| Agile/DevOps 型 | 每個 Sprint 持續測試 |
| 基於模型型 | 需求階段就抓 Bug |

### 實踐方法
1. **靜態測試**：需求審查、設計審查（Code Review 也是）
2. **開發者自測**：寫完程式碼立即跑測試
3. **統一工具鏈**：開發和測試用同一套工具
4. **自動化 CI**：每次 commit 觸發測試
5. **持續回饋**：測試結果立即回饋給開發

### 成本效益
- 需求階段修 Bug：$1
- 開發階段修 Bug：$10
- 測試階段修 Bug：$100
- 上線後修 Bug：$1000

## 回歸測試策略
### 何時執行
- 新功能整合後
- Bug 修復後
- 需求變更後
- 效能優化後
- 外部系統整合後

### 測試用例選擇（優先度）
1. 歷史上缺陷頻發的功能
2. 使用者可見的功能
3. 核心業務功能
4. 最近修改的區域
5. 所有整合測試
6. 複雜和邊界值測試

### 類型
| 類型 | 範圍 | 適用 |
|------|------|------|
| Unit | 只測修改部分 | 小改動 |
| Regional | 修改 + 依賴模組 | 模組改動 |
| Full | 全部重測 | 大改版 / Release |
| Selective | 影響範圍子集 | 時間有限 |

### 最佳實踐
- 盡可能自動化
- 整合到 CI/CD pipeline
- 每次 code change 後執行
- 維持一致的測試環境
- 使用隔離、可重現的測試資料

## Mobile App 測試 Checklist
### 功能測試
- [ ] 必填欄位有明顯區分
- [ ] App 啟動/停止正常
- [ ] 來電時 App 行為正確
- [ ] 收 SMS 時不影響 App
- [ ] 多工切換正常
- [ ] 社群分享功能正常
- [ ] 支付閘道正常（Visa/MC/Apple Pay）
- [ ] 網路失敗有適當錯誤訊息
- [ ] 系統 crash 後 App 能恢復
- [ ] 安裝/更新流程無重大錯誤

### 效能測試
- [ ] 不同負載下回應時間可接受
- [ ] 峰值使用者數量下網路覆蓋充足
- [ ] 電池續航在預期負載下足夠
- [ ] WiFi ↔ 4G/5G 切換不影響功能
- [ ] CPU 使用率優化
- [ ] 記憶體洩漏檢測
- [ ] GPS/Camera 等資源使用合理
- [ ] 長時間使用穩定性

### 安全測試
- [ ] 暴力破解防護
- [ ] 敏感內容需認證
- [ ] 強密碼策略
- [ ] Session 過期時間合理
- [ ] SQL Injection 防護
- [ ] SSL/TLS 憑證驗證
- [ ] 加密存儲（Keychain / Encrypted SharedPreferences）
- [ ] 鍵盤快取安全
- [ ] Cookie 安全

### 可用性測試
- [ ] 按鈕大小適合觸控（最小 44pt）
- [ ] 按鈕位置一致不混淆
- [ ] 圖示直覺一致
- [ ] 相同功能顏色一致
- [ ] 有縮放功能
- [ ] 盡量減少鍵盤輸入
- [ ] 有返回/取消功能
- [ ] 文字大小可讀
- [ ] 大量下載有提示
- [ ] 關閉 App 後狀態保留

### 相容性 / 中斷 / 恢復
- [ ] 不同螢幕尺寸 UI 適應
- [ ] 文字不被截斷
- [ ] 不同 OS 版本可運行
- [ ] 網路中斷後鍵盤輸入不中斷
- [ ] 充電時背景 App 效能正常
- [ ] 低電量 + 高負載組合
- [ ] Crash 後資料完整性
- [ ] 連線中斷後資料恢復
- [ ] 解除安裝後無殘留檔案

## RWD 響應式測試（Web）
### 標準 breakpoints（常用、依專案 design system 調整）
| 區段 | 寬度 (px) | 代表裝置 |
|------|----------|---------|
| Mobile XS | 320–374 | iPhone SE 1st gen / 早期 Android |
| Mobile | 375–413 | iPhone SE 3rd / iPhone 13–16 mini |
| Mobile L | 414–767 | iPhone Pro Max / Plus 系列 |
| Tablet | 768–1023 | iPad 直向 |
| Tablet L | 1024–1279 | iPad 橫向 / iPad Pro |
| Desktop | 1280–1919 | 標準 laptop / 外接螢幕 |
| Desktop XL | ≥ 1920 | 大型外接螢幕 / 4K |

### 必測軸
- **排版不破**：text 不溢出 / 不截斷 / layout 不重疊
- **互動切換**：mobile 用 hamburger + tap；desktop 用 hover + mouse；過渡區（tablet）兩者皆要可用
- **觸控目標**：mobile 點擊區 ≥ 44×44pt（WCAG 2.5.5 / Apple HIG）
- **媒體切換**：`<picture srcset>` / `image-set()` 在不同 DPR 載對解析度
- **字體可讀**：base font-size ≥ 14px；行高 1.4–1.6；尺寸用 rem/em（隨用戶縮放）
- **鍵盤導航**：tab order 不因 RWD 重排而錯亂

### 常見 RWD bug 模式（每條都該寫 TC）
- **px 寫死**：用戶字體放大或縮放時 layout 爆掉
- **Hover only 互動**：mobile 無 hover、卡在 hover 後狀態不會觸發
- **隱藏不卸載**：`display:none` 但 DOM 還在 → selector 抓到不可見元素誤判
- **viewport meta 缺失**：手機看像縮小桌面版、不會 reflow
- **過渡區未測試**：768–1023 排版錯位（mobile / desktop 都正常、tablet 破）
- **Image 不 lazy / srcset 缺失**：mobile 下載桌面大圖、流量爆 + 載入慢

### 測試策略
- **E2E**：核心流程選 3 個代表 viewport（mobile 375 / tablet 768 / desktop 1280）各跑一次
- **視覺回歸**：用 visual diff 工具（Percy / Chromatic）跨 viewport 比對
- **邊界值思維**：breakpoint ± 1px（如 767 vs 768）應一致或明確切換，不能曖昧
- **真機優先**：CSS emulator ≠ 真實 mobile（iOS Safari bottom bar / 安卓 IME 高度 / 動態島）

## 測試類型總覽
| 類型 | 定義 | 自動化 | 頻率 |
|------|------|--------|------|
| **冒煙測試** | 建構後快速驗證核心功能 | 應自動化 | 每次 Build |
| **回歸測試** | 確認改動沒破壞既有功能 | 應自動化 | 每次 Change |
| **功能測試** | 驗證業務需求 | 部分自動化 | 每次 Feature |
| **整合測試** | 驗證模組間互動 | 應自動化 | 每次整合 |
| **E2E 測試** | 完整使用者旅程 | 關鍵路徑自動化 | Release 前 |
| **效能測試** | 負載、回應時間、資源 | 工具輔助 | 定期 |
| **安全測試** | 漏洞、認證、加密 | 部分工具 | Release 前 |
| **探索性測試** | 無腳本、經驗導向 | 手動 | 新功能 |
| **驗收測試** | 符合業務標準 | 部分 | Release 前 |

## QA Metrics
| 指標 | 公式 | 目標 |
|------|------|------|
| **執行率** | (Pass + Fail) / Total | > 95% |
| **通過率** | Pass / (Pass + Fail) | > 95% |
| **缺陷密度** | Bug 數 / KLOC | 越低越好 |
| **缺陷移除效率** | 上線前 Bug / 總 Bug | > 90% |
| **回歸通過率** | 回歸 Pass / 回歸 Total | > 98% |
| **自動化覆蓋** | 自動化 TC / 總 TC | 依層級 |
| **平均修復時間** | 修復完成 - Bug 提交 | 越短越好 |
"""


# Per-project knowledge slots — methodology covers the "how to test",
# these cover the "what's specific about THIS product". Both layers are
# needed to escape monkey-testing.
_DOMAIN_TODO_SECTIONS = """## 你的業務規則
- TODO: 領域邏輯 / 折扣計算 / 限購規則 / 會員等級 / 優惠券規範 ...

## 你的歷史 Bug / 回歸點
- TODO: 已修 ticket ID + 簡述 + 期望行為 + 觸發條件 + fix reference

## 你的標準斷言文字
- TODO: UI 標準文案精確字元 / 錯誤訊息 / 成功提示 / CTA 標籤

## 你的 User Journeys
- TODO: 多步驟業務流程：登入 → 主要操作 → 完成 → 驗證結果

## 你的技術約束
- TODO: Test env URL / Test user / 必要 header / 固定隨機種子方法
"""


_BUILTIN_DEFAULTS = (
    "# QA Knowledge — Universal Testing Methodology (built-in fallback)\n\n"
    "> 這份是 mk-qa-master 內建的通用 QA 方法論，整理自 ISTQB / Google 測試金字塔 / 業界 mobile QA 標準。\n"
    "> 任何測試專案都可以套用 — **但缺少你的領域知識**（業務規則 / 回歸點 / 標準文案）\n"
    "> 就還是會偏向 monkey testing。解法：執行 init_qa_knowledge tool，\n"
    "> 在受測專案根產生 qa-knowledge.md、把下面五個「你的 XXX」TODO 區段填上你的領域內容。\n\n"
    + _UNIVERSAL_METHODOLOGY
    + "\n"
    + _DOMAIN_TODO_SECTIONS
)


_STARTER_TEMPLATE = (
    "# QA Knowledge — {project_name}\n\n"
    "> 給 mk-qa-master 讀的領域知識。get_qa_context() 會把這份內容暴露給 AI，\n"
    "> 用於決定要測什麼 + 把規則印進產出 test 的 `# Business context:` 區段。\n"
    "> **規則**：以 H2 (`##`) 區段為單位、client 可指定 section 拉取單一段（partial match）。\n\n"
    "> ---\n"
    "> **上半部「通用測試方法論」**（ISTQB / 邊界值 / 測試金字塔 / 回歸策略 / Mobile checklist / QA metrics）\n"
    "> 由 mk-qa-master 預載。一般不需要動；場景不適用某些方法論可以刪除對應 H2 段落。\n"
    "> \n"
    "> **下半部「你的 XXX」TODO 區段**才是讓測試脫離 monkey 等級的關鍵 — 請填入你的領域業務規則。\n"
    "> ---\n\n"
    + _UNIVERSAL_METHODOLOGY
    + "\n"
    + _DOMAIN_TODO_SECTIONS
)


def load_context(section: str | None = None) -> dict:
    if not QA_KNOWLEDGE_PATH.is_file():
        # Fallback: serve universal methodology so a fresh install isn't useless.
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
                f"未找到 {QA_KNOWLEDGE_PATH}，回傳內建通用 QA 方法論。"
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
    overwrite=True. The starter bundles the universal methodology plus
    empty TODO domain sections, so the user has reference material and
    edit targets in one file.
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
            "編輯這個檔案、把「你的 XXX」TODO 區段換成你的業務規則 / 歷史 Bug / "
            "標準斷言文字 / User Journeys / 技術約束。"
            "之後 get_qa_context 會直接讀你的版本（不再回 fallback）。"
        ),
    }


def _parse_sections(text: str) -> dict[str, str]:
    """Split markdown by ## H2 headers → {section_name: body_text}.

    Why H2-only: H1 is reserved for the document title, H3+ are nested
    detail (subsections within a topic). Treating H2 as the natural "topic"
    boundary matches how authors typically structure a knowledge doc.
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
