"""v43.4: отправка заявки только после реальной готовности вложений.

Разбор сессии `logs_43v` (20261109-140643-19496-ac7eb1):

    11:06:51.125  pending_send.sent               запрос ушёл
    11:06:51.157  upload.state present=0          в чате НОЛЬ файлов
    11:06:51.561  upload.attempt pdf              Memphis.pdf
    11:06:51.905  upload.attempt template         Шаблон.txt
    11:06:52.511  upload.attachments_confirmed    файлы готовы

Запрос уходил на 1.4 с раньше, чем файлы вообще начинали прикрепляться.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chat_ready as cr  # noqa: E402

REQUIRED = ["Memphis.pdf", "Шаблон.txt", "Заявка (отправлено в чат).txt"]
NOW = 1000.0
SETTLED = NOW - (cr.SETTLE_SECONDS + 0.5)


def _state(present, uploading=False, names=None, probe_ok=True):
    return {"present": present, "uploading": uploading,
            "names": REQUIRED[:present] if names is None else names,
            "probe_ok": probe_ok}


class ChatReadyTests(unittest.TestCase):
    """Готовность чата: файлы на месте, загрузка не идёт, выдержка прошла."""

    def test_state_from_the_log_is_not_ready(self):
        """Ровно как в логе: present=0 — отправлять нельзя."""
        result = cr.check(_state(0, names=[]), REQUIRED, confirmed_at=NOW, now=NOW)
        self.assertFalse(result["ready"])
        self.assertEqual(result["reason"], cr.REASON_INCOMPLETE)

    def test_uploading_blocks_sending(self):
        result = cr.check(_state(1, uploading=True, names=["Memphis.pdf"]),
                          REQUIRED, confirmed_at=SETTLED, now=NOW)
        self.assertFalse(result["ready"])
        self.assertEqual(result["reason"], cr.REASON_UPLOADING)

    def test_partial_files_block_sending(self):
        result = cr.check(_state(2, names=["Memphis.pdf", "Шаблон.txt"]),
                          REQUIRED, confirmed_at=SETTLED, now=NOW)
        self.assertFalse(result["ready"])
        self.assertEqual(result["reason"], cr.REASON_INCOMPLETE)
        self.assertIn("Заявка (отправлено в чат).txt", result["missing"])

    def test_all_files_without_settle_are_not_ready(self):
        """Файлы уже видны, но 1–2 секунды ещё не прошли — ждём."""
        result = cr.check(_state(3), REQUIRED, confirmed_at=NOW - 0.5, now=NOW)
        self.assertFalse(result["ready"])
        self.assertEqual(result["reason"], cr.REASON_SETTLING)

    def test_all_files_settled_are_ready(self):
        result = cr.check(_state(3), REQUIRED, confirmed_at=SETTLED, now=NOW)
        self.assertTrue(result["ready"])
        self.assertEqual(result["present"], 3)

    def test_without_confirmation_nothing_is_sent(self):
        result = cr.check(_state(3), REQUIRED, confirmed_at=None, now=NOW)
        self.assertFalse(result["ready"])
        self.assertEqual(result["reason"], cr.REASON_STALE)

    def test_extra_files_do_not_block_forever(self):
        """v43.5: «лишние файлы» больше не вечный висяк — они только в журнале."""
        names = REQUIRED + ["Чужой.pdf", "Старый.txt"]
        result = cr.check(_state(5, names=names), REQUIRED,
                          confirmed_at=SETTLED, now=NOW)
        self.assertTrue(result["ready"])
        self.assertEqual(result["reason"], "")
        self.assertEqual(sorted(result.get("extra") or []),
                         ["Старый.txt", "Чужой.pdf"])

    def test_timeout_expires_the_wait(self):
        self.assertFalse(cr.expired(10))
        self.assertTrue(cr.expired(cr.READY_TIMEOUT + 1))

    def test_settle_constant_matches_the_log_delay(self):
        """По логу файлам нужно 1–2 секунды — выдержка не меньше двух."""
        self.assertGreaterEqual(cr.SETTLE_SECONDS, 2.0)


class DuplicateChipsTests(unittest.TestCase):
    """v43.5: сессия 20261109-143836-13424-edee58 — «лишние файлы» навсегда.

    В чате три вложения, но страница отдавала ШЕСТЬ чипов (они вложены друг в
    друга), и имена дублировались:

        present=6  names=["Memphis.pdf", "Шаблон.txt", "Проверка обращения
                          (заявка).txt", "Memphis.pdf", …]

    Из-за этого заявка висела с причиной «в чате лишние файлы» и не уходила.
    """

    NAMES = ["Memphis.pdf", "Шаблон.txt", "Проверка обращения (заявка).txt"]

    def test_duplicated_names_are_counted_once(self):
        state = {"present": 6, "uploading": False,
                 "names": self.NAMES + self.NAMES}
        info = cr.normalize(state)
        self.assertEqual(info["present"], 3)
        self.assertEqual(info["names"], self.NAMES)

    def test_log_case_is_ready_to_send(self):
        state = {"present": 6, "uploading": False,
                 "names": self.NAMES + self.NAMES}
        result = cr.check(state, self.NAMES, confirmed_at=SETTLED, now=NOW)
        self.assertTrue(result["ready"], result.get("reason"))
        self.assertEqual(result["reason"], "")

    def test_case_insensitive_duplicates(self):
        state = {"present": 4, "uploading": False,
                 "names": ["Memphis.pdf", "memphis.PDF"]}
        self.assertEqual(cr.normalize(state)["present"], 1)

    def test_empty_names_are_ignored(self):
        state = {"present": 3, "uploading": False, "names": ["", " ", ""]}
        info = cr.normalize(state)
        self.assertEqual(info["names"], [])

    def test_stream_merges_nested_chips(self):
        """JS не считает вложенные чипы дважды."""
        src = (Path(__file__).resolve().parent.parent / "main.py").read_text(
            encoding="utf-8")
        js = src[src.index("JS_CHECK_FILES = r"):]
        js = js[:js.index('"""', js.index('"""') + 3)]
        self.assertIn("parent.closest('div.ArblTe')", js)
        self.assertIn("chips.length ? chips : all", js)

    def test_client_deduplicates_names(self):
        src = (Path(__file__).resolve().parent.parent / "main.py").read_text(
            encoding="utf-8")
        self.assertIn("def _unique_names(", src)
        block = src[src.index("def check_state("):]
        block = block[:block.index("def _unique_names")]
        self.assertIn("_unique_names(names)", block)


class RequiredNamesTests(unittest.TestCase):
    def test_names_include_the_request_file(self):
        names = cr.required_names("/a/Memphis.pdf", "/b/Шаблон.txt",
                                  "/c/Заявка (отправлено в чат).txt")
        self.assertEqual(names, ["Memphis.pdf", "Шаблон.txt",
                                 "Заявка (отправлено в чат).txt"])

    def test_template_is_required_even_without_path(self):
        names = cr.required_names("/a/Memphis.pdf", None, None, "Шаблон.txt")
        self.assertIn("Шаблон.txt", names)

    def test_empty_paths_are_skipped(self):
        self.assertEqual(cr.required_names("", None, ""), [])

    def test_message_is_human_readable(self):
        self.assertIn("загружаются", cr.wait_message(cr.REASON_UPLOADING))
        self.assertIn("ждём", cr.wait_message(cr.REASON_INCOMPLETE))


class UploadGateWiringTests(unittest.TestCase):
    """Проверки в самого клиента: где стоит «ворота» готовности."""

    def setUp(self):
        self.src = (Path(__file__).resolve().parent.parent / "main.py").read_text(
            encoding="utf-8")

    def test_send_waits_for_gate(self):
        block = self.src[self.src.index("def _try_send_pending"):]
        block = block[:block.index("def _on_pending_send_done")]
        self.assertIn("cread.check(", block)
        self.assertIn("cread.required_names(", block)
        self.assertIn("cread.wait_message(", block)
        # старая проверка «только pdf_attached» больше не управляет отправкой
        self.assertNotIn('if not getattr(self, "pdf_attached", False) and', block)
        # отправка не идёт в главном потоке (внутри были sleep и CDP)
        self.assertIn('name="PendingSend"', block)

    def test_confirmation_time_is_recorded(self):
        block = self.src[self.src.index("def _on_files_attached"):]
        block = block[:block.index("def _refresh_balance")]
        self.assertIn("_upload_confirmed_at = time.monotonic()", block)

    def test_stream_does_not_confirm_by_counter_only(self):
        # именно поток вложений: первым `def run` в файле идёт другая загрузка
        # метод run() стоит последним в классе — берём до следующего класса
        start = self.src.index("class UploadThread(QThread):")
        block = self.src[start:]
        block = block[:block.index("\nclass ", 1)]
        self.assertIn("def run(self):", block)
        self.assertIn("missing = list(self._all_files())", block)
        self.assertIn("complete = False", block)


class FreshChatTests(unittest.TestCase):
    """Чистый чат: перезапускается браузер внутри GUI, не приложение."""

    def setUp(self):
        self.src = (Path(__file__).resolve().parent.parent / "main.py").read_text(
            encoding="utf-8")

    def test_buttons_do_not_restart_the_application(self):
        block = self.src[self.src.index("def _on_pending_send_restart"):]
        block = block[:block.index("def _pending_send_record")]
        self.assertNotIn("self._restart()", block)
        self.assertIn("_restart_browser_for_fresh_chat", block)

    def test_browser_restart_reuses_start_chrome(self):
        block = self.src[self.src.index("def _restart_browser_for_fresh_chat"):]
        block = block[:block.index("def _load_pending_document")]
        self.assertIn("host.stop()", block)
        self.assertIn("self._start_chrome", block)
        self.assertNotIn("restart_app", block)

    def test_intake_context_is_restored_after_fresh_chat(self):
        block = self.src[self.src.index("def _resume_pending_send"):]
        block = block[:block.index("def _try_send_pending")]
        self.assertIn("_gov_intake_case", block)
        self.assertIn("_gov_intake_text", block)

    def test_request_file_is_reattached_in_same_session(self):
        block = self.src[self.src.index("def _load_pending_document"):]
        block = block[:block.index("def _resume_pending_send")]
        self.assertIn("self.document_path = path", block)
        self.assertIn("source_path", block)

    def test_single_upload_path_after_fresh_chat(self):
        block = self.src[self.src.index("if not self._resume_pending_send():"):]
        self.assertIn("self._load_prompt()", block[:200])


class ExceptionAfterAnswerTests(unittest.TestCase):
    """Ошибка после ответа ИИ (AttributeError в sys.excepthook) — разбор."""

    def test_case_lookup_cannot_raise(self):
        import gov_case as cases
        find = getattr(cases.GovCaseStore, "find", None)
        self.assertIsNotNone(find, "GovCaseStore.find должен существовать")
        import inspect
        src = inspect.getsource(find)
        self.assertIn("except Exception", src)

    def test_answer_handler_uses_safe_lookup(self):
        src = (Path(__file__).resolve().parent.parent / "main.py").read_text(
            encoding="utf-8")
        block = src[src.index("def _apply_gov_intake"):]
        block = block[:block.index("def _gov_intake_watch_stop")]
        self.assertIn("store.find(", block)
        self.assertNotIn("store.load(", block)

    def test_intake_checks_is_saved_in_the_case(self):
        import gov_case as cases
        case = cases.GovCase(number="1")
        self.assertIn("intake_checks", case.to_dict())
        case.intake_checks = 3
        again = cases.GovCase.from_dict(case.to_dict())
        self.assertEqual(again.intake_checks, 3)


if __name__ == "__main__":
    unittest.main()
