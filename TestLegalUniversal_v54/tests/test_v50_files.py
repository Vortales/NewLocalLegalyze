"""tests/test_v50_files.py — «минимум текста в поле запроса, больше файлов» (v50).

Замечание пользователя:

    «ранее ты напрямую вставлял текст в окно запроса ИИ при нажатии
    „Проверка ИИ“ и „Сформировать“, теперь текст обращения экспортируй в файл
    „Обращение.txt“ и экспортируй его в ИИ, теперь минимум чистого текста ИИ
    в поле запроса, больше файлов, в которых вся эта информация».

Проверяется ровно это: обращение — отдельным файлом, оба файла уходят в чат,
в поле запроса — короткая служебная фраза, и из неё ничего не теряется
(этап, вид решения, формат ответа остаются в файле заявки).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import ai_request
import chat_ready as cr
import gov_case as cases
import pending_send as psend
import postanovlenie_prokuror_data as gdata

ROOT = Path(__file__).resolve().parent.parent


def _src(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _block(src: str, start: str, end: str) -> str:
    """Участок исходника между двумя маркерами (без чужих методов)."""
    first = src.index(start)
    return src[first:src.index(end, first)]


class AppealFileTests(unittest.TestCase):
    """Обращение экспортируется в «Обращение.txt» рядом с заявкой."""

    def test_file_name_and_title(self):
        self.assertEqual(ai_request.APPEAL_FILE_NAME, "Обращение.txt")
        text = ai_request.appeal_file_text("1. Заявитель: Mark Skywalker")
        self.assertIn(ai_request.APPEAL_FILE_TITLE, text)
        self.assertIn("1. Заявитель: Mark Skywalker", text)

    def test_file_is_written_next_to_the_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            request = Path(tmp) / "Заявка (отправлено в чат).txt"
            request.write_text("ЗАЯВКА", encoding="utf-8")
            path = ai_request.write_appeal_file(Path(tmp), "Текст обращения")
            self.assertEqual(path.name, "Обращение.txt")
            self.assertEqual(path.parent, request.parent)
            self.assertIn("Текст обращения", path.read_text(encoding="utf-8"))

    def test_empty_appeal_does_not_break_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = ai_request.write_appeal_file(Path(tmp), "")
            self.assertTrue(path.exists())
            self.assertIn(ai_request.APPEAL_FILE_TITLE,
                          path.read_text(encoding="utf-8"))


class ShortMessageTests(unittest.TestCase):
    """В поле запроса — короткая фраза, а не простыня текста."""

    APPEAL = ("От: гражданина штата Сан-Андреас Lordik Claw\n"
              "1. Данные о заявителе: Lordik Claw 83461\n"
              "5. Подробное описание ситуации: Сотрудник FIB не пресёк драку.\n"
              "8. Доказательства правонарушения: Видеофиксация")

    def test_intake_message_points_to_files(self):
        text = ai_request.intake_message()
        self.assertIn("Обращение.txt", text)
        self.assertIn("заявк", text.lower())
        self.assertLess(len(text), 400, "в поле запроса не должно быть простыни")

    def test_document_message_points_to_both_files(self):
        text = ai_request.document_message("Заявка (отправлено в чат).txt")
        self.assertIn("Заявка (отправлено в чат).txt", text)
        self.assertIn("Обращение.txt", text)
        self.assertLess(len(text), 600)

    def test_intake_request_does_not_contain_the_appeal(self):
        case = cases.GovCase(number="1", appeal_raw=self.APPEAL)
        text = cases.intake_request(case, "Marcus Nomad")
        self.assertNotIn("Lordik Claw 83461", text)
        self.assertIn("Обращение.txt", text)
        # смысловая часть заявки осталась в файле заявки
        self.assertIn("Принято", text)
        self.assertIn("Основание", text)
        self.assertFalse(cases.parse_intake_answer(text)["ok"])

    def test_document_request_does_not_contain_the_appeal(self):
        doc = gdata.PostanovlenieProkurora()
        doc.set("appeal_raw", self.APPEAL)
        doc.set("situation", "Обстоятельства прокурора")
        text = doc.to_chat_txt()
        self.assertNotIn("Lordik Claw 83461", text)
        self.assertIn("Обращение.txt", text)
        # обстоятельства прокурора остаются в заявке: они не обращение
        self.assertIn("Обстоятельства прокурора", text)


class RecordTests(unittest.TestCase):
    """Запись отложенной отправки: два разных текста и два вложения."""

    def test_message_and_request_are_separate(self):
        record = psend.build_record(
            kind="intake", request_text="ЗАЯВКА ЦЕЛИКОМ",
            message_text="Проверь обращение", appeal_path="/c/Обращение.txt")
        self.assertEqual(record["request_text"], "ЗАЯВКА ЦЕЛИКОМ")
        self.assertEqual(record["message_text"], "Проверь обращение")
        self.assertEqual(record["appeal_path"], "/c/Обращение.txt")

    def test_old_record_without_message_sends_the_request(self):
        # совместимость: у записей до v50 message_text пуст
        record = psend.build_record(kind="document", request_text="ЗАЯВКА")
        self.assertEqual(record["message_text"], "")

    def test_both_files_are_required_in_the_chat(self):
        names = cr.required_names("/a/Memphis.pdf", "/b/Шаблон.txt",
                                  "/c/Заявка.txt", "Шаблон.txt",
                                  "/d/Обращение.txt")
        self.assertIn("Заявка.txt", names)
        self.assertIn("Обращение.txt", names)


class WiringTests(unittest.TestCase):
    """Клиент и форма действительно отправляют файлы, а не текст."""

    def setUp(self):
        self.main = _src("main.py")
        self.ui = _src("postanovlenie_prokuror_ui.py")

    def test_form_exports_the_appeal_file_for_intake(self):
        body = _block(self.ui, "def _intake_check", "def _mark_request_sent")
        self.assertIn("ai_request.write_appeal_file", body)
        self.assertIn("message_text=ai_request.intake_message", body)
        self.assertIn("appeal_path=str(appeal_path)", body)

    def test_form_exports_the_appeal_file_for_document(self):
        body = _block(self.ui, "def _form", "def showEvent")
        self.assertIn("ai_request.write_appeal_file", body)
        self.assertIn("message_text=ai_request.document_message", body)
        self.assertIn("appeal_path=str(appeal_path)", body)

    def test_pending_send_prints_the_short_message(self):
        body = _block(self.main, "def _try_send_pending",
                      "def _on_pending_send_done")
        self.assertIn('record.get("message_text")', body)
        self.assertIn("Input.insertText", body)

    def test_upload_keeps_the_appeal_file(self):
        start = self.main.index("class UploadThread(QThread):")
        body = self.main[start:self.main.index("\nclass ", start + 1)]
        self.assertIn("self.appeal_path", body)
        self.assertIn("self.appeal_path = appeal_path", body)
        self.assertIn("_chat_has_file(names, Path(self.appeal_path).name)", body)
        self.assertIn("+ (1 if self.appeal_path else 0)", body)

    def test_appeal_file_is_restored_after_fresh_chat(self):
        body = _block(self.main, "def _load_pending_document",
                      "def _resume_pending_send")
        self.assertIn('record.get("appeal_path")', body)
        self.assertIn("self.appeal_path = Path(appeal_path)", body)

    def test_appeal_file_is_dropped_after_send(self):
        body = _block(self.main, "def _drop_document_attachment",
                      "def _document_case_store")
        self.assertIn("self.appeal_path = None", body)
        self.assertIn("thread.appeal_path = None", body)


if __name__ == "__main__":
    unittest.main()
