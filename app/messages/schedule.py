from datetime import datetime


def lessons_list_text(lessons, title: str):
    lines = [f"<b>{title}</b>"]
    if not lessons:
        lines.append("(нет)")
    for les in lessons:
        dt: datetime = les.time
        who = les.student.full_name if hasattr(les, "student") else "?"
        lines.append(f"• {dt.strftime('%d.%m %H:%M')} — {who}")
    return "\n".join(lines)