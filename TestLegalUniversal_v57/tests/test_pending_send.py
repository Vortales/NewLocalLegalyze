"""v43.2: перезапуск клиента перед отправкой заявки в ИИ (pending_send)."""

import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pending_send as ps  # noqa: E402


def _P(name):
    return Path(__file__).resolve().parent.parent / name


class PendingSendRecordTests(unittest.TestCase):
    """Сохранение и подхват заявки между запусками."""

    def test_record_is_saved_and_loaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = ps.build_record(kind="document", request_text="заявка",
                                     case_number="3", stage_kind="measure",
                                     stage_index=2,
                                     doc_type="О привлечении к ответственности")
            ps.save(tmp, record)
            loaded = ps.load(tmp)
            self.assertEqual(loaded["kind"], "document")
            self.assertEqual(loaded["request_text"], "заявка")
            self.assertEqual(loaded["case_number"], "3")
            self.assertEqual(loaded["stage_index"], 2)
            self.assertEqual(loaded["state"], ps.STATE_WAITING)

    def test_record_file_lives_next_to_postanovleniya(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(ps.record_path(tmp), Path(tmp) / "pending_send.json")

    def test_sent_record_is_not_repeated(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = ps.build_record(kind="document", request_text="заявка")
            ps.save(tmp, record)
            ps.mark_sent(tmp, ps.load(tmp))
            self.assertFalse(ps.record_path(tmp).exists())
            self.assertFalse(ps.is_fresh(ps.load(tmp)))

    def test_stale_record_is_not_sent(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = ps.build_record(kind="intake", request_text="проверка")
            record["created_at"] = time.time() - 20 * 60
            ps.save(tmp, record)
            self.assertFalse(ps.is_fresh(ps.load(tmp)))

    def test_empty_record_is_not_sent(self):
        with tempfile.TemporaryDirectory() as tmp:
            ps.save(tmp, ps.build_record(kind="intake", request_text="   "))
            self.assertFalse(ps.is_fresh(ps.load(tmp)))

    def test_broken_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            ps.record_path(tmp).write_text("{битый json", encoding="utf-8")
            self.assertEqual(ps.load(tmp), {})
            self.assertFalse(ps.is_fresh({}))

    def test_cancel_removes_the_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = ps.build_record(kind="document", request_text="заявка")
            ps.save(tmp, record)
            ps.mark_cancelled(tmp, ps.load(tmp))
            self.assertFalse(ps.record_path(tmp).exists())

    def test_describe_names_the_case_and_stage(self):
        record = ps.build_record(kind="document", request_text="x",
                                 case_number="7", stage_index=2,
                                 doc_type="О привлечении к административной ответственности")
        text = ps.describe(record)
        self.assertIn("№7", text)
        self.assertIn("этап 2", text)
        self.assertIn("привлечении", text)
        self.assertEqual(ps.describe(ps.build_record(kind="intake", request_text="x")),
                         "Проверка обращения")


class PendingSendWiringTests(unittest.TestCase):
    """Кнопки формы перезапускают клиент, отправка — после старта."""

    def setUp(self):
        self.ui = (_P("postanovlenie_prokuror_ui.py")).read_text(encoding="utf-8")
        self.main = (_P("main.py")).read_text(encoding="utf-8")

    def test_form_has_restart_signal(self):
        self.assertIn("restart_requested = pyqtSignal(object, str)", self.ui)
        self.assertIn("import pending_send", self.ui)

    def test_intake_check_queues_instead_of_sending(self):
        block = self.ui[self.ui.index("def _intake_check"):]
        block = block[:block.index("def _intake_recheck")]
        self.assertIn("pending_send.build_record", block)
        self.assertIn('kind="intake"', block)
        self.assertIn("restart_requested.emit", block)
        # сама отправка в чат из формы больше не выполняется
        self.assertNotIn("self.intake_requested.emit(text", block)

    def test_form_queues_document_instead_of_sending(self):
        block = self.ui[self.ui.index("def _form("):]
        block = block[:block.index("def showEvent")]
        self.assertIn('kind="document"', block)
        self.assertIn("restart_requested.emit", block)
        self.assertIn("pending_send.save", block)

    def test_main_queues_and_starts_fresh_chat(self):
        """v43.4: кнопки формы перезапускают ТОЛЬКО браузер, не приложение."""
        self.assertIn("import pending_send as psend", self.main)
        self.assertIn("dlg.restart_requested.connect(self._on_pending_send_restart)",
                      self.main)
        block = self.main[self.main.index("def _on_pending_send_restart"):]
        block = block[:block.index("def _pending_send_record")]
        # приложение больше не перезапускается целиком
        self.assertNotIn("self._restart()", block)
        self.assertIn("self._restart_browser_for_fresh_chat()", block)
        self.assertIn("psend.save", block)

    def test_fresh_chat_restarts_only_the_browser(self):
        block = self.main[self.main.index("def _restart_browser_for_fresh_chat"):]
        block = block[:block.index("def _load_pending_document")]
        self.assertIn("host.stop()", block)          # гасим окно браузера
        self.assertIn("self._start_chrome", block)   # поднимаем заново
        self.assertNotIn("restart_app", block)       # без перезапуска клиента
        self.assertNotIn("QApplication", block)

    def test_resume_runs_after_chat_is_ready(self):
        block = self.main[self.main.index("if not self._resume_pending_send():"):]
        self.assertIn("self._load_prompt()", block[:200])

    def test_no_double_send_with_upload_thread(self):
        block = self.main[self.main.index("def _on_files_attached"):]
        block = block[:block.index("def _refresh_balance")]
        self.assertIn("_pending_send_record_data", block)
        self.assertIn("_auto_send_document_request", block)

    def test_send_waits_for_attachments(self):
        block = self.main[self.main.index("def _try_send_pending"):]
        block = block[:block.index("def _pending_send_stop")]
        self.assertIn("pdf_attached", block)
        self.assertIn("Input.insertText", block)
        self.assertIn("JS_STOP_AND_SEND", block)

    def test_intake_watch_starts_after_resumed_send(self):
        block = self.main[self.main.index("def _try_send_pending"):]
        block = block[:block.index("def _pending_send_stop")]
        self.assertIn("_watch_document_answer", block)
        self.assertIn('kind == "intake"', block)


if __name__ == "__main__":
    unittest.main()
