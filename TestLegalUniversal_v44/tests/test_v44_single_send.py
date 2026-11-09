"""v44: одна заявка — одно сообщение в чате.

Разбор сессии `20261109-145359-16532-54c702` (папка `Постановление_44`):

    11:54:30.145  pending_send.sent        отправлена заявка (отложенная)
    11:54:30.150  attachments_confirmed    complete=false  ← сброс состояния
    11:54:30.498  upload.attempt pdf       файлы выгружаются ЗАНОВО
    11:54:31.869  attachments_confirmed    complete=true
    11:54:31.870  documents.auto_send      ← ВТОРАЯ отправка того же текста
    11:54:32.580  documents.auto_sent

В чат ушло два сообщения подряд; ИИ не успел обработать первое и начал анализ
второго. Пользователь: «экспортировало 2 сообщения, при чём первое даже не
успело обработаться ИИ».
"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import chat_ready as cr  # noqa: E402
import gov_case as cases  # noqa: E402
import postanovlenie_data as pdoc  # noqa: E402
import postanovlenie_prokuror_data as pgov  # noqa: E402


class SingleSendTests(unittest.TestCase):
    """Отправка помечается ДО действия, второй путь её видит и молчит."""

    def setUp(self):
        self.src = (ROOT / "main.py").read_text(encoding="utf-8")

    def test_pending_send_marks_request_before_sending(self):
        block = self.src[self.src.index("def _try_send_pending"):]
        block = block[:block.index("def _on_pending_send_done")]
        mark = block.index("self._request_sent_key = self._request_key()")
        busy = block.index("self._pending_send_busy = True")
        thread = block.index('name="PendingSend"')
        self.assertLess(mark, busy)
        self.assertLess(mark, thread)

    def test_auto_send_skips_duplicate(self):
        block = self.src[self.src.index("def _auto_send_document_request"):]
        block = block[:block.index("def _watch_document_answer")]
        self.assertIn("_request_sent_key", block)
        self.assertIn("documents.skip_duplicate_send", block)
        # проверка стоит до реальной вставки текста
        self.assertLess(block.index("skip_duplicate_send"),
                        block.index("Input.insertText"))

    def test_request_key_identifies_one_request(self):
        block = self.src[self.src.index("def _request_key"):]
        block = block[:block.index("def _pending_send_stop")]
        self.assertIn("Path(path).name", block)
        self.assertIn("stat().st_size", block)

    def test_key_resets_for_each_document(self):
        block = self.src[self.src.index("def _on_document_formed"):]
        block = block[:block.index("self._document_doc = document")]
        self.assertIn('self._request_sent_key = ""', block)

    def test_no_reupload_when_attachments_already_present(self):
        block = self.src[self.src.index("def _resume_pending_send"):]
        block = block[:block.index("def _try_send_pending")]
        self.assertIn("pending_send.files_already_attached", block)
        self.assertIn("_fresh_chat_requested", block)

    def test_dedup_does_not_reset_confirmation_before_send(self):
        block = self.src[self.src.index("class UploadThread(QThread):"):]
        block = block[:block.index("\nclass ", 1)]
        self.assertIn("_keep_attachments", block)


class OwnAnswerTests(unittest.TestCase):
    """Ответ ИИ принимается, только если он НОВЫЙ и это не наша заявка."""

    def setUp(self):
        self.src = (ROOT / "main.py").read_text(encoding="utf-8")

    def test_watch_remembers_chat_length(self):
        block = self.src[self.src.index("def _watch_document_answer"):]
        block = block[:block.index("def _chat_text_length")]
        self.assertIn("_doc_watch_base_len", block)
        self.assertIn("_chat_text_length()", block)

    def test_poll_requires_new_text(self):
        block = self.src[self.src.index("def _poll_document_answer"):]
        block = block[:block.index("def _documents_root")]
        self.assertIn("JS_CHAT_TEXT_LENGTH", block)
        self.assertIn("answer_not_new", block)

    def test_own_request_is_filtered_first(self):
        block = self.src[self.src.index("def _apply_document_answer"):]
        block = block[:block.index("def _on_gov_intake_requested")]
        self.assertIn("is_own_request", block)
        self.assertLess(block.index("is_own_request"), block.index("_apply_gov_intake"))

    def test_chat_text_js_exists(self):
        self.assertIn("JS_CHAT_TEXT_LENGTH", self.src)
        js = self.src[self.src.index("JS_CHAT_TEXT_LENGTH"):]
        js = js[:js.index('"""', js.index('"""') + 3)]
        self.assertIn("message-content", js)
        self.assertIn("innerText", js)


class IntakeParsingTests(unittest.TestCase):
    """Ответ из отчёта пользователя разбирается как «Отказано»."""

    ANSWER = (
        "===ПРОВЕРКА ОБРАЩЕНИЯ===\n"
        "Состояние: Отказано\n"
        "Основание: Пункт 14 части 1 статьи 16 Регламента подачи обращений\n"
        "Вид решения: Постановление об отказе в принятии обращения к производству\n"
        "Что проверить: отсутствие доказательств\n"
        "===КОНЕЦ ПРОВЕРКИ===\n"
        "===КОНЕЦ ОТВЕТА==="
    )

    def test_answer_is_parsed(self):
        self.assertTrue(pgov.has_intake_block(self.ANSWER))
        result = cases.parse_intake_answer(self.ANSWER)
        self.assertTrue(result["ok"])
        self.assertEqual(result["state"], cases.INTAKE_REFUSED)
        self.assertEqual(result["status"], cases.STATUS_REFUSED)
        self.assertIn("16", result["basis"])

    def test_refused_case_parses_to_refused_status(self):
        case = cases.GovCase(number="1")
        case.apply_intake(cases.parse_intake_answer(self.ANSWER))
        self.assertTrue(case.refused())
        self.assertFalse(case.accepted())

    def test_own_request_is_not_an_answer(self):
        request = "ЗАЯВКА НА ПРОВЕРКУ ОБРАЩЕНИЯ (этап 1)\nПрокурор: Jonathan Raine"
        self.assertTrue(pdoc.is_own_request(request, request))
        self.assertFalse(pdoc.is_own_request(self.ANSWER, request))

    def test_request_echo_is_not_taken_for_an_answer(self):
        """Наша заявка не должна приниматься за ответ ИИ (проверка v44)."""
        case = cases.GovCase(number="1")
        case.appeal_raw = "Обращение гражданина Sae Gucci"
        request = cases.intake_request(case, "Jonathan Raine")
        self.assertTrue(pdoc.is_own_request(request, request))
        # ответ ИИ содержит служебный блок, заявка — нет
        self.assertTrue(pgov.has_intake_block(self.ANSWER))
        self.assertFalse(pgov.has_intake_block(request))


class SingleSendIntegrationTests(unittest.TestCase):
    """Ключ заявки: повторная выгрузка того же файла — тот же ключ."""

    def _key(self, path, text):
        try:
            size = path.stat().st_size
        except Exception:
            size = 0
        return f"{path.name}|{size}|{text[:160]}"

    def test_same_file_gives_same_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Проверка обращения (заявка).txt"
            path.write_text("заявка", encoding="utf-8")
            self.assertEqual(self._key(path, "заявка"), self._key(path, "заявка"))

    def test_different_request_gives_different_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "a.txt"
            second = Path(tmp) / "b.txt"
            first.write_text("одна заявка", encoding="utf-8")
            second.write_text("другая заявка", encoding="utf-8")
            self.assertNotEqual(self._key(first, "одна заявка"),
                                self._key(second, "другая заявка"))


class GateStillWorksTests(unittest.TestCase):
    """Правки v44 не сломали готовность вложений из v43.4/v43.5."""

    REQUIRED = ["Memphis.pdf", "Шаблон.txt", "Проверка обращения (заявка).txt"]

    def test_ready_after_settle(self):
        state = {"present": 3, "uploading": False, "names": self.REQUIRED}
        result = cr.check(state, self.REQUIRED, confirmed_at=100.0, now=200.0)
        self.assertTrue(result["ready"])

    def test_duplicate_chips_still_collapsed(self):
        state = {"present": 6, "uploading": False,
                 "names": self.REQUIRED * 2}
        self.assertEqual(cr.normalize(state)["present"], 3)


if __name__ == "__main__":
    unittest.main()


class RequestIsNotAnAnswerTests(unittest.TestCase):
    """v44: своя заявка не может стать результатом проверки.

    В деле 1 из отчёта пользователя оказался весь текст страницы:
    «Перейти к основному контенту … Чат в режиме ИИ: ЗАЯВКА НА ПРОВЕРКУ …»,
    статус «Отказано», основание пустое.
    """

    def _request(self):
        case = cases.GovCase(number="1")
        case.appeal_raw = "Обращение гражданина Sae Gucci, ID 12361"
        return cases.intake_request(case, "Jonathan Raine")

    def test_request_has_no_answer_markers(self):
        request = self._request()
        self.assertFalse(pgov.has_intake_block(request),
                         "в заявке не должно быть маркеров ответа")

    def test_request_is_not_parsed_as_a_decision(self):
        result = cases.parse_intake_answer(self._request())
        self.assertFalse(result["ok"], "заявка не должна давать состояние")
        self.assertEqual(result["state"], "")

    def test_request_keeps_the_appeal_text(self):
        request = self._request()
        self.assertIn("ОБРАЩЕНИЕ ГРАЖДАНИНА", request)
        self.assertIn("Sae Gucci, ID 12361", request)

    def test_menu_line_is_not_a_decision(self):
        self.assertTrue(pgov._is_state_menu(
            "В служебной строке укажи одно: Принято, Отказано, Переадресовано"))
        self.assertFalse(pgov._is_state_menu("Состояние: Отказано"))

    def test_real_answers_still_parsed(self):
        for text, state in [
            ("Состояние: Отказано\nОснование: п. 14 ч. 1 ст. 16", cases.INTAKE_REFUSED),
            ("Состояние: Принято\nОснование: форма соблюдена", cases.INTAKE_ACCEPTED),
            ("Состояние: Переадресовано\nОснование: ст. 18", cases.INTAKE_REDIRECTED),
        ]:
            result = cases.parse_intake_answer(text)
            self.assertTrue(result["ok"], text)
            self.assertEqual(result["state"], state)

    def test_appeal_text_goes_to_case_not_the_request(self):
        """Дело ищется по тексту обращения, а не по тексту заявки."""
        src = (ROOT / "main.py").read_text(encoding="utf-8")
        block = src[src.index("def _resume_pending_send"):]
        block = block[:block.index("def _try_send_pending")]
        self.assertIn("_gov_intake_appeal", block)
        self.assertIn("appeal_text", block)


class ReadAnswerJsTests(unittest.TestCase):
    """JS читает ответ ИИ, но никогда — заявку пользователя (v44)."""

    def setUp(self):
        src = (ROOT / "main.py").read_text(encoding="utf-8")
        js = src[src.index("JS_READ_ANSWER = r"):]
        js = js[:js.index('"""', js.index('"""') + 3)]
        self.js = js

    def test_body_is_not_used_as_fallback(self):
        code = "\n".join(line for line in self.js.splitlines()
                         if not line.strip().startswith("//"))
        self.assertNotIn("document.body", code)
        self.assertIn("return ''", self.js)

    def test_user_selectors_are_known(self):
        for selector in ("user-query", "query-text", "user-message"):
            self.assertIn(selector, self.js)

    def test_reply_equal_to_query_is_rejected(self):
        self.assertIn("reply === said", self.js)
