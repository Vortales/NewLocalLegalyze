"""v45: PNG постановления печатается из ответа ИИ.

Замечание пользователя: «Проверка ИИ» прошла, обращение висит как «Отказано»,
а по кнопке «Сформировать» .png не создаётся. Ответ ИИ при этом в чате есть
(постановление AG-2 об отказе в принятии обращения).

Разбор показал три независимые причины — все ниже под тестами.
"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import doc_render as prender  # noqa: E402
import gov_case as cases  # noqa: E402
import postanovlenie_data as pdoc  # noqa: E402
import postanovlenie_prokuror_data as pgov  # noqa: E402

#: Ответ ИИ из замечания (AG-2). Сокращены только длинные абзацы.
ANSWER = """ПОСТАНОВЛЕНИЕ
Ведомство: STATE OF SAN-ANDREAS
Подразделение: OFFICE OF THE ATTORNEY GENERAL
Вид документа: Об отказе в принятии обращения к производству
Номер: AG-2
Дата: 09.11.2026
Постановление вынес: Jonathan Raine
Должность: Прокурор штата Сан-Андреас
Удостоверение прокурора: 198632
Город: г. Лос-Сантос
Заявитель: Sae Gucci
ID заявителя: 12361
Дата обращения: 06.11.2026
Предмет обращения: заявление о преступлении, совершенном должностным лицом
при исполнении либо в связи с исполнением служебных обязанностей
В отношении: Manual Killaura
Удостоверение: 33400
Отдел сотрудника: FIB
Дата и время события: 05.11.2026, 19:19-20:01
Нарушенный акт: Регламент подачи обращений в Офис Генерального прокурора
Пункт нарушения: пункт 14 части 1 статьи 16
Нормативное основание: часть 1 статьи 12, пункт 14 части 1 статьи 16,
статьи 27-28 Регламента подачи обращений

УСТАНОВИЛ:
Гражданин Sae Gucci 06.11.2026 подал обращение в Офис Генерального Прокурора
штата Сан-Андреас в отношении сотрудника Федерального Расследовательского
Бюро Manual Killaura с опознавательным знаком 33400. Согласно пункту 14 части
1 статьи 16 Регламента, в рассмотрении обращения отказывается, если оно не
содержит доказательств. Обращение подлежит отклонению на стадии первичного
решения.

ПОСТАНОВИЛ:

1. В принятии обращения гражданина Sae Gucci от 06.11.2026 к производству Офиса Генерального прокурора штата Сан-Андреас отказать на основании пункта 14 части 1 статьи 16 Регламента подачи обращений.
2. Настоящее постановление вступает в законную силу с момента подписания.
3. Разъяснить заявителю, что решение может быть обжаловано в течение 72 часов.

Jonathan Raine
Прокурор штата Сан-Андреас
Удостоверение № 198632

КОНЕЦ ПОСТАНОВЛЕНИЯ

===КОНЕЦ ОТВЕТА==="""

INTAKE_ANSWER = ("ПРОВЕРКА ОБРАЩЕНИЯ\nСостояние: Отказано\n"
                 "Основание: пункт 14 части 1 статьи 16\nКОНЕЦ ПРОВЕРКИ\n"
                 "===КОНЕЦ ОТВЕТА===")


class PollStartsAfterDocumentRequestTests(unittest.TestCase):
    """Причина 1: после «Сформировать» ответ никто не читал."""

    def setUp(self):
        self.src = (ROOT / "main.py").read_text(encoding="utf-8")

    def test_poll_starts_for_document_requests_too(self):
        block = self.src[self.src.index("def _on_pending_send_done"):]
        block = block[:block.index("def _request_key")]
        self.assertIn("self._watch_document_answer()", block)
        # условие «только для intake» убрано
        self.assertNotIn('if kind == "intake":', block)

    def test_auto_send_also_starts_poll(self):
        block = self.src[self.src.index("def _auto_send_document_request"):]
        block = block[:block.index("def _watch_document_answer")]
        self.assertIn('name="DocAutoSend"', block)
        self.assertIn("self._watch_document_answer()", block)
        self.assertLess(block.index('name="DocAutoSend"'),
                        block.index("self._watch_document_answer()"))

    def test_case_number_comes_from_record(self):
        """Причина 2: без номера дела PNG уходил в новое дело."""
        block = self.src[self.src.index("def _resume_pending_send"):]
        block = block[:block.index("def _try_send_pending")]
        self.assertIn("_doc_case_number", block)
        apply_block = self.src[self.src.index("def _apply_gov_answer"):]
        apply_block = apply_block[:apply_block.index("def _open_prompts_folder")]
        self.assertIn("_doc_case_number", apply_block)


class PngFromAnswerTests(unittest.TestCase):
    """Причина 3: разбор и печать — на реальном ответе из замечания."""

    def setUp(self):
        self.doc = pgov.parse_gov_answer(ANSWER)

    def test_answer_is_parsed(self):
        self.assertIsNotNone(self.doc)
        self.assertEqual(self.doc.get("doc_number"), "AG-2")
        self.assertEqual(self.doc.get("applicant_name"), "Sae Gucci")
        self.assertEqual(self.doc.get("subject_name"), "Manual Killaura")
        self.assertEqual(self.doc.get("author_id"), "198632")
        self.assertIn("16", self.doc.get("violation_clause"))
        self.assertEqual(len(self.doc.points), 3)

    def test_refusal_document_has_no_offence_sentence(self):
        """«Указанные обстоятельства образуют нарушение» в отказе — ошибка."""
        established = self.doc.facts_text()
        self.assertNotIn("образуют нарушение", established)
        self.assertIn("Sae Gucci", established)

    def test_png_is_rendered(self):
        out = Path(tempfile.mkdtemp())
        pages = prender.render_document(self.doc, pgov.gov_assets(ROOT))
        paths = prender.save_pages(pages, out, "AG-2")
        self.assertTrue(paths)
        for path in paths:
            self.assertTrue(Path(path).exists())
            self.assertGreater(Path(path).stat().st_size, 10_000)

    def test_png_goes_to_case_stage_folder(self):
        root = Path(tempfile.mkdtemp())
        store = cases.GovCaseStore(root / "cases")
        case = store.open_or_create("Обращение Sae Gucci")
        case.apply_intake(cases.parse_intake_answer(INTAKE_ANSWER))
        store.save(case)
        self.assertTrue(case.refused())
        kind = cases.STAGE_INTAKE           # отказ — всегда первичное решение
        index = case.stage_number(kind)
        folder = cases.stage_folder(root, self.doc.get("doc_number"), index, kind)
        pages = prender.render_document(self.doc, pgov.gov_assets(ROOT))
        paths = prender.save_pages(
            pages, folder, cases.stage_stem("AG-2", index, kind))
        self.assertTrue(paths)
        self.assertTrue(all(Path(p).exists() for p in paths))
        self.assertIn("этап-1", str(folder))


class RefusalTypeTests(unittest.TestCase):
    """Вид решения следует за фактом отказа, а не наоборот."""

    def test_suggestion_follows_the_resolution(self):
        doc = pgov.PostanovlenieProkurora()
        doc.doc_stage = "primary"
        doc.points = ["В принятии обращения гражданина Sae Gucci отказать."]
        self.assertIn("отказе в принятии", pgov.suggest_doc_type(doc))

    def test_refusal_title_has_no_offence(self):
        doc = pgov.PostanovlenieProkurora()
        doc.doc_type = "Об отказе в принятии обращения к рассмотрению"
        doc.violation_clause = "пункт 14 части 1 статьи 16"
        doc.violation_act = "Регламент подачи обращений"
        doc.established = "Обращение не содержит доказательств."
        self.assertNotIn("образуют нарушение", doc.facts_text())

    def test_real_offence_still_printed(self):
        doc = pgov.PostanovlenieProkurora()
        doc.doc_type = "О привлечении к дисциплинарной ответственности"
        doc.violation_clause = "пункт 8.5"
        doc.violation_act = "Устав LSCSD"
        doc.established = "Сотрудник нарушил дресс-код."
        self.assertIn("образуют нарушение", doc.facts_text())


if __name__ == "__main__":
    unittest.main()
