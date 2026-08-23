"""Turning a validated summary into a YouTube video description.

Deterministic, like `render.summary` — built from the schema's own fields
rather than guessed back out of rendered prose. The original system's
`push_youtube.py` extracted its four sections (topics / progress / homework /
tips) with regexes run against already-formatted Markdown; a summary whose
headings drifted even slightly (a typo, a translated label) fell out of every
pattern silently. Reading the validated fields directly cannot miss a section
that is actually there.
"""

from __future__ import annotations

from typing import Any

#: The studio's own voice, carried over from `push_youtube.py` verbatim —
#: viewers and parents already know this signature.
_FOOTER = (
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "✨ สรุปการสอนโดยผู้ช่วยตัวน้อย หางยาว (Created by Chunny)\n"
    "ข้อมูลอาจมีตกหล่นหรือคลาดเคลื่อนบ้าง แต่น้องหางยาวกำลังพยายามพัฒนาตัวเองให้เก่งขึ้นอยู่ทุกวัน!\n"
    "หากมีส่วนไหนผิดพลาดไป ต้องขออภัยด้วยนะเมี๊ยว~ 🐈🎀"
)


def format_description(
    summary: dict[str, Any],
    *,
    instrument: str = "",
    week: int | str = "",
    student_name: str = "",
    date: str = "",
) -> str:
    """Render one lesson's YouTube description.

    Args:
        summary: A validated ``lesson_summary`` structure — the same one
            `render.summary.to_markdown` renders to the document.
        instrument, week, student_name, date: Header fields Baton supplies
            from its own records, not from the model-written summary.
    """
    lines = [f"🎵 บทเรียน {instrument} ประจำสัปดาห์ที่ {week}".strip()]
    if student_name:
        lines.append(f"👤 นักเรียน: {student_name}")
    if date:
        lines.append(f"📅 วันที่: {date}")
    lines.append("━━━━━━━━━━━━━━━━━━")

    overview = summary.get("overview") or []
    if overview:
        lines.append("")
        lines.append("✅ ความคืบหน้า:")
        lines.extend(f"- {item}" for item in overview)

    covered = summary.get("covered") or []
    if covered:
        lines.append("")
        lines.append("📚 สิ่งที่เรียน:")
        for entry in covered:
            topic = entry.get("topic", "")
            detail = entry.get("detail", "")
            lines.append(f"- {topic} — {detail}" if detail else f"- {topic}")

    goals = summary.get("goals") or []
    if goals:
        lines.append("")
        lines.append("📝 การบ้าน:")
        lines.extend(f"- {goal}" for goal in goals)

    focus = summary.get("focus") or []
    if focus:
        lines.append("")
        lines.append("💡 คำแนะนำการฝึก:")
        lines.extend(f"- {item['issue']} → {item['fix']}" for item in focus)

    lines.append("")
    lines.append(_FOOTER)
    return "\n".join(lines)
