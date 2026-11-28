"""v56 — четыре правки по выгрузке «Постановление» (архив `Постановление.7z`).

Замечания пользователя:

1. **Двойное «постановил».** В IAD вводная часть заканчивалась «…настоящим
   кодексом постановил:», и сразу под ней печатался заголовок «ПОСТАНОВИЛ:» —
   слово повторялось дважды. Должно быть «…настоящим кодексом,» и далее
   «ПОСТАНОВИЛ:».

2. **Блок «Основание:».** В обоих бланках PNG печатался «Основание: пункт 2.11
   (5.1, 5.5… Устава LSCSD) Устава Los Santos County Sheriff Department» —
   с задвоенным актом. Блок в PNG не нужен вообще.

3. **Порядок «Сформировать».** Сначала внутри обновляется страница ИИ и только
   потом материал уходит в чат — в обеих формах.

4. **«ОБСТОЯТЕЛЬСТВА ПРОКУРОРА» в «УСТАНОВИЛ».** Блоки вкладки
   «Обстоятельства» (а–г) — это вход для ИИ, а не текст документа. В PNG идёт
   только ответ ИИ; в заявку — только то, что человек написал сам.
"""

from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Выгрузка пользователя (распакованный `Постановление.7z`), если она рядом.
EXPORT = ROOT.parent / "Постановление"
needs_export = unittest.skipUnless(
    EXPORT.is_dir(), "нет выгрузки «Постановление» рядом с репозиторием")


def _src(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _block(source: str, start: str, end: str = "\n    def ") -> str:
    """Кусок исходника от `start` до следующего метода."""
    chunk = source[source.index(start) + len(start):]
    cut = chunk.find(end)
    return chunk if cut < 0 else chunk[:cut]


class V56PreambleTests(unittest.TestCase):
    """1. «постановил» печатается один раз."""

    def setUp(self):
        import postanovlenie_data as pdoc
        self.doc = pdoc.Postanovlenie(
            doc_number="LSCSD-IAD-1", author_name="Marcus Nomad",
            author_rank="Куратор отдела IAD (Assistent sheriff)")

    def test_preamble_ends_with_codex(self):
        text = self.doc.preamble()
        self.assertTrue(text.endswith("настоящим кодексом,"), text)
        self.assertNotIn("постановил", text.lower())

    def test_resolution_heading_is_the_only_postanovil(self):
        from doc_render import render_document
        text = self.doc.to_document_txt()
        self.assertEqual(text.lower().count("постановил"), 1)
        self.assertIn("ПОСТАНОВИЛ:", text)
        # и в самом рендере: заголовок один
        self.assertTrue(callable(render_document))


class V56BasisHiddenTests(unittest.TestCase):
    """2. Блока «Основание:» в PNG нет ни в одном бланке."""

    def setUp(self):
        import postanovlenie_data as pdoc
        import postanovlenie_prokuror_data as pgov
        self.iad = pdoc.Postanovlenie(
            doc_number="LSCSD-IAD-1", subject_name="Aliya Nomad",
            violation_act="Устав Los Santos County Sheriff Department",
            violation_clause="пункты 2.11, 5.1, 5.5 Устава LSCSD",
            penalty="строгий выговор",
            situation="Сотрудник нарушил форму одежды.")
        self.gov = pgov.PostanovlenieProkurora(
            doc_number="AG-1", city="г. Лос-Сантос",
            violation_act='Закон "О Федеральном Расследовательском Бюро"',
            violation_clause="части 1 и 2 статьи 13")

    def test_models_hide_basis_in_png(self):
        self.assertFalse(self.iad.PNG_SHOW_BASIS)
        self.assertFalse(self.gov.PNG_SHOW_BASIS)

    def test_render_checks_the_flag(self):
        source = _src("doc_render.py")
        self.assertIn("PNG_SHOW_BASIS", source)
        self.assertIn('if basis and show_basis:', source)

    def test_norms_are_kept_for_files(self):
        """Нормы никуда не исчезли: они остаются в полях документа."""
        self.assertIn("2.11", self.iad.basis_text())
        self.assertIn("статьи 13", self.gov.basis_text())


class V56ChatBeforeExportTests(unittest.TestCase):
    """3. Сначала обновляется чат, потом экспорт — в обеих формах."""

    def test_formed_does_not_upload_when_pending(self):
        source = _src("main.py")
        block = _block(source, "    def _on_document_formed(")
        self.assertIn("_pending_send_expected", block)
        # выгрузка идёт только вне режима отложенной отправки
        # v65: `pending_mode` считает и активную запись заявки формы.
        self.assertLess(block.index("pending_mode = "),
                        block.index("self._load_prompt(force=True)"))
        self.assertIn("pending_here", block)

    def test_auto_send_skips_pending_requests(self):
        source = _src("main.py")
        block = _block(source, "    def _auto_send_document_request(")
        self.assertIn('if getattr(self, "_pending_send_expected", False):', block)

    def test_restart_marks_the_deferred_upload(self):
        source = _src("main.py")
        block = _block(source, "    def _on_pending_send_restart(")
        self.assertIn("self._pending_send_expected = True", block)
        self.assertIn("_restart_browser_for_fresh_chat()", block)

    def test_iad_form_defers_the_send_too(self):
        source = _src("postanovlenie_ui.py")
        self.assertIn("restart_requested = pyqtSignal(object, str)", source)
        block = _block(source, "    def _form(self)")
        self.assertIn("self.restart_requested.emit(record, self.chat_txt_path)",
                      block)
        # окно больше не закрывается по «Сформировать»
        self.assertNotIn("self.accept()", block)

    def test_main_connects_the_iad_restart(self):
        source = _src("main.py")
        block = _block(source, "    def _open_postanovlenie(self)")
        self.assertIn("dlg.restart_requested.connect", block)
        self.assertIn("self._iad_dialog = dlg", block)


def _statements(block: str) -> str:
    """Только строки кода блока: комментарии и docstring-и не в счёт.

    Тесты проверяют фактические вызовы, а не упоминания в пояснениях:
    иначе комментарий «раньше здесь был перезапуск приложения» сам ломал
    бы проверку.
    """
    DQ = chr(34) * 3
    SQ = chr(39) * 3
    body = []
    in_docstring = False
    for line in block.splitlines():
        stripped = line.strip()
        quotes = stripped.count(DQ) + stripped.count(SQ)
        if in_docstring:
            if quotes % 2:
                in_docstring = False
            continue
        if quotes % 2:
            in_docstring = True
            continue
        if not stripped or stripped.startswith('#') or quotes:
            continue
        body.append(stripped)
    return '\n'.join(body)


class V56NoAppRestartTests(unittest.TestCase):
    """v56: после PNG приложение не перезапускается — обновляется чат."""

    def _block(self, source, start, end):
        chunk = source[source.index(start) + len(start):]
        return chunk[:chunk.index(end)]

    def test_iad_answer_soft_reloads_the_chat(self):
        source = _src("main.py")
        block = self._block(source, "    def _apply_iad_answer(", "    def _open_prompts_folder(")
        self.assertIn("self._schedule_fresh_chat_after_answer()", block)
        self.assertNotIn("restart_app()", _statements(block))

    def test_post_png_hook_is_soft(self):
        source = _src("main.py")
        block = self._block(source, "    def _restart_after_document(", "    @pyqtSlot(str)\n    def _apply_gov_answer")
        self.assertIn("self._schedule_fresh_chat_after_answer()", block)
        self.assertNotIn("restart_app()", _statements(block))
        self.assertNotIn("self._restart()", _statements(block))

    def test_soft_reload_does_not_kill_the_browser(self):
        source = _src("main.py")
        block = self._block(source, "    def _schedule_fresh_chat_after_answer(",
                            "    def _restart_after_document(")
        self.assertIn("FRESH_CHAT_AFTER_ANSWER_DELAY", block)
        self.assertIn("_restart_browser_for_fresh_chat", block)

    def test_dialog_message_hides_the_restart(self):
        source = _src("main.py")
        block = self._block(source, "    def _apply_iad_answer(", "    def _open_prompts_folder(")
        self.assertNotIn("Клиент будет перезапущен", block)
        # v63: чат больше не очищается после PNG — и сообщение об этом честно
        self.assertNotIn("Чат с ИИ очистится сам", block)
        self.assertIn("Чат с ИИ остаётся как есть", block)


class V56FactsAreInputTests(unittest.TestCase):
    """4. Блоки «Обстоятельства» — вход для ИИ, а не текст документа."""

    def setUp(self):
        import gov_case as cases
        self.cases = cases
        self.case = cases.GovCase(number="1")
        self.case.appeal_raw = "ОБРАЩЕНИЕ гражданина"
        self.case.facts_video = ("01:16 — сотрудник объясняет применение "
                                 "статьи 17.6 УК")
        self.case.own_decision = "Привлечь к ответственности"

    @staticmethod
    def _doc():
        import postanovlenie_prokuror_data as pgov
        return pgov.PostanovlenieProkurora(doc_number="AG-1", city="г. Лос-Сантос")

    def test_request_contains_only_own_text(self):
        request = self.cases.stage_request(self.case, self.cases.STAGE_MEASURE,
                                           self._doc())
        self.assertIn("01:16", request)
        # текст, который записал ИИ, обратно в ИИ не уходит
        self.case.remember_ai_facts({"facts_video": self.case.facts_video})
        request = self.cases.stage_request(self.case, self.cases.STAGE_MEASURE,
                                           self._doc())
        self.assertNotIn("01:16", request)

    def test_only_edits_go_back_to_ai(self):
        self.case.remember_ai_facts({"facts_video": self.case.facts_video})
        self.case.facts_video = self.case.facts_video + "\nУточнение: штраф 15 000"
        own = self.case.own_facts()
        self.assertNotIn("01:16", own.get("facts_video", ""))
        self.assertIn("15 000", own.get("facts_video", ""))

    def test_prosecutor_form_does_not_put_facts_into_document(self):
        source = _src("postanovlenie_prokuror_ui.py")
        block = _block(source, "    def _collect(self")
        self.assertIn('doc.set("situation", "")', block)
        self.assertNotIn("doc.prosecutor_facts = self.case.prosecutor_facts()",
                         block)

    def test_answer_is_written_back_into_the_blocks(self):
        source = _src("postanovlenie_prokuror_ui.py")
        block = _block(source, "    def apply_answer_texts(")
        self.assertIn('widget.setPlainText(established)', block)
        self.assertIn("remember_ai_facts", block)
        # «ПОСТАНОВИЛ» ИИ идёт в «г) Решение прокурора»
        self.assertIn('widget = self.texts.get("own_decision")', block)

    def test_iad_answer_is_written_back_into_situation(self):
        import postanovlenie_data as pdoc
        doc = pdoc.Postanovlenie(doc_number="LSCSD-IAD-1",
                                 situation="Текст ИИ про обстоятельства.")
        self.assertEqual(doc.own_situation(), "Текст ИИ про обстоятельства.")
        doc.remember_ai_facts({"situation": doc.situation})
        self.assertEqual(doc.own_situation(), "")
        doc.situation = doc.situation + "\nИ моя правка."
        self.assertEqual(doc.own_situation(), "И моя правка.")

    def test_iad_request_contains_only_own_text(self):
        import postanovlenie_data as pdoc
        doc = pdoc.Postanovlenie(doc_number="LSCSD-IAD-1",
                                 subject_name="Aliya Nomad",
                                 situation="Сотрудник нарушил форму одежды.")
        self.assertIn("форму одежды", doc.to_chat_txt())
        doc.remember_ai_facts({"situation": doc.situation})
        self.assertNotIn("форму одежды", doc.to_chat_txt())

    def test_clear_button_exists_in_both_forms(self):
        for name in ("postanovlenie_ui.py", "postanovlenie_prokuror_ui.py"):
            source = _src(name)
            self.assertIn("Очистить обстоятельства", source, name)
            self.assertIn("def _clear_facts(self)", source, name)


class V56ExportShapeTests(unittest.TestCase):
    """Выгрузка «Постановление»: то, что видно на PNG, больше не повторяется."""

    def setUp(self):
        try:
            import postanovlenie_data as pdoc
            import postanovlenie_prokuror_data as pgov
        except Exception as exc:                       # pragma: no cover
            self.skipTest(exc)
        self.pdoc, self.pgov = pdoc, pgov

    @needs_export
    def test_iad_export_has_no_double_postanovil(self):
        path = (EXPORT / "Постановление №1" / "LSCSD-IAD-1" / "ИИ-поля.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        doc = self.pdoc.Postanovlenie.from_dict(data.get("поля") or data)
        self.assertNotIn("постановил", doc.preamble().lower())
        self.assertEqual(doc.to_document_txt().lower().count("постановил"), 1)

    @needs_export
    def test_gov_export_facts_come_from_ai_only(self):
        """В «УСТАНОВИЛ» нет блока «ОБСТОЯТЕЛЬСТВА ПРОКУРОРА» из заявки."""
        stage = (EXPORT / "Обращение №1" / "AG-1" / "этап-2 — санкция")
        fields = json.loads((stage / "ИИ-поля.json").read_text(encoding="utf-8"))
        doc = self.pgov.PostanovlenieProkurora.from_dict(fields)
        facts = doc.facts_text()
        # в выгрузке «УСТАНОВИЛ» начинался с подсказки прокурора
        self.assertTrue(facts.startswith("15 ноября 2026 года"), facts[:120])
        self.assertNotIn("ОБСТОЯТЕЛЬСТВА ПРОКУРОРА", facts)
        # и сама подсказка не попала в документ отдельным абзацем
        self.assertNotIn("01:16 — сотрудник объясняет применение статьи 17.6 УК "
                         "тем, что гражданин не остановил", facts)
        # а в ответе ИИ этот же тайм-код уже пересказан официально
        self.assertIn("в 01:16 видеозаписи", facts)


if __name__ == "__main__":
    unittest.main()
