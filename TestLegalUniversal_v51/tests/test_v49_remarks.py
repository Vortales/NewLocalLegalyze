"""test_v49_remarks.py — четыре замечания пользователя к версии v49.

1. Реквизиты определяет только ИИ: локальные условия распознавания
   («Распознано: заявитель …») убраны, ключевые слова из ответа ИИ
   разбираются и подставляются в дело и в постановление.
2. Этап выбирается в одном месте («Постановление»); дубль полей этапа из
   «Обстоятельств» убран. PNG этапов: ОДИН номер постановления, разные папки
   и метки «Этап N» + «Вид решения».
3. Окно «Постановление Обращение №» не закрывается на «Проверка ИИ» и
   «Сформировать» — оно обновляется по запросу и ответу ИИ.
4. В «Основание:» статьи и их содержимое не дублируются.
"""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _src(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _block(src: str, start: str, end: str) -> str:
    body = src[src.index(start):]
    return body[:body.index(end)]


def _code(src: str) -> str:
    """Строки кода без комментариев и без строк-комментариев в docstring."""
    lines = []
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        lines.append(line)
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
#  1. Реквизиты определяет ИИ
# --------------------------------------------------------------------------- #

INTAKE_ANSWER = """===ПРОВЕРКА ОБРАЩЕНИЯ===
Состояние: Отказано
Основание: пункт 14 части 1 статьи 16 Регламента подачи обращений
Заявитель: Sae Gucci
ID заявителя: 12361
В отношении: Manual Killaura
Удостоверение: 33400
Организация нарушителя: FIB
Дата и время события: 05.11.2026, 19:19-20:01
Предмет обращения: заявление о преступлении
===КОНЕЦ ПРОВЕРКИ===
===КОНЕЦ ОТВЕТА===
"""


class V49KeywordsTests(unittest.TestCase):
    """Ключевые слова ответа ИИ — единственный источник реквизитов."""

    def setUp(self):
        import postanovlenie_prokuror_data as g
        self.g = g
        self.result = g.parse_intake_answer(INTAKE_ANSWER)

    def test_keywords_are_parsed_from_answer(self):
        keywords = self.result["keywords"]
        self.assertEqual(keywords["applicant_name"], "Sae Gucci")
        self.assertEqual(keywords["applicant_id"], "12361")
        self.assertEqual(keywords["subject_name"], "Manual Killaura")
        self.assertEqual(keywords["subject_id"], "33400")
        self.assertEqual(keywords["subject_department"], "FIB")
        self.assertIn("05.11.2026", keywords["event_date"])
        self.assertIn("преступлении", keywords["appeal_subject"])

    def test_service_fields_are_not_keywords(self):
        keywords = self.result["keywords"]
        # состояние/основание — служебные строки блока, а не реквизиты
        self.assertNotIn("state", keywords)
        self.assertNotIn("basis", keywords)

    def test_empty_keywords_are_dropped(self):
        result = self.g.parse_intake_answer(
            "===ПРОВЕРКА ОБРАЩЕНИЯ===\nСостояние: Принято\n"
            "Заявитель:\n===КОНЕЦ ПРОВЕРКИ===\n")
        self.assertEqual(result["keywords"], {})

    def test_place_placeholder_is_not_a_keyword(self):
        result = self.g.parse_intake_answer(
            "===ПРОВЕРКА ОБРАЩЕНИЯ===\nСостояние: Принято\n"
            "Место события: в ходе служебной деятельности\n"
            "===КОНЕЦ ПРОВЕРКИ===\n")
        self.assertNotIn("event_place", result["keywords"])


class V49CaseKeywordsTests(unittest.TestCase):
    """Дело и постановление получают реквизиты из ключевых слов ИИ."""

    def setUp(self):
        import gov_case as c
        import postanovlenie_prokuror_data as g
        self.c = c
        self.g = g

    def test_case_keeps_keywords(self):
        case = self.c.GovCase(number="1", appeal_raw="обращение")
        case.apply_intake(self.c.parse_intake_answer(INTAKE_ANSWER))
        self.assertEqual(case.keyword("applicant_name"), "Sae Gucci")
        self.assertEqual(case.keyword("subject_name"), "Manual Killaura")
        self.assertEqual(case.keyword("subject_id"), "33400")

    def test_keywords_survive_json_roundtrip(self):
        case = self.c.GovCase(number="1", appeal_raw="обращение")
        case.apply_intake(self.c.parse_intake_answer(INTAKE_ANSWER))
        again = self.c.GovCase.from_dict(json.loads(json.dumps(case.to_dict())))
        self.assertEqual(again.intake_fields["applicant_name"], "Sae Gucci")

    def test_old_case_json_without_keywords_is_readable(self):
        case = self.c.GovCase.from_dict({"number": "1", "status": "new"})
        self.assertEqual(case.intake_fields, {})
        self.assertEqual(case.keyword("applicant_name"), "")

    def test_new_keywords_do_not_erase_old_ones(self):
        case = self.c.GovCase(number="1")
        case.apply_keywords({"applicant_name": "Sae Gucci"})
        case.apply_keywords({"subject_name": "Manual Killaura"})
        self.assertEqual(case.keyword("applicant_name"), "Sae Gucci")
        self.assertEqual(case.keyword("subject_name"), "Manual Killaura")

    def test_document_accepts_keywords(self):
        doc = self.g.PostanovlenieProkurora(doc_number="AG-1")
        doc.apply_keywords({"applicant_name": "Sae Gucci 12361",
                            "subject_name": "Manual Killaura"})
        self.assertEqual(doc.get("applicant_name"), "Sae Gucci")
        self.assertEqual(doc.get("applicant_id"), "12361")
        self.assertEqual(doc.get("subject_name"), "Manual Killaura")

    def test_document_skips_placeholders(self):
        doc = self.g.PostanovlenieProkurora()
        doc.apply_keywords({"event_place": "по обстоятельствам, указанным в заявке"})
        self.assertEqual(doc.get("event_place"), "")


class V49NoLocalRecognitionTests(unittest.TestCase):
    """Локального «распознавания» реквизитов больше нет."""

    def setUp(self):
        self.ui = _src("postanovlenie_prokuror_ui.py")

    def test_no_recognised_line_in_form(self):
        block = _code(_block(self.ui, "def _on_appeal_changed",
                             "def _keyword_summary"))
        # строка «Распознано: …» ушла из текста подсказки
        self.assertNotIn('"Распознано', block)
        self.assertNotIn("'Распознано", block)
        self.assertNotIn("parse_appeal_text", block)

    def test_hint_asks_to_run_ai_check(self):
        block = _block(self.ui, "def _on_appeal_changed", "def _keyword_summary")
        self.assertIn("Проверка ИИ", block)
        self.assertIn("Реквизиты определит ИИ", block)

    def test_collect_does_not_guess_from_appeal_text(self):
        block = _block(self.ui, "def _collect(self)", "def set_document")
        self.assertNotIn("apply_appeal(doc.appeal_raw)", block)
        self.assertIn("apply_keywords(self.case.intake_fields)", block)

    def test_intake_request_asks_for_keywords(self):
        import gov_case as c
        text = c.intake_request(c.GovCase(number="1", appeal_raw="обращение"))
        self.assertIn("ключевыми словами", text)
        # и сама заявка не выглядит готовым ответом ИИ (v44)
        self.assertFalse(c.parse_intake_answer(text)["ok"])


# --------------------------------------------------------------------------- #
#  2. Один этап, один номер, разные папки и метки
# --------------------------------------------------------------------------- #

class V49StageNamesTests(unittest.TestCase):
    def setUp(self):
        import gov_case as c
        self.c = c

    def test_doc_type_slug_is_filesystem_safe(self):
        slug = self.c.doc_type_slug("Об отказе в принятии обращения к рассмотрению")
        self.assertEqual(slug, "отказе-в-принятии-обращения-к-рассмотрению")
        self.assertIsNone(re.search(r"[\\/:*?\"<>| ]", slug))

    def test_number_is_shared_between_stages(self):
        first = self.c.stage_stem("AG-1", 1, self.c.STAGE_INTAKE,
                                  "Об отказе в принятии обращения к рассмотрению")
        second = self.c.stage_stem("AG-1", 2, self.c.STAGE_MEASURE,
                                   "О привлечении к административной ответственности")
        self.assertTrue(first.startswith("AG-1_"), first)
        self.assertTrue(second.startswith("AG-1_"), second)
        self.assertNotEqual(first, second)

    def test_stem_has_stage_and_decision_type(self):
        stem = self.c.stage_stem("AG-1", 2, self.c.STAGE_MEASURE,
                                 "О привлечении к административной ответственности")
        self.assertIn("Этап_2", stem)
        self.assertIn("санкция", stem)
        self.assertIn("Вид_привлечении-к-административной-ответственности", stem)

    def test_stage_folders_stay_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = self.c.stage_folder(tmp, "AG-1", 1, self.c.STAGE_INTAKE)
            second = self.c.stage_folder(tmp, "AG-1", 2, self.c.STAGE_MEASURE)
            self.assertNotEqual(first, second)
            self.assertTrue(first.is_dir() and second.is_dir())
            self.assertEqual(first.parent, second.parent)

    def test_main_puts_decision_type_into_file_name(self):
        src = _src("main.py")
        self.assertIn('doc.get("doc_type"))', src)
        block = _block(src, "def _apply_gov_answer", "def _open_prompts_folder")
        self.assertIn("pcases.stage_stem", block)
        self.assertIn('doc.get("doc_type")', block)

    def test_stage_artifacts_use_decision_type(self):
        src = _src("gov_case.py")
        block = _block(src, "def stage_artifacts", "def primary_stage_kind")
        self.assertIn('doc.get("doc_type")', block)


class V49SingleStagePlaceTests(unittest.TestCase):
    """Этап выбирается только на вкладке «Постановление»."""

    def setUp(self):
        self.ui = _src("postanovlenie_prokuror_ui.py")

    def test_work_tab_has_no_stage_fields(self):
        work = _block(self.ui, "def _build_work_tab", "def _build_document_tab")
        for marker in ('self.cb_stage_kind = QComboBox()', "self.stage_list = QListWidget()",
                       "+ этап", 'self._label("Этап"'):
            self.assertNotIn(marker, work)
        # одна строка-состояние без полей остаётся
        self.assertIn('self.stage_hint = self._label("", "Caption")', work)

    def test_document_tab_holds_the_only_stage_selector(self):
        doc = _block(self.ui, "def _build_document_tab", "def _build_history_tab")
        self.assertIn("self.cb_stage = QComboBox()", doc)
        self.assertIn("self.cb_chain_stage = QComboBox()", doc)
        self.assertIn("def _add_stage", self.ui)

    def test_stage_kind_comes_from_document_tab(self):
        block = _block(self.ui, "def _stage_kind", "def _stage_index")
        self.assertNotIn("cb_stage_kind", block)
        self.assertIn("cb_chain_stage", block)

    def test_mapping_between_stage_and_kind_is_explicit(self):
        import gov_case as c
        self.assertEqual(c.stage_kind_for_gov_stage(c.gdata.GOV_STAGE_PRIMARY),
                         c.STAGE_INTAKE)
        self.assertEqual(c.stage_kind_for_gov_stage(c.gdata.GOV_STAGE_FINAL),
                         c.STAGE_MEASURE)
        self.assertEqual(c.gov_stage_for_kind(c.STAGE_INTAKE),
                         c.gdata.GOV_STAGE_PRIMARY)


# --------------------------------------------------------------------------- #
#  3. Окно не закрывается по кнопкам формы
# --------------------------------------------------------------------------- #

class V49DialogStaysOpenTests(unittest.TestCase):
    def setUp(self):
        self.ui = _src("postanovlenie_prokuror_ui.py")
        self.main = _src("main.py")

    def test_intake_check_does_not_close_window(self):
        block = _code(_block(self.ui, "def _intake_check", "def _mark_request_sent"))
        self.assertNotIn("self.accept()", block)
        self.assertIn("_mark_request_sent", block)

    def test_form_does_not_close_window(self):
        block = _code(_block(self.ui, "def _form(self)", "def showEvent"))
        self.assertNotIn("self.accept()", block)
        self.assertIn('_mark_request_sent("Постановление")', block)

    def test_window_waits_for_answer_and_is_refreshed(self):
        self.assertIn("self.waiting_for_answer", self.ui)
        self.assertIn("def note_answer_received", self.ui)
        self.assertIn("def refresh_document", self.ui)
        # обновление формы по ответу ИИ, без закрытия окна
        self.assertIn("dialog.refresh_document(doc)", self.main)
        block = _block(self.main, "def _apply_gov_answer", "def _open_prompts_folder")
        self.assertNotIn("dialog.accept()", block)
        self.assertNotIn("dialog.close()", block)

    def test_restart_is_skipped_while_form_is_open(self):
        """v51: форма остаётся открытой, «перезапуск» заменён очисткой чата.

        Раньше здесь проверялось `if dialog is None:` — форма-де закрываться не
        должна, а клиент перезапускается только без неё. v51 (замечание п.3):
        браузер обязан обновиться ПОСЛЕ ответа ИИ в любом случае, но не
        убийством дерева, а мягкой перезагрузкой страницы. Форма при этом
        по-прежнему не закрывается.
        """
        block = _block(self.main, "def _apply_gov_answer", "def _open_prompts_folder")
        self.assertIn("self._schedule_fresh_chat_after_answer()", block)
        # старый «перезапуск клиента» по закрытой форме больше не вызывается
        self.assertNotIn("self._restart_after_document()", block)
        self.assertNotIn("dialog.accept()", block)
        self.assertNotIn("dialog.close()", block)


# --------------------------------------------------------------------------- #
#  4. «Основание» без дублей
# --------------------------------------------------------------------------- #

class V49BasisDedupeTests(unittest.TestCase):
    def setUp(self):
        import postanovlenie_prokuror_data as g
        self.g = g
        self.act = ("Регламент подачи обращений в Офис Генерального прокурора "
                    "штата Сан-Андреас")

    def test_same_norm_twice_is_printed_once(self):
        text = (f"пункт 14 части 1 статьи 16; {self.act}; "
                f"пункт 14 части 1 статьи 16 {self.act}")
        result = self.g._dedupe_basis_segments(text)
        self.assertEqual(result.count("пункт 14 части 1 статьи 16"), 1)

    def test_article_covered_by_point_is_dropped(self):
        text = (f"пункт 14 части 1 статьи 16; {self.act}; "
                f"часть 1 статьи 16 {self.act}; статьи 27–28 {self.act}")
        result = self.g._dedupe_basis_segments(text)
        self.assertEqual(result.count("статьи 16"), 1)
        self.assertIn("пункт 14 части 1 статьи 16", result)
        self.assertIn("27–28", result)

    def test_other_norms_are_kept(self):
        text = ("часть 2 статьи 12.8; часть 2 статьи 15.1; "
                "часть 2 статьи 36 Конституции штата Сан-Андреас")
        result = self.g._dedupe_basis_segments(text)
        for piece in ("12.8", "15.1", "36"):
            self.assertIn(piece, result)

    def test_basis_text_of_repeated_answer_has_one_norm(self):
        answer = (
            "===ПОСТАНОВЛЕНИЕ===\n"
            "Номер: AG-1\nДата: 09.11.2026\n"
            "Вид документа: Об отказе в принятии обращения к рассмотрению\n"
            "Постановление вынес: Jonathan Raine\n"
            "Заявитель: Sae Gucci\n"
            f"Нарушенный акт: {self.act}\n"
            "Пункт нарушения: пункт 14 части 1 статьи 16\n"
            f"Нормативное основание: пункт 14 части 1 статьи 16 {self.act}; "
            f"часть 1 статьи 16 {self.act}\n"
            "УСТАНОВИЛ:\nОбращение поступило.\n"
            "ПОСТАНОВИЛ:\nОтказать в принятии обращения.\n"
            "===КОНЕЦ ПОСТАНОВЛЕНИЯ===\n===КОНЕЦ ОТВЕТА===\n")
        doc = self.g.parse_gov_answer(answer)
        basis = doc.basis_text()
        self.assertEqual(basis.count("статьи 16"), 1, basis)




class V49FormSurvivesBrowserRestartTests(unittest.TestCase):
    """Форма возвращается на экран после смены браузера (v49)."""

    def setUp(self):
        self.main = _src("main.py")

    def test_visibility_is_remembered_and_restored(self):
        self.assertIn("_remember_gov_dialog_visibility", self.main)
        self.assertIn("_restore_gov_dialog_visibility", self.main)
        self.assertIn("self._gov_dialog_visibility = self._remember_gov_dialog_visibility()",
                      self.main)
        self.assertIn("QTimer.singleShot(650, self._restore_gov_dialog_visibility)",
                      self.main)

    def test_visibility_is_marked_before_new_browser_starts(self):
        block = _block(self.main, "def _restart_browser_for_fresh_chat",
                       "def _load_pending_document")
        # видимость запоминается до запуска нового браузера, иначе форма
        # потерялась бы между `host.stop()` и `_start_chrome`
        self.assertLess(block.index("_remember_gov_dialog_visibility"),
                        block.index("QTimer.singleShot(400, self._start_chrome)"))

    def test_restore_shows_the_same_dialog(self):
        block = _block(self.main, "def _restore_gov_dialog_visibility",
                       "def _restart_browser_for_fresh_chat")
        self.assertIn("dialog.show()", block)
        self.assertIn("self._gov_dialog", block)


if __name__ == "__main__":
    unittest.main()
