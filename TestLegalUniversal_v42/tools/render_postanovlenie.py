#!/usr/bin/env python3
"""render_postanovlenie.py — собрать PNG-постановление без запуска клиента.

Зачем
-----
Иногда нужно просто проверить бланк: шрифт, отступы, печать, подпись,
многостраничность. Открывать для этого весь клиент с браузером не нужно.

Примеры
-------
    python tools/render_postanovlenie.py --demo
    python tools/render_postanovlenie.py --number LSCSD-IAD-141 --subject "Qorick Mythic"

Результат складывается в `postanovleniya/png/` рядом с `main.py`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import doc_render as render          # noqa: E402
import postanovlenie_data as pdoc    # noqa: E402


def build_demo() -> pdoc.Postanovlenie:
    reg = pdoc.Regulation(pdoc.default_regulation_path(ROOT / "data"))
    penalties = reg.penalties() or pdoc.PENALTIES
    return pdoc.Postanovlenie(
        doc_number="LSCSD-IAD-141",
        doc_date=pdoc.today_ru(),
        author_name="Kirill Goldens",
        author_rank="Major — Начальник отдела внутренних расследований",
        subject_name="Qorick Mythic",
        subject_id="166759",
        subject_rank="Cadet",
        subject_department="Patrol",
        violation_act="Внутреннего устава Los Santos County Sheriff Department",
        violation_clause="пункт 3.1 устава",
        penalty=penalties[2] if len(penalties) > 2 else "строгий выговор",
        situation="Сотрудник вынес дисциплинарное взыскание сотруднику, "
                  "находящемуся выше по званию, чем превысил свои полномочия.",
        evidence="Рапорт supervisor, выписка из журнала наказаний.",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Рендер постановления в PNG (A4)")
    parser.add_argument("--demo", action="store_true", help="собрать демонстрационный документ")
    parser.add_argument("--number", default="LSCSD-IAD-141", help="номер документа")
    parser.add_argument("--subject", default="Qorick Mythic", help="в отношении кого")
    parser.add_argument("--clause", default="пункт 3.1 устава", help="пункт нарушения")
    parser.add_argument("--out", default=str(ROOT / "postanovleniya"), help="куда сохранять")
    args = parser.parse_args()

    doc = build_demo()
    doc.doc_number = args.number
    doc.subject_name = args.subject
    doc.violation_clause = args.clause
    if not args.demo:
        missing = doc.missing_fields()
        if missing:
            print("Не хватает полей: " + ", ".join(missing))
    assets = render.DocAssets(ROOT)
    pages = render.render_document(doc, assets)
    paths = render.save_pages(pages, Path(args.out), doc.doc_number or "postanovlenie")
    print("Реквизиты:", assets.describe())
    print(f"Страниц: {len(pages)}")
    for path in paths:
        print(" ", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
