"""v51 — четыре замечания пользователя.

1. Итоговые/промежуточные файлы «Постановление»: лишние и недостающие элементы
   в PNG. Логика почти работала — здесь закрыты ОСТАВШИЕСЯ её баги:

   * «Указанные обстоятельства образуют нарушение: статьи 14 Регламента подачи
     обращений…» в постановлении о ПРИНЯТИИ (AG-1) — процедурная норма, а не
     состав, такой фразы быть не должно;
   * «…образуют нарушение:» терялось, если в «УСТАНОВИЛ» ИИ написал «Оснований
     для отказа … не выявлено» (проверка шла по слову «нарушение»);
   * «В подтверждение представлено: представлены.» (AG-2) — заглушка ИИ;
   * ответ ИИ этапа 2 печатался в папку этапа 1 (у AG-2 не было PNG санкции);
   * наказание из формы терялось, когда ИИ прислал готовые пункты резолюции.

2. «Сформировать» роняла окно: падение в момент убийства дерева браузера.

3. После «Сформировать» браузер оставался с ответом ИИ — чат не очищался.

4. «История» показывала каждую запись дважды.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _src(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _code(text: str) -> str:
    """Убирает комментарии: иначе подстрочный поиск ловит слова из пояснений."""
    out = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        out.append(line.split("  #")[0])
    return "\n".join(out)


def _block(text: str, start: str, end: str) -> str:
    chunk = text[text.index(start):]
    return chunk[:chunk.index(end)] if end in chunk else chunk


# --------------------------------------------------------------------------- #
#  1. PNG: «образуют нарушение» только там, где нарушение вменяется
# --------------------------------------------------------------------------- #

class V51OffencePhraseTests(unittest.TestCase):
    """Лишние абзацы в «УСТАНОВИЛ» (выгрузки AG-1 и AG-2)."""

    def setUp(self):
        import postanovlenie_prokuror_data as g
        self.g = g
        self.reglament = ("Регламент подачи обращений в Офис Генерального "
                          "прокурора штата San-Andreas")

    def test_procedural_norm_is_not_an_offence(self):
        """AG-1: принятие обращения — «образуют нарушение» про Регламент ложно."""
        doc = self.g.PostanovlenieProkurora(
            doc_number="AG-1",
            doc_type="О принятии обращения к собственному производству",
            violation_act=self.reglament, violation_clause="Статья 14",
            established="Обращение подлежит принятию к собственному производству.")
        self.assertNotIn("образуют нарушение", doc.facts_text())

    def test_real_offence_is_still_printed(self):
        doc = self.g.PostanovlenieProkurora(
            doc_number="AG-2",
            doc_type="О возбуждении уголовного дела по обращению",
            violation_act="Уголовный Кодекс штата Сан-Андреас",
            violation_clause="часть 1 статьи 15.1",
            established="Сотрудник допустил превышение полномочий.")
        self.assertIn("образуют нарушение", doc.facts_text())

    def test_negative_mention_does_not_swallow_the_offence(self):
        """AG-1: «Оснований для отказа … не выявлено» — не вменение."""
        doc = self.g.PostanovlenieProkurora(
            doc_number="AG-2",
            doc_type="О возбуждении уголовного дела по обращению",
            violation_act="Уголовный Кодекс штата Сан-Андреас",
            violation_clause="часть 1 статьи 15.1",
            established="Оснований для отказа, предусмотренных статьей 16 "
                        "Регламента, не выявлено.")
        self.assertIn("образуют нарушение", doc.facts_text())

    def test_offence_written_by_ai_is_not_repeated(self):
        doc = self.g.PostanovlenieProkurora(
            doc_type="О привлечении к административной ответственности",
            violation_act="Устав Federal Investigation Bureau",
            violation_clause="пункт 2.5",
            established="Указанные обстоятельства образуют нарушение: "
                        "пункта 2.5 Устава FIB.")
        self.assertEqual(doc.facts_text().count("образуют нарушение"), 1)


class V51SubjectRequisiteTests(unittest.TestCase):
    """«В отношении / Удостоверение / Отдел сотрудника» есть в заявке — нет в PNG."""

    def setUp(self):
        import postanovlenie_prokuror_data as g
        self.g = g

    def _doc(self, established):
        return self.g.PostanovlenieProkurora(
            doc_type="О возбуждении уголовного дела по обращению",
            established=established, subject_name="Jonny Zakonov",
            subject_id="55454", subject_department="LSCSD",
            subject_rank="Deputy Sheriff")

    def test_requisites_are_added_when_ai_omits_them(self):
        text = self._doc("Сотрудник мешал заседанию Сената.").facts_text()
        self.assertIn("55454", text)
        self.assertIn("LSCSD", text)
        self.assertIn("Jonny Zakonov", text)

    def test_requisites_are_not_repeated(self):
        doc = self._doc("Сотрудник LSCSD Jonny Zakonov, удостоверение № 55454, "
                        "мешал заседанию.")
        self.assertEqual(doc.facts_text().lower().count("удостоверение"), 1)

    def test_requisites_go_before_the_offence_conclusion(self):
        doc = self._doc("Сотрудник мешал заседанию. Указанные обстоятельства "
                        "образуют нарушение: части 1 статьи 15.1 Уголовного "
                        "Кодекса штата Сан-Андреас.")
        text = doc.facts_text()
        self.assertLess(text.index("удостоверение № 55454"),
                        text.index("образуют нарушение"))
        self.assertTrue(text.rstrip().endswith("Сан-Андреас."))

    def test_placeholders_are_not_printed_as_requisites(self):
        doc = self.g.PostanovlenieProkurora(
            subject_name="Jonny Zakonov", subject_id="не указано",
            subject_department="—")
        text = doc.facts_text()
        self.assertIn("Jonny Zakonov", text)
        self.assertNotIn("не указано", text)
        self.assertNotIn("удостоверение", text)


class V51EvidenceStubTests(unittest.TestCase):
    """«В подтверждение представлено: представлены.» (AG-2)."""

    def setUp(self):
        import postanovlenie_prokuror_data as g
        self.g = g

    def test_evidence_stub_is_recognized(self):
        for stub in ("представлены", "представлено", "приложены", "имеются",
                     "не представлены", "отсутствуют"):
            self.assertTrue(self.g.is_evidence_stub(stub), stub)

    def test_real_evidence_is_not_a_stub(self):
        for value in ("https://disk.yandex.ru/i/pK1j0wOeEFyjmQ",
                      "видеозапись заседания, тайм-коды 05:01 и 08:23",
                      "скриншот переписки"):
            self.assertFalse(self.g.is_evidence_stub(value), value)

    def test_stub_is_not_printed(self):
        doc = self.g.PostanovlenieProkurora(
            doc_type="О возбуждении уголовного дела по обращению",
            established="Сотрудник допустил превышение полномочий.",
            evidence="представлены")
        self.assertNotIn("В подтверждение", doc.facts_text())

    def test_stub_sentence_written_by_ai_is_removed(self):
        text = ("Сотрудник допустил превышение полномочий. "
                "В подтверждение представлено: представлены. Конец.")
        cleaned = self.g.strip_evidence_stub_sentences(text)
        self.assertNotIn("представлены.", cleaned)
        self.assertIn("превышение полномочий", cleaned)

    def test_real_evidence_sentence_is_kept(self):
        text = ("Сотрудник допустил превышение полномочий. В подтверждение "
                "представлено: https://disk.yandex.ru/i/pK1j0wOeEFyjmQ")
        cleaned = self.g.strip_evidence_stub_sentences(text)
        self.assertIn("https://disk.yandex.ru/i/pK1j0wOeEFyjmQ", cleaned)


class V51PenaltyTests(unittest.TestCase):
    """Наказание из формы не теряется в готовой резолюции ИИ."""

    def test_penalty_is_added_to_ai_points(self):
        import postanovlenie_prokuror_data as g
        doc = g.PostanovlenieProkurora(
            doc_type="О привлечении к административной ответственности",
            penalty="штраф 30 000 $")
        doc.points = ["Признать сотрудника виновным в нарушении статьи 28."]
        self.assertTrue(any("30 000" in p for p in doc.decision_points()))

    def test_penalty_named_by_ai_is_not_repeated(self):
        import postanovlenie_prokuror_data as g
        doc = g.PostanovlenieProkurora(penalty="штраф 30 000 $")
        doc.points = ["Назначить наказание: штраф 30 000 $."]
        self.assertEqual(len(doc.decision_points()), 1)


class V51StageFolderTests(unittest.TestCase):
    """Ответ этапа 2 печатается в папку этапа 2, а не «этап-1»."""

    def test_stage_kind_wins_over_doc_type(self):
        src = _code(_block(_src("main.py"), "def _apply_gov_answer(",
                           "def _open_prompts_folder("))
        self.assertIn("doc.get(\"case_stage_kind\")", src)
        self.assertIn("_doc_stage_kind", src)
        self.assertLess(src.index("doc.get(\"case_stage_kind\")"),
                        src.index("doc.is_final()"))

    def test_pending_fields_restore_the_stage_document(self):
        """После чистого чата документ этапа восстанавливается с диска."""
        main = _src("main.py")
        self.assertIn("def _load_pending_fields(", main)
        block = _code(_block(main, "def _load_pending_fields(",
                             "def _resume_pending_send("))
        self.assertIn("Заявка-поля.json", block)
        self.assertIn("self._document_doc = doc", block)
        self.assertIn("case_stage_kind", block)
        self.assertIn("_load_pending_fields(record, path)",
                      _code(_block(main, "def _load_pending_document(",
                                   "def _load_pending_fields(")))


# --------------------------------------------------------------------------- #
#  2 и 3. «Сформировать»: без убийства браузера, чат очищается
# --------------------------------------------------------------------------- #

class V51FreshChatTests(unittest.TestCase):
    """п.2: окно не падает; п.3: чат обновляется после ответа ИИ."""

    def setUp(self):
        self.main = _src("main.py")

    def test_fresh_chat_reloads_the_page_instead_of_killing_the_browser(self):
        block = _code(_block(self.main, "def _restart_browser_for_fresh_chat(",
                             "def _fresh_chat_reload_page("))
        # Мягкий путь — раньше жёсткого, и он не трогает процессы браузера.
        self.assertIn("_fresh_chat_reload_page()", block)
        self.assertIn("_restart_browser_hard(", block)
        self.assertLess(block.index("_fresh_chat_reload_page()"),
                        block.index("_restart_browser_hard("))
        self.assertNotIn("host.stop()", block)
        self.assertNotIn("kill_tree(", block)

    def test_soft_reload_uses_page_navigate(self):
        block = _code(_block(self.main, "def _fresh_chat_reload_page(",
                             "def _fresh_chat_reload_watchdog("))
        self.assertIn('page.send("Page.navigate"', block)
        self.assertIn("_start_web_automation()", block)
        self.assertNotIn("kill_tree", block)

    def test_hard_restart_is_the_only_place_that_kills_the_tree(self):
        block = _code(_block(self.main, "def _restart_browser_hard(",
                             "def _load_pending_document("))
        self.assertIn("host.stop()", block)
        self.assertIn("QTimer.singleShot(400, self._start_chrome)", block)

    def test_timeout_falls_back_to_hard_restart(self):
        block = _code(_block(self.main, "def _fresh_chat_reload_watchdog(",
                             "def _restart_browser_hard("))
        self.assertIn("_restart_browser_hard(", block)

    def test_chat_is_cleared_after_the_answer(self):
        """п.3: ответ ИИ больше не оставляет браузер с текстом ответа."""
        block = _code(_block(self.main, "def _apply_gov_answer(",
                             "def _open_prompts_folder("))
        self.assertIn("self._schedule_fresh_chat_after_answer()", block)
        self.assertNotIn("self._restart_after_document()", block)

    def test_answer_cleanup_is_soft_and_keeps_the_form(self):
        block = _code(_block(self.main, "def _schedule_fresh_chat_after_answer(",
                             "def _restart_after_document("))
        self.assertIn("_restart_browser_for_fresh_chat", block)
        self.assertNotIn("_restart()", block)
        self.assertNotIn("dialog.close()", block)

    def test_pending_record_is_kept_in_memory_before_restart(self):
        block = _code(_block(self.main, "def _on_pending_send_restart(",
                             "def _pending_send_record("))
        self.assertIn("self._pending_send_record_data = record", block)
        self.assertLess(block.index("self._pending_send_record_data = record"),
                        block.index("_restart_browser_for_fresh_chat()"))


class V51DeadWindowGuardTests(unittest.TestCase):
    """п.2: никаких Win32-вызовов по убитому окну браузера."""

    def test_host_stop_marks_it_stopped(self):
        src = _src("main.py")
        block = _code(_block(src, "    def stop(self):", "    def alive_window("))
        self.assertIn("self.stopped = True", block)
        self.assertLess(block.index("self.stopped = True"),
                        block.index("self._kill_all()"))

    def test_alive_window_checks_process_and_hwnd(self):
        src = _src("main.py")
        block = _code(_block(src, "def alive_window(", "def sync("))
        self.assertIn("self.alive()", block)
        self.assertIn("win32_embed.is_window", block)

    def test_win32_entry_points_check_the_window(self):
        src = _code(_src("main.py"))
        for name in ("def sync(", "def poll_focus("):
            block = _block(src, name, "\n    def ")
            self.assertIn("alive_window()", block, name)
        host_block = _block(src, "class NativeHost", "\nclass BrowserFetchWorker")
        self.assertIn("alive_window()", _block(host_block, "def hide_taskbar(",
                                               "\n    def "))

    def test_focus_timer_and_zoom_skip_a_stopped_host(self):
        src = _code(_src("main.py"))
        poll = _block(src, "def _poll_browser_focus(", "\n    def ")
        self.assertIn("getattr(host, \"stopped\", False)", poll)
        zoom = _block(src, "def _ensure_zoom(", "\n    def ")
        self.assertIn("stopped", zoom)

    def test_dead_tree_is_not_touched(self):
        src = _code(_src("win32_embed.py"))
        block = _block(src, "def hide_stray_windows(", "\n    hidden = []")
        self.assertIn("any_alive(", block)
        helper = _block(src, "def any_alive(", "\ndef kill_tree(")
        self.assertIn("GetExitCodeProcess", helper)
        self.assertIn("STILL_ACTIVE", helper)


# --------------------------------------------------------------------------- #
#  4. «История»: одна запись на документ
# --------------------------------------------------------------------------- #

class V51HistoryTests(unittest.TestCase):
    def setUp(self):
        import postanovlenie_prokuror_data as g
        self.g = g

    def test_form_snapshot_is_a_draft_and_is_not_listed(self):
        with tempfile.TemporaryDirectory() as tmp:
            history = self.g.GovHistory(Path(tmp))
            form = self.g.PostanovlenieProkurora(
                doc_number="AG-1", doc_type="Определит ИИ по обращению",
                subject_name="Jonny Zakonov", case_number="1")
            history.save(form, draft=True)
            self.assertEqual(history.items(), [])
            self.assertEqual(history.labels(), [])

    def test_answer_replaces_the_draft(self):
        with tempfile.TemporaryDirectory() as tmp:
            history = self.g.GovHistory(Path(tmp))
            form = self.g.PostanovlenieProkurora(
                doc_number="AG-1", doc_type="Определит ИИ по обращению",
                subject_name="Jonny Zakonov", case_number="1")
            history.save(form, draft=True)
            answer = self.g.PostanovlenieProkurora(
                doc_number="AG-1",
                doc_type="О принятии обращения к собственному производству",
                subject_name="Jonny Zakonov", case_number="1", source="ai")
            history.replace(answer)
            self.assertEqual(len(history.items()), 1)
            self.assertEqual(len(history.labels()), 1)
            label = history.labels()[0]
            self.assertIn("AG-1", label)
            self.assertEqual(history.load_index(0).get("doc_type"),
                             "О принятии обращения к собственному производству")

    def test_repeated_saves_of_one_document_are_one_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            history = self.g.GovHistory(Path(tmp))
            doc = self.g.PostanovlenieProkurora(
                doc_number="AG-2", subject_name="Jonny Zakonov")
            history.save(doc)
            history.save(doc)
            # Один документ — одна строка, сколько бы раз его ни сохраняли.
            self.assertEqual(len(history.items()), 1)

    def test_history_payload_keeps_document_time(self):
        doc = self.g.PostanovlenieProkurora(doc_number="AG-1")
        self.assertEqual(doc.history_payload()["created_at"],
                         doc.history_payload()["created_at"])
        self.assertEqual(doc.history_payload()["created_at"],
                         float(doc.created_at))

    def test_form_and_answer_paths_are_wired(self):
        ui = _code(_src("postanovlenie_prokuror_ui.py"))
        self.assertIn("self.history.save(doc, draft=True)", ui)
        main = _code(_src("main.py"))
        self.assertIn("self._document_gov_history().replace(doc)", main)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
