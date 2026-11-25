"""v48: после «Проверки ИИ» в чате остаются только .pdf и «Шаблон.txt».

Замечание пользователя: «после исполнения функции „Проверка ИИ" и после
получения ответа и после перезагрузки у меня помимо .pdf файла и Шаблон.txt
ещё привязан файл „Проверка обращения (заявка).txt"».

Причина: путь к заявке (`document_path`) выставлялся перед отправкой и после неё
НЕ очищался. Окно ИИ после ответа перезагружается (v46), а новый поток
вложений снова видел `document_path` и вкладывал заявку — в чате оказывались
три файла вместо двух.
"""

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MAIN = (ROOT / "main.py").read_text(encoding="utf-8")


def func(name: str) -> str:
    tree = ast.parse(MAIN)
    node = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == name)
    return ast.get_source_segment(MAIN, node) or ""


class AttachmentDroppedAfterSendTests(unittest.TestCase):
    """Заявка перестаёт быть вложением сразу после отправки."""

    def test_helper_exists_and_clears_paths(self):
        body = func("_drop_document_attachment")
        self.assertTrue(body)
        self.assertIn("self.document_path = None", body)
        self.assertIn("self._doc_sent_path = None", body)
        # Потоку вложений тоже сообщаем: иначе он ждёт третий файл.
        self.assertIn("thread.document_path = None", body)

    def test_pending_send_clears_after_success(self):
        body = func("_pending_send_finish")
        self.assertIn('self._drop_document_attachment("sent")', body)
        # Очистка стоит в ветке УСПЕШНОЙ отправки: в ветке «не отправлена»
        # (psend.clear) её нет, иначе устаревшая заявка тоже «забывалась» бы.
        sent = body.index('self._drop_document_attachment("sent")')
        self.assertLess(body.index("psend.mark_sent"), sent)
        self.assertLess(sent, body.index("psend.clear("))

    def test_auto_send_clears_attachment(self):
        body = func("_auto_send_document_request")
        self.assertIn('self._drop_document_attachment("auto_send")', body)
        # Очистка — после запуска отправки, но до ожидания ответа.
        self.assertLess(body.index('name="DocAutoSend"'),
                        body.index("_drop_document_attachment"))
        self.assertLess(body.index("_drop_document_attachment"),
                        body.index("self._watch_document_answer()"))

    def test_request_text_is_kept(self):
        # Текст заявки нужен, чтобы отличить свой запрос от ответа ИИ —
        # очищаем только ПУТЬ к файлу-вложению.
        body = func("_drop_document_attachment")
        self.assertNotIn("_doc_request_text", body)


class UploadThreadNeedsTwoFilesTests(unittest.TestCase):
    """Без заявки поток вложений требует ровно промт и «Шаблон.txt»."""

    def test_required_count_follows_document_path(self):
        body = func("_required_count")
        self.assertIn("REQUIRED_CHAT_FILES", body)
        self.assertIn("self.document_path", body)

    def test_reload_starts_from_two_files(self):
        # v46 перезагружает окно ИИ штатным путём; v48 гарантирует, что к
        # этому моменту заявки во вложениях уже нет.
        reload_body = func("_reload_chat_after_intake")
        self.assertIn("_recover_web", reload_body)
        finish = func("_pending_send_finish")
        self.assertLess(finish.index("mark_sent"),
                        finish.index("_drop_document_attachment"))


if __name__ == "__main__":
    unittest.main()
