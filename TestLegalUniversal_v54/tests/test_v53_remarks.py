"""v53 — две правки по выгрузке «Постановление для 53 версии».

1. **Папка обращения.** Раньше постановления лежали прямо в корне:
   `postanovleniya/AG-1/этап-1 — принятие/…`, и по папке нельзя было понять,
   к какому обращению относится документ. Теперь корень дела — папка
   «Обращение №N», а внутри неё постановления по этапам:

       postanovleniya/
         Обращение №1/
           cases/1/case.json
           AG-1/этап-1 — принятие/…
           AG-2/этап-2 — санкция/…

2. **Адреса в тексте постановления.** В выгрузке «Постановление для 53
   версии» в «УСТАНОВИЛ» печатались сами ссылки: «Запись заседания Сената от
   09.11.2026: https://www.youtube.com/watch?v=…», «docs.google.com/…»,
   «imageban.ru/…». В официальном документе это недопустимо: вместо адреса —
   название материала и «приложено к обращению».

Правка сделана в двух местах: промт (`promt 1.30.txt`, п. 8.1.1.1) и код
(`strip_urls`), потому что промт ИИ выполняет не всегда, а документ печатается
из его ответа.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPORT = ROOT.parent / "Постановление для 53 версии"
PROMPT = ROOT.parent / "Для новой версии" / "Текущий промт" / "promt 1.30.txt"


def _code(text: str) -> str:
    out = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        out.append(line.split("  #")[0])
    return "\n".join(out)


class V53AppealFolderTests(unittest.TestCase):
    """Папка «Обращение №N» и вложенные в неё AG-1/AG-2."""

    def setUp(self):
        import gov_case as cases
        import postanovlenie_data as pdoc
        self.cases = cases
        self.pdoc = pdoc

    def test_folder_name_is_obraschenie_n(self):
        self.assertEqual(self.pdoc.appeal_folder_name("1"), "Обращение №1")
        self.assertEqual(self.pdoc.appeal_folder_name(""), "Обращение №без-номера")
        # уже готовое имя не удваивается
        self.assertEqual(self.pdoc.appeal_folder_name("Обращение №2"),
                         "Обращение №2")

    def test_document_folder_is_inside_appeal_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = self.pdoc.doc_folder(tmp, "AG-1", "1")
            self.assertEqual(folder.name, "AG-1")
            self.assertEqual(folder.parent.name, "Обращение №1")
            self.assertTrue(folder.is_dir())

    def test_stage_folder_is_inside_appeal_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = self.cases.stage_folder(tmp, "AG-2", 2,
                                             self.cases.STAGE_MEASURE,
                                             case_number="1")
            self.assertEqual(folder.name, "этап-2 — санкция")
            self.assertEqual(folder.parent.name, "AG-2")
            self.assertEqual(folder.parent.parent.name, "Обращение №1")
            self.assertTrue(folder.is_dir())

    def test_two_stages_of_one_appeal_live_side_by_side(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = self.cases.stage_folder(tmp, "AG-1", 1,
                                            self.cases.STAGE_INTAKE, case_number="1")
            second = self.cases.stage_folder(tmp, "AG-2", 2,
                                             self.cases.STAGE_MEASURE, case_number="1")
            self.assertNotEqual(first, second)
            self.assertEqual(first.parent.parent, second.parent.parent)
            self.assertEqual(first.parent.parent.name, "Обращение №1")

    def test_appeal_folder_is_created_with_the_case(self):
        """Папка обращения появляется сразу при заведении дела (v53)."""
        import gov_case as cases
        with tempfile.TemporaryDirectory() as tmp:
            store = cases.GovCaseStore(Path(tmp))
            case = store.open_or_create("обращение гражданина")
            folder = Path(tmp) / "Обращение №1"
            self.assertTrue(folder.is_dir())
            self.assertEqual(folder.name, self.pdoc.appeal_folder_name(case.number))

    def test_documents_without_a_case_stay_flat(self):
        """Документ без дела и без номера IAD печатается прямо в корне.

        v54: номер вида «LSCSD-IAD-N» теперь лежит в своей папке
        «Постановление №N» (см. `tests/test_v54_remarks.py`), поэтому плоским
        остаётся только документ с произвольным номером.
        """
        with tempfile.TemporaryDirectory() as tmp:
            folder = self.pdoc.doc_folder(tmp, "постановление-1")
            self.assertEqual(folder.parent, Path(tmp))

    def test_main_and_form_pass_the_case_number(self):
        main = _code((ROOT / "main.py").read_text(encoding="utf-8"))
        self.assertIn("case_number=case.number)", main)
        ui = _code((ROOT / "postanovlenie_prokuror_ui.py").read_text(encoding="utf-8"))
        self.assertIn("case_number=self.case.number", ui)


class V53UrlTests(unittest.TestCase):
    """Адреса в тексте заменяются ссылкой на приложенный материал."""

    def setUp(self):
        import postanovlenie_prokuror_data as g
        self.g = g

    def test_has_url(self):
        self.assertTrue(self.g.has_url("см. https://a.ru/1"))
        self.assertTrue(self.g.has_url("www.example.gov"))
        self.assertFalse(self.g.has_url("тайм-коды 0:14 и 1:15"))

    def test_material_name_is_kept_instead_of_address(self):
        text = ("В подтверждение представлено: Запись заседания Сената от "
                "09.11.2026: https://www.youtube.com/watch?v=FhIWsJXvVP4")
        cleaned = self.g.strip_urls(text)
        self.assertNotIn("http", cleaned)
        self.assertIn("Запись заседания Сената от 09.11.2026", cleaned)
        self.assertIn("приложено к обращению", cleaned)

    def test_address_without_a_name(self):
        cleaned = self.g.strip_urls("В подтверждение представлено: https://disk.yandex.ru/i/x")
        self.assertNotIn("http", cleaned)
        self.assertIn("материалы приложены к обращению", cleaned)

    def test_address_in_brackets(self):
        cleaned = self.g.strip_urls("Копия ID (https://imageban.ru/x.png) приложена.")
        self.assertNotIn("http", cleaned)
        self.assertIn("приложено к обращению", cleaned)

    def test_several_addresses_become_one_mention(self):
        cleaned = self.g.strip_urls("Материалы: https://a.ru/1, https://b.ru/2 и https://c.ru/3.")
        self.assertNotIn("http", cleaned)
        self.assertEqual(cleaned.lower().count("приложен"), 1, cleaned)

    def test_timecodes_and_numbers_survive(self):
        text = "Тайм-коды: 0:14 — представление, 1:00 — отказ, 1:15 — отстранение."
        self.assertEqual(self.g.strip_urls(text), text)

    def test_plain_text_is_untouched(self):
        text = "Сотрудник отказал в рассмотрении инициативы сенатора."
        self.assertEqual(self.g.strip_urls(text), text)

    def test_facts_text_has_no_urls(self):
        doc = self.g.PostanovlenieProkurora(
            doc_type="О принятии обращения к собственному производству",
            established=("Обращение поступило. В подтверждение представлено: "
                         "Запись заседания от 09.11.2026: "
                         "https://www.youtube.com/watch?v=FhIWsJXvVP4 "
                         "Тайм-коды: 0:14 — представление инициативы."))
        facts = doc.facts_text()
        self.assertNotIn("http", facts)
        self.assertIn("Запись заседания от 09.11.2026", facts)
        self.assertIn("0:14", facts)

    def test_decision_points_have_no_urls(self):
        doc = self.g.PostanovlenieProkurora(doc_type="О принятии обращения")
        doc.points = ["Приложить материалы: ссылка: https://a.ru/1."]
        points = doc.decision_points()
        self.assertFalse(any("http" in point for point in points), points)

    def test_real_export_ag1_facts(self):
        """Эталон из выгрузки: в «УСТАНОВИЛ» адресов больше нет."""
        fields_path = EXPORT / "AG-1" / "этап-1 — принятие" / "ИИ-поля.json"
        if not fields_path.exists():
            self.skipTest("выгрузка отсутствует")
        data = json.loads(fields_path.read_text(encoding="utf-8"))
        doc = self.g.PostanovlenieProkurora.from_dict(data.get("поля", data))
        facts = doc.facts_text()
        self.assertNotIn("http", facts)
        self.assertNotIn("www.", facts)
        self.assertIn("Запись заседания Сената от 09.11.2026", facts)
        self.assertIn("0:14", facts)


class V53PromptTests(unittest.TestCase):
    """Промт 1.29 запрещает печатать адреса."""

    def setUp(self):
        if not PROMPT.exists():
            self.skipTest("промт 1.29 отсутствует")
        self.text = PROMPT.read_text(encoding="utf-8")

    def test_prompt_forbids_urls(self):
        self.assertIn("НИКАКИХ ССЫЛОК В ТЕКСТЕ ПОСТАНОВЛЕНИЯ", self.text)
        self.assertIn("https://…", self.text)

    def test_prompt_tells_how_to_refer_to_materials(self):
        self.assertIn("приложено к обращению", self.text)
        self.assertIn("тайм-коды", self.text.lower())

    def test_prompt_keeps_previous_rules(self):
        self.assertIn("===ПРОВЕРКА ОБРАЩЕНИЯ===", self.text)
        self.assertIn("руководствуясь <НОРМЫ>", self.text)
        self.assertNotIn("замечание v", self.text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
