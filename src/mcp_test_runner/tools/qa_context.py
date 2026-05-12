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
"""
import re

from ..config import QA_KNOWLEDGE_PATH


def load_context(section: str | None = None) -> dict:
    if not QA_KNOWLEDGE_PATH.is_file():
        return {
            "path": str(QA_KNOWLEDGE_PATH),
            "exists": False,
            "hint": (
                f"未找到 qa-knowledge 檔。建議在 {QA_KNOWLEDGE_PATH} 建立 markdown，"
                "包含 ## 業務規則 / ## 歷史 Bug / ## 標準斷言文字 / ## User Journeys 等 H2 區段。"
            ),
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
        # Case-insensitive, allow partial match. Returns single section.
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
