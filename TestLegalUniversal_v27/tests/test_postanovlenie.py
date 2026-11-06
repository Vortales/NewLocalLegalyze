"""Тесты постановлений v27: разбор ответа ИИ, данные, история и PNG-бланк.

Проверяем ровно то, на чём держится новая функция:

* триггеры (`===ПОСТАНОВЛЕНИЕ===`, `===КОНЕЦ ОТВЕТА===`) распознаются;
* поля из ответа ИИ попадают в документ, пункты собираются по номерам;
* без триггера и без полей разбор ничего не «выдумывает»;
* документ всегда отдаёт осмысленный .txt для чата и подпись «И. Фамилия»;
* история хранит полный снимок (открыть старое заполнение и поправить);
* PNG-рендер даёт A4 и умеет разбивать длинный документ на страницы.
"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import postanovlenie_data as pdoc  # noqa: E402

AI_ANSWER = """===ПОСТАНОВЛЕНИЕ===
Ведомство: LOS SANTOS COUNTY SHERIFF'S DEPARTMENT
Подразделение: INTERNAL AFFAIRS DIVISION
Вид документа: Постановление о выдаче дисциплинарного взыскания
Заголовок: о выдаче дисциплинарного взыскания
Номер: LSCSD-IAD-141
Дата: 06.11.2026
Постановление вынес: Kirill Goldens
Должность: Major (Майор IAD)
В отношении: Qorick Mythic
Удостоверение: 166759
Звание сотрудника: Cadet
Отдел сотрудника: Patrol
Нарушенный акт: Внутреннего устава Los Santos County Sheriff Department
Пункт нарушения: пункт 19) части 1 статьи а
Взыскание: строгий выговор
Ситуация: Сотрудник оставил пост без разрешения.
Доказательства: Рапорт supervisor.
Постановляющая часть:
1. Назначить сотруднику Qorick Mythic взыскание в виде строгого выговора.
2. Постановление вступает в силу с момента подписания.
3. Контроль оставляю за собой.
===КОНЕЦ ПОСТАНОВЛЕНИЯ===
===КОНЕЦ ОТВЕТА===
"""


class TriggerTests(unittest.TestCase):
    def test_trigger_is_detected_in_any_spacing(self):
        for text in ("===ПОСТАНОВЛЕНИЕ===", "=== ПОСТАНОВЛЕНИЕ ===",
                     "постановление:", "Конец: ===КОНЕЦ ПОСТАНОВЛЕНИЯ==="):
            self.assertTrue(pdoc.has_document_trigger(text), text)

    def test_answer_end_is_detected_like_v26_client(self):
        self.assertTrue(pdoc.has_answer_end("текст\n===КОНЕЦ ОТВЕТА==="))
        self.assertTrue(pdoc.has_answer_end("текст\n=== КОНЕЦ ОТВЕТА ==="))
        self.assertFalse(pdoc.has_answer_end("текст без маркера"))

    def test_triggers_are_removed_from_human_readable_text(self):
        clean = pdoc.strip_triggers("Привет\n===ПОСТАНОВЛЕНИЕ===\nТело\n===КОНЕЦ ОТВЕТА===")
        self.assertNotIn("===", clean)
        self.assertIn("Тело", clean)


class ParseTests(unittest.TestCase):
    def test_fields_and_points_are_parsed(self):
        doc = pdoc.parse_ai_answer(AI_ANSWER)
        self.assertIsNotNone(doc)
        self.assertEqual(doc.get("doc_number"), "LSCSD-IAD-141")
        self.assertEqual(doc.get("doc_date"), "06.11.2026")
        self.assertEqual(doc.get("subject_name"), "Qorick Mythic")
        self.assertEqual(doc.get("subject_id"), "166759")
        self.assertEqual(doc.get("penalty"), "строгий выговор")
        self.assertEqual(len(doc.points), 3)
        self.assertIn("строгого выговора", doc.points[0])
        self.assertEqual(doc.source, "ai")

    def test_base_document_fills_the_gaps(self):
        base = pdoc.Postanovlenie(doc_number="LSCSD-IAD-150", author_name="Kirill Goldens")
        doc = pdoc.parse_ai_answer("===ПОСТАНОВЛЕНИЕ===\nВ отношении: Qorick Mythic\n",
                                   base=base)
        self.assertEqual(doc.get("doc_number"), "LSCSD-IAD-150")
        self.assertEqual(doc.get("subject_name"), "Qorick Mythic")

    def test_plain_chat_without_trigger_gives_nothing(self):
        self.assertIsNone(pdoc.parse_ai_answer("Обычный правовой ответ без документа."))
        self.assertIsNone(pdoc.parse_ai_answer(""))

    def test_answer_without_points_gets_points_from_the_charter(self):
        doc = pdoc.parse_ai_answer("===ПОСТАНОВЛЕНИЕ===\nВ отношении: Qorick Mythic\n"
                                   "Взыскание: строгий выговор\n")
        points = doc.decision_points()
        self.assertEqual(len(points), 3)
        self.assertIn("строгого выговора", points[0])

    def test_free_text_answer_keeps_the_story(self):
        doc = pdoc.parse_ai_answer(
            "===ПОСТАНОВЛЕНИЕ===\nНомер: LSCSD-IAD-9\n"
            "Сотрудник отсутствовал на построении.\n")
        self.assertIn("отсутствовал", doc.get("situation"))


class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.doc = pdoc.Postanovlenie(
            doc_number="LSCSD-IAD-141",
            doc_date="06.11.2026",
            author_name="Kirill Goldens",
            author_rank="Major (Майор IAD)",
            subject_name="Qorick Mythic",
            subject_id="166759",
            violation_act="Внутреннего устава Los Santos County Sheriff Department",
            violation_clause="пункт 19) части 1 статьи а",
            penalty="строгий выговор",
        )

    def test_signature_is_initial_plus_surname(self):
        self.assertEqual(self.doc.signature(), "K. Goldens")
        self.assertEqual(pdoc.initials_signature("kirill goldens"), "K. Goldens")
        self.assertEqual(pdoc.initials_signature("Qorick"), "Qorick")
        self.assertEqual(pdoc.initials_signature(""), "")

    def test_genitive_forms_are_used_in_the_resolution(self):
        self.assertEqual(pdoc.penalty_instrumental("строгий выговор"), "строгого выговора")
        self.assertEqual(pdoc.penalty_instrumental("увольнение"), "увольнения")
        self.assertEqual(pdoc.clause_genitive("пункт 19) части 1 статьи а"),
                         "пункта 19) части 1 статьи а")
        self.assertIn("в виде строгого выговора", self.doc.decision_points()[0])

    def test_missing_fields_are_named_for_the_user(self):
        empty = pdoc.Postanovlenie()
        missing = empty.missing_fields()
        self.assertIn("Номер документа", missing)
        self.assertEqual(self.doc.missing_fields(), [])

    def test_chat_txt_is_structured_and_named(self):
        text = self.doc.to_chat_txt()
        self.assertTrue(text.startswith(pdoc.DOC_MARKER_NAME))
        self.assertIn("Номер: LSCSD-IAD-141", text)
        self.assertIn("Постановляющая часть:", text)
        self.assertIn(pdoc.TRIGGER_ANSWER_END, text)
        self.assertNotIn("===ПОСТАНОВЛЕНИЕ===", text)  # маркер выдаёт только ИИ
        self.assertEqual(self.doc.chat_txt_name(), "ПОСТАНОВЛЕНИЕ LSCSD-IAD-141.txt")

    def test_chat_txt_has_no_duplicated_fields(self):
        text = self.doc.to_chat_txt()
        self.assertEqual(text.count("Вид документа:"), 1)

    def test_auto_number_follows_history(self):
        self.assertEqual(self.doc.auto_number([]), "LSCSD-IAD-1")
        self.assertEqual(
            self.doc.auto_number(["LSCSD-IAD-7", "LSCSD-IAD-140"], prefix="LSCSD-IAD"),
            "LSCSD-IAD-141")

    def test_round_trip_through_dict(self):
        clone = pdoc.Postanovlenie.from_dict(self.doc.to_dict())
        for key, _form, _label in pdoc.DOC_FIELDS:
            self.assertEqual(clone.get(key), self.doc.get(key), key)


class HistoryTests(unittest.TestCase):
    def test_history_keeps_full_snapshot_for_editing(self):
        with tempfile.TemporaryDirectory() as tmp:
            history = pdoc.DocumentHistory(Path(tmp))
            doc = pdoc.Postanovlenie(doc_number="LSCSD-IAD-1", subject_name="Qorick Mythic",
                                     situation="Первая редакция")
            history.save(doc)
            doc.situation = "Вторая редакция"
            history.save(doc)

            self.assertEqual(len(history.items()), 2)
            self.assertEqual(len(history.labels()), 2)
            self.assertEqual(history.used_numbers(), ["LSCSD-IAD-1", "LSCSD-IAD-1"])

            loaded = history.load_index(0)
            self.assertIsNotNone(loaded)
            self.assertIn(loaded.get("situation"),
                          {"Первая редакция", "Вторая редакция"})
            self.assertTrue(history.labels()[0].endswith("LSCSD-IAD-1 · Qorick Mythic"))

    def test_broken_history_file_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "20260101-000000.json").write_text("{битый", encoding="utf-8")
            history = pdoc.DocumentHistory(folder)
            self.assertEqual(history.items(), [])


class RegulationTests(unittest.TestCase):
    def test_charter_penalties_match_the_list_of_the_ustav(self):
        reg = pdoc.Regulation(pdoc.default_regulation_path(ROOT / "data"))
        self.assertTrue(reg.title)
        penalties = [p.lower() for p in reg.penalties()]
        for expected in ("устный выговор", "строгий выговор", "увольнение"):
            self.assertIn(expected, penalties)

    def test_unknown_clause_is_not_invented(self):
        reg = pdoc.Regulation(pdoc.default_regulation_path(ROOT / "data"))
        self.assertEqual(reg.clause_text("пункт 9999) части 1 статьи zz"), "")

    def test_clause_is_found_in_the_real_ustav_format(self):
        reg = pdoc.Regulation(pdoc.default_regulation_path(ROOT / "data"))
        found = reg.clause_text("пункт 3.1 устава")
        self.assertTrue(found.startswith("3.1"), found[:40])
        self.assertIn("дисциплинарное взыскание", found.lower())
        self.assertTrue(reg.clause_text("4.1").startswith("4.1"))


class RenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import doc_render
        except Exception as exc:                       # pragma: no cover
            raise unittest.SkipTest("Pillow недоступен: %s" % exc)
        cls.render = doc_render

    def _doc(self, points=None):
        return pdoc.Postanovlenie(
            doc_number="LSCSD-IAD-141",
            doc_date="06.11.2026",
            author_name="Kirill Goldens",
            author_rank="Major (Майор IAD)",
            subject_name="Qorick Mythic",
            subject_id="166759",
            violation_act="Внутреннего устава Los Santos County Sheriff Department",
            violation_clause="пункт 19) части 1 статьи а",
            penalty="строгий выговор",
            situation="Сотрудник оставил пост без разрешения.",
            points=list(points or []),
        )

    def test_single_page_is_a4_at_200_dpi(self):
        pages = self.render.render_document(self._doc())
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0].size, (self.render.PAGE_W, self.render.PAGE_H))
        # 200 dpi * 8.27" ≈ 1654 px, 11.69" ≈ 2339 px
        self.assertEqual(self.render.PAGE_W, 1654)

    def test_long_document_is_split_into_pages(self):
        points = [f"Пункт {i}. " + "Обстоятельства и правовые основания. " * 8
                  for i in range(1, 12)]
        pages = self.render.render_document(self._doc(points))
        self.assertGreaterEqual(len(pages), 2)
        for page in pages:
            self.assertEqual(page.size, (self.render.PAGE_W, self.render.PAGE_H))

    def test_png_files_are_written_with_readable_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            written = self.render.render_to_png(self._doc(), Path(tmp), "LSCSD-IAD-141")
            self.assertEqual(len(written), 1)
            self.assertEqual(written[0].name, "LSCSD-IAD-141.png")
            self.assertGreater(written[0].stat().st_size, 5000)

            long_points = [f"Пункт {i}. " + "Текст. " * 120 for i in range(1, 10)]
            multi = self.render.render_to_png(self._doc(long_points), Path(tmp), "multi")
            self.assertGreaterEqual(len(multi), 2)
            self.assertTrue(all("стр." in path.name for path in multi))

    def test_assets_fall_back_gracefully_without_images(self):
        assets = self.render.DocAssets(ROOT)
        described = assets.describe()
        self.assertTrue(described["gerb"].endswith("gerb_lscsd.png"))
        self.assertTrue(described["pechat"].endswith("stamp_lscsd.png"))
        self.assertTrue(described["shrift_podpisi"])


if __name__ == "__main__":
    unittest.main()
