"""test_v63_fixes.py — три замечания выгрузки `Постановление.7z` (v63).

Замечания пользователя:

1. «Вставил Обращение в „Обращение (вставить целиком)“ — пытаюсь нажать на
   кнопку „Этап 1“, а она недоступна. Нажимаю на „Проверить ещё раз“…»
2. «Проанализируй ИИ-документ.txt … в частности „УСТАНОВИЛ“».
3. «При первом нажатии „Этап 1“ перезагрузить браузер существующими методами…
   далее при нажатии „Сформировать“ для Этапа 2 (подэтапов 2) и Этапа 3
   браузер не перезагружать… чтобы ИИ помнил Этапы 1, 2 и 3. И в конце
   предусмотреть кнопку „Закрыть обращение“ на Этапе 3».

Образцы в `tests/samples_v63/` — реальные файлы из выгрузки (не синтетика):
именно на них видны и испорченный «УСТАНОВИЛ», и цепочки без маркеров.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

import gov_case as gc
import ogp_stages
import postanovlenie_prokuror_data as gdata

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = Path(__file__).resolve().parent / "samples_v63"


def _sample(name: str) -> str:
    return (SAMPLES / name).read_text(encoding="utf-8", errors="replace")


def _src(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _block(src: str, start: str, end: str) -> str:
    body = src[src.index(start):]
    return body[:body.index(end)]


# --------------------------------------------------------------------------- #
#  П.2 — «УСТАНОВИЛ»: служебные блоки не печатаются в постановлении
# --------------------------------------------------------------------------- #

class V63EstablishedCleanTests(unittest.TestCase):
    """Раздел «УСТАНОВИЛ» содержит только факты дела и правовую оценку."""

    #: Что не должно попадать в бланк: наша реплика из чата и служебные блоки.
    FORBIDDEN = (
        "Проверь обращение",
        "во вложении «Обращение.txt»",
        "порядок проверки и формат ответа",
        "ПРОВЕРКА ОБРАЩЕНИЯ",
        "КОНЕЦ ПРОВЕРКИ",
        "ЦЕПОЧКИ ЭТАПА 2",
        "ЦЕПОЧКА A:",
        "ЗАЧЕМ:",
        "Состояние: Принято",
        "Что проверить:",
    )

    def test_dirty_sample_contains_chat_leak(self):
        """Образец действительно испорчен — иначе тест ничего не проверяет."""
        dirty = _sample("испорченный_установил_этап1.txt")
        found = [item for item in self.FORBIDDEN if item in dirty]
        self.assertTrue(found, "в образце нет служебного мусора — образец не тот")

    def test_clean_established_removes_chat_leak(self):
        dirty = _sample("испорченный_установил_этап1.txt")
        body = dirty[dirty.index("УСТАНОВИЛ:") + len("УСТАНОВИЛ:"):
                     dirty.index("ПОСТАНОВИЛ:")]
        clean = gdata.clean_established(body)
        for item in self.FORBIDDEN:
            self.assertNotIn(item, clean, f"в «УСТАНОВИЛ» осталось: {item}")

    def test_parse_keeps_only_facts_in_established(self):
        """Ответ постановления этапа 2: «УСТАНОВИЛ» = факты, а не блоки."""
        doc = gdata.parse_gov_answer(_sample("ответ_постановление_этап2.txt"))
        self.assertIsNotNone(doc)
        established = doc.established or ""
        self.assertGreater(len(established), 200, "«УСТАНОВИЛ» потерялся целиком")
        for item in self.FORBIDDEN:
            self.assertNotIn(item, established)
        self.assertIn("London Skeletskoy", established)

    def test_dirty_document_is_parsed_without_service_blocks(self):
        """Документ этапа 1 из выгрузки: служебные блоки не попадают в бланк.

        Это тот самый документ, по которому пришло замечание п.2: в его
        «УСТАНОВИЛ» ИИ вставил нашу реплику из чата и пересказ блока
        «ПРОВЕРКА ОБРАЩЕНИЯ» вместе с цепочками этапа 2.
        """
        text = _sample("испорченный_установил_этап1.txt")
        doc = gdata.parse_gov_answer(text)
        self.assertIsNotNone(doc)
        for part in ((doc.established or ""), " ".join(doc.points or []),
                     doc.situation or ""):
            for item in self.FORBIDDEN:
                self.assertNotIn(item, part, f"служебный текст в документе: {item}")

    def test_intake_answer_is_not_a_document(self):
        """Ответ «ПРОВЕРКА ОБРАЩЕНИЯ» — не постановление: его не печатают.

        В нём нет документа, поэтому разбор обязан вернуть None (иначе клиент
        напечатал бы служебный блок как постановление).
        """
        self.assertIsNone(gdata.parse_gov_answer(_sample("ответ_этап1_с_цепочками.txt")))

    def test_service_blocks_are_cut_as_a_whole(self):
        text = ("УСТАНОВИЛ: факт первый. ПРОВЕРКА ОБРАЩЕНИЯ Состояние: Принято "
                "КОНЕЦ ПРОВЕРКИ ЦЕПОЧКИ ЭТАПА 2 ЦЕПОЧКА A: путь ЗАЧЕМ: быстро "
                "1. О вызове — нужен КОНЕЦ ЦЕПОЧЕК факт второй.")
        cleaned = gdata.clean_established(text)
        self.assertIn("факт первый", cleaned)
        self.assertIn("факт второй", cleaned)
        self.assertNotIn("ЦЕПОЧКА", cleaned)
        self.assertNotIn("Состояние", cleaned)

    def test_instruction_sentence_inside_facts_is_removed(self):
        """Инструкция может стоять в одном абзаце с фактами — режем по фразе."""
        text = ("Проверь обращение. Текст обращения — во вложении «Обращение.txt», "
                "порядок проверки и формат ответа — в файле заявки. "
                "15 ноября 2026 года в Офис поступило обращение адвоката.")
        cleaned = gdata.clean_established(text)
        self.assertNotIn("Проверь обращение", cleaned)
        self.assertNotIn("во вложении", cleaned)
        self.assertIn("поступило обращение адвоката", cleaned)

    def test_clean_established_is_idempotent(self):
        text = _sample("испорченный_установил_этап1.txt")
        body = text[text.index("УСТАНОВИЛ:") + len("УСТАНОВИЛ:"):
                    text.index("ПОСТАНОВИЛ:")]
        once = gdata.clean_established(body)
        self.assertEqual(once, gdata.clean_established(once))


# --------------------------------------------------------------------------- #
#  П.3 — цепочки из ответа этапа 1 находятся, даже без маркеров «===»
# --------------------------------------------------------------------------- #

class V63ChainsTests(unittest.TestCase):
    """Выгрузка: три цепочки в ответе были, а форма их не видела."""

    def test_chains_are_found_in_real_answer(self):
        chains = ogp_stages.split_chains(_sample("ответ_этап1_с_цепочками.txt"))
        self.assertGreaterEqual(len(chains), 2,
                                "цепочки из ответа ИИ не разобрались")
        for chain in chains:
            self.assertTrue(chain["letter"])
            self.assertTrue(chain["title"])
            self.assertGreaterEqual(len(chain["steps"]), 1)

    def test_chain_steps_keep_their_titles(self):
        chains = ogp_stages.split_chains(_sample("ответ_этап1_с_цепочками.txt"))
        titles = [step["title"] for chain in chains for step in chain["steps"]]
        self.assertTrue(any(t.lower().startswith("об истребовании") for t in titles),
                        f"названия шагов потерялись: {titles[:4]}")

    def test_steps_without_numbers_are_numbered(self):
        text = ("ЦЕПОЧКИ ЭТАПА 2\n"
                "ЦЕПОЧКА A: свой путь\n"
                "ЗАЧЕМ: быстро\n"
                "Об истребовании видеофиксации — получить запись\n"
                "О наложении штрафной санкции — привлечь к ответственности\n"
                "КОНЕЦ ЦЕПОЧЕК\n")
        chains = ogp_stages.split_chains(text)
        self.assertEqual(len(chains), 1)
        self.assertEqual([step["n"] for step in chains[0]["steps"]], [1, 2])

    def test_markdown_and_missing_markers_are_tolerated(self):
        text = ("**ЦЕПОЧКИ ЭТАПА 2**\n"
                "### ЦЕПОЧКА C: проверка своими силами\n"
                "**ЗАЧЕМ:** быстро\n"
                "1. Об истребовании видеофиксации — получить запись\n"
                "2. О наложении штрафной санкции — привлечь к ответственности\n"
                "**КОНЕЦ ЦЕПОЧЕК**\n")
        chains = ogp_stages.split_chains(text)
        self.assertEqual(len(chains), 1)
        self.assertEqual(chains[0]["letter"], "C")
        self.assertEqual(len(chains[0]["steps"]), 2)

    def test_chains_present_sees_soft_marker(self):
        self.assertTrue(ogp_stages.chains_present("ЦЕПОЧКИ ЭТАПА 2\nЦЕПОЧКА A: путь"))
        self.assertFalse(ogp_stages.chains_present("просто текст без цепочек"))


# --------------------------------------------------------------------------- #
#  П.1 и п.3 — «Этап 1» доступен сразу, дальше без перезагрузки
# --------------------------------------------------------------------------- #

class V63Stage1ButtonTests(unittest.TestCase):
    """Кнопка «Этап 1» включается по тексту обращения, а не по делу."""

    def test_can_run_uses_appeal_text(self):
        case = gc.GovCase(number="1")
        self.assertFalse(ogp_stages.node_state(case, "1")["can_run"])
        case.appeal_raw = "Обращение гражданина целиком"
        self.assertTrue(ogp_stages.node_state(case, "1")["can_run"])

    def test_appeal_field_writes_case_and_refreshes_button(self):
        block = _block(_src("postanovlenie_prokuror_ui.py"),
                       "def _on_appeal_changed", "def _keyword_summary")
        self.assertIn('self.case.appeal_raw = self.texts["appeal_raw"].toPlainText()',
                      block)
        self.assertIn("self._refresh_main_action()", block)

    def test_hint_names_the_stage_button(self):
        block = _block(_src("postanovlenie_prokuror_ui.py"),
                       "def _on_appeal_changed", "def _keyword_summary")
        self.assertIn("«Этап 1»", block)


class V63BrowserPolicyTests(unittest.TestCase):
    """Политика чата: v63 — перезагрузка только на этапе 1; v65 — на каждом шаге.

    Решение пользователя по выгрузке «Обращение №1.7z»: «не оставлять браузер
    для памяти, а очищать». Память дела ИИ получает из файла заявки
    («СВЕДЕНИЯ ПО ДЕЛУ»), поэтому точность не теряется, а на чистой странице
    ровно один ответ — клиент не разбирает прошлый шаг вместо текущего.
    """

    def test_every_node_request_restarts_browser(self):
        block = _block(_src("postanovlenie_prokuror_ui.py"),
                       "def _send_node", "def _save_node_document")
        self.assertIn("self.restart_requested.emit", block)
        self.assertNotIn("self.send_in_chat.emit", block)
        # v65: перезагрузка безусловная — ветвления по этапу больше нет.
        self.assertNotIn('if str(kind) == "1":\n            self.restart', block)

    def test_form_has_restart_signal_only(self):
        tree = ast.parse(_src("postanovlenie_prokuror_ui.py"))
        signals = {node.targets[0].id for node in ast.walk(tree)
                   if isinstance(node, ast.Assign)
                   and isinstance(node.value, ast.Call)
                   and getattr(node.value.func, "id", "") == "pyqtSignal"
                   and isinstance(node.targets[0], ast.Name)}
        self.assertIn("restart_requested", signals)
        self.assertIn("case_closed", signals)
        self.assertNotIn("send_in_chat", signals)

    def test_reorder_also_restarts(self):
        block = _block(_src("postanovlenie_prokuror_ui.py"),
                       "def _send_custom_order", "def _build_work_window")
        self.assertIn("self.restart_requested.emit", block)
        self.assertNotIn("self.send_in_chat.emit", block)

    def test_main_has_no_send_in_chat_slot(self):
        """Обработчика «в текущий диалог» больше нет — одна дорога: чистый чат."""
        src = _src("main.py")
        self.assertNotIn("def _on_send_in_chat", src)
        self.assertNotIn("dlg.send_in_chat.connect", src)
        tree = ast.parse(src)
        handler = next(node for node in ast.walk(tree)
                       if isinstance(node, ast.FunctionDef)
                       and node.name == "_on_pending_send_restart")
        called = {node.func.attr for node in ast.walk(handler)
                  if isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Attribute)}
        self.assertIn("_restart_browser_for_fresh_chat", called)
        assigned = {node.targets[0].attr for node in ast.walk(handler)
                    if isinstance(node, ast.Assign)
                    and isinstance(node.targets[0], ast.Attribute)
                    and isinstance(node.targets[0].value, ast.Name)
                    and node.targets[0].value.id == "self"}
        self.assertIn("_fresh_chat_requested", assigned)
        # v65: новая заявка сбрасывает состояние ожидания ответа.
        self.assertIn("_doc_handled_text", assigned)

    def test_intake_still_restarts_for_clean_chat(self):
        """Этап 1 по-прежнему идёт через чистый чат (точность решения)."""
        block = _block(_src("postanovlenie_prokuror_ui.py"),
                       "def _intake_check", "def _mark_request_sent")
        self.assertIn("self.restart_requested.emit", block)


class V63CloseCaseTests(unittest.TestCase):
    """Кнопка «Закрыть обращение» на этапе 3."""

    def test_button_exists_on_chain_tab(self):
        src = _src("postanovlenie_prokuror_ui.py")
        self.assertIn('QPushButton("Закрыть обращение")', src)
        block = _block(src, "def _build_chain_tab", "def _refresh_chain_view")
        self.assertIn("Закрыть обращение", block)

    def test_close_marks_case_worked_off(self):
        case = gc.GovCase(number="7")
        case.appeal_raw = "текст"
        case.set_outcome("accept")
        case.set_work_node("3", answer="ПОСТАНОВИЛ: завершить производство", done=True)
        self.assertTrue(case.close_work(reason="этап 3 выполнен",
                                        when="25.11.2026 21:00"))
        self.assertEqual(case.status, gc.STATUS_CLOSED)
        self.assertEqual(case.closed_at, "25.11.2026 21:00")
        row = gc.case_row(case)
        self.assertTrue(row["closed"])
        self.assertIn("отработано", row["status"])

    def test_close_is_saved_and_restored(self):
        case = gc.GovCase(number="7")
        case.close_work(reason="готово", when="25.11.2026 21:00")
        restored = gc.GovCase.from_dict(case.to_dict()) if hasattr(
            gc.GovCase, "from_dict") else case
        self.assertEqual(restored.closed_at, "25.11.2026 21:00")
        self.assertEqual(restored.close_reason, "готово")

    def test_close_asks_before_skipping_unfinished_steps(self):
        """Решение пользователя: закрывать можно всегда, но с вопросом.

        «Нажать можно в любой момент, но при незакрытых шагах — вопрос
        „шаги 2.2, 2.3 не вынесены, закрыть всё равно?“».
        """
        block = _block(_src("postanovlenie_prokuror_ui.py"),
                       "def _close_case_requested", "def _save_current_step")
        self.assertIn("ogp_stages.close_reason(self.case)", block)
        self.assertLess(block.index("close_reason(self.case)"),
                        block.index("self.case_closed.emit"))
        self.assertIn("close_work", block)
        # жёсткого «этап 3 не выполнен — отказ» больше нет
        self.assertNotIn("Этап 3 не выполнен", block)

    def test_close_reason_lists_unfinished_steps(self):
        case = gc.GovCase(number="7")
        case.appeal_raw = "текст"
        self.assertIn("этап 1 не выполнен", ogp_stages.close_reason(case))
        case.set_outcome("accept")
        case.apply_chains([{"letter": "A", "title": "Цепочка A", "why": "",
                            "steps": [{"n": 1, "kind": "measure", "title": "Запрос"},
                                      {"n": 2, "kind": "measure", "title": "Проверка"}]}])
        self.assertIn("2.1", ogp_stages.close_reason(case))
        case.set_work_node("2.1", answer="ПОСТАНОВИЛ: истребовать документы")
        self.assertIn("2.2", ogp_stages.close_reason(case))
        self.assertNotIn("2.1,", ogp_stages.close_reason(case))
        case.set_work_node("2.2", answer="ПОСТАНОВИЛ: истребовать сведения")
        self.assertEqual(ogp_stages.close_reason(case), "")

    def test_negative_step_is_reported_for_stage3(self):
        case = gc.GovCase(number="7")
        case.appeal_raw = "текст"
        case.set_outcome("accept")
        case.apply_chains([{"letter": "A", "title": "Цепочка A", "why": "",
                            "steps": [{"n": 1, "kind": "measure", "title": "Запрос"},
                                      {"n": 2, "kind": "measure", "title": "Проверка"}]}])
        case.set_work_node("2.1", answer="ПОСТАНОВИЛ: истребовать документы")
        case.set_work_node("2.2", answer="ПОСТАНОВИЛ: отказать в удовлетворении")
        self.assertEqual(ogp_stages.negative_steps(case), [2])

    def test_main_reacts_with_fresh_chat(self):
        block = _block(_src("main.py"), "def _on_gov_case_closed",
                       "def _on_gov_dialog_finished")
        self.assertIn("_schedule_fresh_chat_after_answer", block)


class V63DateCheckTests(unittest.TestCase):
    """Дата постановления раньше даты обращения — замечание (v63).

    В выгрузке `Постановление.7z` документ этапа 1 был датирован 16.11.2026,
    а обращение — 25.11.2026: ИИ взял дату из примеров промта.
    """

    def test_document_before_appeal_is_flagged(self):
        issue = gdata.check_gov_dates("16.11.2026", "25.11.2026")
        self.assertIn("раньше даты обращения", issue)

    def test_same_or_later_date_is_fine(self):
        self.assertEqual(gdata.check_gov_dates("25.11.2026", "25.11.2026"), "")
        self.assertEqual(gdata.check_gov_dates("26.11.2026", "25.11.2026"), "")

    def test_empty_dates_are_not_issues(self):
        self.assertEqual(gdata.check_gov_dates("", "25.11.2026"), "")
        self.assertEqual(gdata.check_gov_dates("16.11.2026", ""), "")

    def test_check_is_wired_into_document_check(self):
        self.assertIn("check_gov_dates", _src("postanovlenie_prokuror_data.py"))


class V63PromptTests(unittest.TestCase):
    """Текущий промт согласован с клиентом (три состояния, даты).

    v64: проверяем САМЫЙ СВЕЖИЙ промт в «Для новой версии/Текущий промт»,
    а не жёстко 1.34 — действующим стал 1.35 (приведён к заголовкам заявок
    клиента), и каждый следующий выпуск не должен ломать эти проверки.
    """

    def setUp(self):
        folder = ROOT.parent / "Для новой версии" / "Текущий промт"
        found = []
        if folder.is_dir():
            for path in folder.glob("promt 1.*.txt"):
                match = re.search(r"promt 1\.(\d+)\.txt$", path.name)
                if match:
                    found.append((int(match.group(1)), path))
        if not found:
            self.skipTest("папка промтов вне версии не найдена")
        self.PROMPT = max(found)[1]
        self.assertGreaterEqual(int(re.search(r"1\.(\d+)", self.PROMPT.name).group(1)),
                                34, "промт старше 1.34")
        self.text = self.PROMPT.read_text(encoding="utf-8")

    def test_stage1_has_only_three_states(self):
        self.assertIn("ТРИ СОСТОЯНИЯ ЭТАПА 1", self.text)
        self.assertNotIn("пять состояний", self.text)
        self.assertNotIn("«Отказано» или «Прекращено»", self.text)

    def test_chain_markers_match_the_client(self):
        """Маркеры промта обязаны совпадать с теми, что ищет ogp_stages."""
        for marker in (ogp_stages.CHAIN_START, ogp_stages.CHAIN_END):
            self.assertIn(marker, self.text)

    def test_dates_rule_is_explicit(self):
        self.assertIn("ДАТЫ БЕРУТСЯ ТОЛЬКО ИЗ ОБРАЩЕНИЯ", self.text)


class V63PlanSourceTests(unittest.TestCase):
    """План берётся из активного варианта цепочки, а не из отставшей копии."""

    def test_current_plan_reads_active_chain(self):
        case = gc.GovCase(number="1")
        case.work_chains = [
            {"letter": "A", "title": "A", "steps": [{"n": 1, "title": "a1"}],
             "nodes": {}},
            {"letter": "B", "title": "B",
             "steps": [{"n": 1, "title": "b1"}, {"n": 2, "title": "b2"}],
             "nodes": {}},
        ]
        case.work_letter = "B"
        case.work_plan = [{"n": 1, "title": "a1"}]      # отставшая копия
        self.assertEqual([step["title"] for step in case.current_plan()],
                         ["b1", "b2"])
        self.assertEqual(case.next_work_step(), 1)

    def test_stages_module_uses_current_plan(self):
        src = _src("ogp_stages.py")
        self.assertIn("def _plan(case)", src)
        self.assertNotIn("for step in case.work_plan", src)


if __name__ == "__main__":
    unittest.main()
