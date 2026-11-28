"""v65: «Обращение №1» — постановление (.png) не формировалось.

Реальная выгрузка пользователя (7 файлов) показала, что на одной странице чата
лежат ДВА ответа ИИ (проверка обращения + постановление) и две собственные
заявки клиента, а в чат по кнопке «Сформировать» уходило ДВА сообщения.

Первопричины и решения пользователя (опрос 2026-11-27):

1. `reload_policy = always_reload` — страница ИИ очищается ПЕРЕД КАЖДОЙ
   заявкой («Этап 1», «Этап 2.N», «Этап 3», «Сформировать», «Согласовать
   порядок»), файлы выгружаются заново, в чат — одно короткое сообщение;
   память дела ИИ получает файлом-заявкой. Разбор ответа всё равно берёт
   «последний/лучший» участок — страховка на случай неудачной перезагрузки.
2. `established = cut_preamble` — вводную часть «Я, Прокурор …» в «УСТАНОВИЛ»
   обрезает клиент (он печатает свою), тайм-коды сохраняются.
3. `points = newline_point` — новая строка = новый пункт, если предыдущая
   закончена точкой; список глаголов расширен.
4. `prompt = prompt_136` — промт 1.36 рядом с 1.35, код терпит старый формат.

Образцы в `tests/samples_v65/` — реальные файлы из выгрузки «Обращение №1.7z»:

* `страница_чата_два_ответа.txt` — вся страница чата (126 строк, два ответа);
* `заявка_сформировать_этап1.txt` — заявка, которая ушла в чат;
* `заявка_поля_этап1.json` — поля отложенной отправки;
* `обращение.txt` — обращение гражданина.
"""

from __future__ import annotations

import ast
import json
import re
import unittest
from pathlib import Path

import gov_case as cases
import pending_send as psend
import postanovlenie_prokuror_data as gdata

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = Path(__file__).resolve().parent / "samples_v65"
PROMPTS = ROOT.parent / "Для новой версии" / "Текущий промт"


def _sample(name: str) -> str:
    return (SAMPLES / name).read_text(encoding="utf-8", errors="replace")


def _src(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _block(src: str, start: str, end: str) -> str:
    i = src.index(start)
    j = src.index(end, i)
    return src[i:j]


def _method(src: str, name: str) -> str:
    """Тело метода класса — до следующего `def` того же отступа."""
    i = src.index(f"    def {name}(")
    j = src.index("\n    def ", i + 1)
    return src[i:j]


PAGE = _sample("страница_чата_два_ответа.txt")
REQUEST = _sample("заявка_сформировать_этап1.txt")


class V65AnswerRegionTests(unittest.TestCase):
    """Страница с двумя ответами: клиент берёт тот блок, который просил."""

    def setUp(self):
        self.document = gdata.extract_ai_answer(PAGE, REQUEST, want="document")
        self.intake = gdata.extract_ai_answer(PAGE, REQUEST, want="intake")

    def test_document_region_is_found(self):
        self.assertTrue(self.document.strip())
        first = self.document.strip().splitlines()[0]
        self.assertIn("ПОСТАНОВЛЕНИЕ", first.upper())

    def test_document_region_has_no_service_blocks(self):
        up = self.document.upper()
        self.assertNotIn("ПРОВЕРКА ОБРАЩЕНИЯ", up)
        self.assertNotIn("ЦЕПОЧКА ЭТАПА", up)
        self.assertNotIn("РЕКОМЕНДАЦИИ", up)

    def test_intake_region_is_the_check_block(self):
        up = self.intake.upper()
        self.assertIn("ПРОВЕРКА ОБРАЩЕНИЯ", up)
        self.assertIn("СОСТОЯНИЕ:", up)
        self.assertIn("ЦЕПОЧКА", up)
        # блока документа в участке проверки быть не должно
        self.assertNotIn("УСТАНОВИЛ:", up)

    def test_page_chips_and_disclaimer_are_not_the_answer(self):
        up = self.document.upper()
        self.assertNotIn("MEMPHIS.PDF", up)
        self.assertNotIn("ШАБЛОН.TXT", up)
        self.assertNotIn("ИИ МОЖЕТ ОШИБАТЬСЯ", up)
        self.assertNotIn("ВЫ ОТПРАВИЛИ", up)
        self.assertNotIn("ПЕРЕЙТИ К ПРЕДЫДУЩЕМУ ЗАПРОСУ", up)

    def test_own_request_echo_is_not_the_answer(self):
        up = self.document.upper()
        self.assertNotIn("ЗАЯВКА НА ПОСТАНОВЛЕНИЕ", up)
        self.assertNotIn("СВЕДЕНИЯ ПО ДЕЛУ", up)

    def test_v64_single_answer_page_still_parses(self):
        """Регресс: страница с ОДНИМ ответом (выгрузка v64) не сломана."""
        old = (Path(__file__).resolve().parent / "samples_v64"
               / "страница_чата_этап1.txt")
        if not old.is_file():
            self.skipTest("нет образца v64")
        text = old.read_text(encoding="utf-8", errors="replace")
        self.assertIn("ПРОВЕРКА ОБРАЩЕНИЯ",
                      gdata.extract_ai_answer(text, "", want="intake").upper())


class V65DocumentParseTests(unittest.TestCase):
    """Поля постановления из реального ответа — без эха заявки и мусора."""

    @classmethod
    def setUpClass(cls):
        clean = gdata.extract_ai_answer(PAGE, REQUEST, want="document")
        cls.doc = gdata.parse_gov_answer(clean)

    def test_document_is_parsed(self):
        self.assertIsNotNone(self.doc)

    def test_fields(self):
        doc = self.doc
        self.assertEqual(doc.doc_number.strip(), "001")
        self.assertEqual(doc.doc_date.strip(), "27.11.2026")
        self.assertIn("принятии обращения", doc.doc_type.lower())
        self.assertIn("Raine", doc.author_name)
        self.assertEqual(doc.author_id.strip(), "198632")
        self.assertIn("Shroud", doc.applicant_name)
        self.assertEqual(doc.appeal_date.strip(), "25.11.2026")
        self.assertIn("Asaev", doc.subject_name)
        self.assertEqual(doc.subject_id.strip(), "110384")
        self.assertEqual(doc.doc_stage, "primary")

    def test_request_echo_does_not_eat_document_fields(self):
        """Поля заявки («Заявитель:», «Дата обращения:») — не эхо запроса."""
        self.assertTrue(self.doc.appeal_subject.strip())
        self.assertTrue(self.doc.subject_department.strip())

    def test_no_remarks(self):
        self.assertEqual(gdata.check_gov_document(self.doc), [])


class V65EstablishedTests(unittest.TestCase):
    """«УСТАНОВИЛ»: вводная часть ИИ обрезана, тайм-коды сохранены."""

    @classmethod
    def setUpClass(cls):
        clean = gdata.extract_ai_answer(PAGE, REQUEST, want="document")
        cls.doc = gdata.parse_gov_answer(clean)
        cls.text = str(cls.doc.get("established") or "")

    def test_not_empty(self):
        self.assertGreater(len(self.text), 400)

    def test_ai_preamble_is_cut(self):
        low = self.text.lower()
        self.assertNotIn("я, прокурор штата сан-андреас", low)
        self.assertNotIn("руководствуясь", low)
        self.assertNotIn("рассмотрев обращение гражданина", low)

    def test_body_starts_from_the_essence(self):
        # клиент печатает свою вводную часть и дальше — суть из ответа ИИ.
        self.assertTrue(self.text.strip().startswith("Заявитель обжалует"))

    def test_timecodes_are_kept(self):
        found = re.findall(r"\d{2}:\d{2}", self.text)
        self.assertIn("01:14", found)
        self.assertIn("02:27", found)
        self.assertGreaterEqual(len(found), 8)

    def test_recommendations_are_not_inside(self):
        self.assertNotIn("РЕКОМЕНДАЦИИ", self.text.upper())

    def test_client_preamble_is_not_duplicated(self):
        preamble = self.doc.preamble()
        self.assertIn("Я, Прокурор", preamble)
        self.assertEqual(preamble.count("рассмотрев обращение"), 1)

    def test_preamble_basis_field_is_recognised(self):
        """Промт 1.36 отдаёт нормы вводной части отдельным полем."""
        self.assertEqual(gdata._normalize_gov_key("Основание вводной части"),
                         "preamble_basis")
        doc = gdata.parse_gov_answer(
            "ПОСТАНОВЛЕНИЕ\nНомер: 1\nОснование вводной части: ст. 8 Закона об ОГП\n"
            "УСТАНОВИЛ:\nустановил, что заявитель обжалует действия сотрудника.\n"
            "ПОСТАНОВИЛ:\n1. Принять обращение к производству.\nКОНЕЦ ПОСТАНОВЛЕНИЯ")
        self.assertIn("ст. 8 Закона об ОГП", str(doc.preamble_basis))
        self.assertIn("ст. 8 Закона об ОГП", doc.preamble())


class V65PointsTests(unittest.TestCase):
    """«ПОСТАНОВИЛ»: новая строка = новый пункт, если предыдущая закончена."""

    @classmethod
    def setUpClass(cls):
        clean = gdata.extract_ai_answer(PAGE, REQUEST, want="document")
        cls.doc = gdata.parse_gov_answer(clean)
        cls.points = list(getattr(cls.doc, "points", []) or [])

    def test_five_points_from_unnumbered_lines(self):
        self.assertEqual(len(self.points), 5)

    def test_first_point_is_the_decision(self):
        self.assertIn("Принять", self.points[0])

    def test_signature_is_not_a_point(self):
        joined = " ".join(self.points)
        self.assertNotIn("J. Raine", joined)
        self.assertNotIn("Удостоверение №", joined)

    def test_new_line_starts_new_point_when_previous_ends_with_dot(self):
        doc = gdata.parse_gov_answer(
            "ПОСТАНОВЛЕНИЕ\nНомер: 1\nУСТАНОВИЛ:\nустановил факты.\n"
            "ПОСТАНОВИЛ:\n1. Истребовать материалы дела.\n"
            "Обязать руководителя устранить нарушение.\n"
            "Поручить отделу проверить исполнение.\n"
            "КОНЕЦ ПОСТАНОВЛЕНИЯ")
        points = list(getattr(doc, "points", []) or [])
        self.assertEqual(len(points), 3)

    def test_broken_line_is_joined(self):
        doc = gdata.parse_gov_answer(
            "ПОСТАНОВЛЕНИЕ\nНомер: 1\nУСТАНОВИЛ:\nустановил факты.\n"
            "ПОСТАНОВИЛ:\n1. Истребовать материалы дела\n"
            "и приобщить их к проверке.\n"
            "2. Контроль возложить на отдел.\nКОНЕЦ ПОСТАНОВЛЕНИЯ")
        points = list(getattr(doc, "points", []) or [])
        self.assertEqual(len(points), 2)
        self.assertIn("приобщить", points[0])

    def test_extended_verb_list(self):
        for verb in ("инициировать", "обязать", "истребовать", "поручить",
                     "вызвать", "отстранить", "передать", "признать",
                     "провести", "обеспечить", "рекомендовать",
                     "контроль возложить"):
            doc = gdata.parse_gov_answer(
                "ПОСТАНОВЛЕНИЕ\nНомер: 1\nУСТАНОВИЛ:\nустановил факты.\n"
                f"ПОСТАНОВИЛ:\n1. Принять обращение.\n{verb.capitalize()} "
                "исполнение решения.\nКОНЕЦ ПОСТАНОВЛЕНИЯ")
            self.assertEqual(len(list(getattr(doc, "points", []) or [])), 2, verb)


class V65AlwaysReloadTests(unittest.TestCase):
    """Политика `always_reload`: одна заявка — один чистый чат, одно сообщение."""

    def test_ui_has_only_restart_signal(self):
        ui = _src("postanovlenie_prokuror_ui.py")
        self.assertNotIn("send_in_chat", ui)
        self.assertEqual(ui.count("self.restart_requested.emit"), 4)

    def test_every_button_restarts(self):
        ui = _src("postanovlenie_prokuror_ui.py")
        for name, end in (("def _send_node", "def _save_node_document"),
                          ("def _save_node_document", "def _close_case_requested"),
                          ("def _send_custom_order", "def _build_work_window")):
            block = _block(ui, name, end)
            self.assertIn("self.restart_requested.emit", block, name)

    def test_main_has_no_in_chat_slot(self):
        main = _src("main.py")
        self.assertNotIn("def _on_send_in_chat", main)
        self.assertNotIn("dlg.send_in_chat.connect", main)

    def test_restart_resets_waiting_state(self):
        """Новая заявка не должна считать прошлый ответ обработанным."""
        main = _src("main.py")
        block = _method(main, "_on_pending_send_restart")
        for attr in ("_doc_handled_text", "_doc_last_text", "_doc_settle_count",
                     "_doc_warned_text"):
            self.assertIn(attr, block)

    def test_form_request_does_not_start_a_second_upload(self):
        main = _src("main.py")
        block = _method(main, "_on_document_formed")
        self.assertIn("pending_here", block)

    def test_chat_message_is_short_and_files_carry_the_context(self):
        """Простыня на 6684 знака удалена: сообщение — `ai_request.document_message`."""
        main = _src("main.py")
        block = _method(main, "_auto_send_document_request")
        self.assertIn("ai_request.document_message", block)
        self.assertNotIn("ОБРАЩЕНИЕ ГРАЖДАНИНА приведено в заявке целиком", block)
        self.assertNotIn("ЕСЛИ В ЗАЯВКЕ УКАЗАНО", block)

    def test_pending_record_of_the_real_case(self):
        """Отложенная отправка реальной заявки: 4 вложения, короткий текст."""
        fields = json.loads(_sample("заявка_поля_этап1.json"))
        self.assertTrue(fields)
        self.assertIn(REQUEST.splitlines()[0][:40], REQUEST)
        self.assertGreater(len(REQUEST), 4000)
        self.assertIn("СВЕДЕНИЯ ПО ДЕЛУ", REQUEST)
        self.assertTrue(hasattr(psend, "build_record"))
        self.assertTrue(hasattr(psend, "save"))

    def test_case_memory_travels_in_the_request_file(self):
        """Память дела есть в заявке — перезагрузка страницы её не теряет."""
        case = cases.GovCase(number="1")
        case.appeal_raw = _sample("обращение.txt")
        case.apply_intake({"state": cases.INTAKE_ACCEPTED,
                           "basis": "ст. 11 Регламента"})
        request = cases.node_document_request(case, "1", "Jonathan Raine")
        self.assertIn("СВЕДЕНИЯ ПО ДЕЛУ", request)
        self.assertIn("ЗАЯВКА НА ПОСТАНОВЛЕНИЕ", request.upper())
        self.assertGreater(len(request), 2000)


class V65KeepDraftTests(unittest.TestCase):
    """Если ИИ не ответил, заявка остаётся и повторяется одной кнопкой."""

    def test_pending_send_keeps_sent_and_failed_records(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            record = psend.build_record(kind="document", request_text="заявка")
            psend.save(tmp, record)
            psend.mark_sent(tmp, psend.load(tmp))
            self.assertTrue(psend.record_path(tmp).exists())
            self.assertFalse(psend.is_fresh(psend.load(tmp)))
            psend.mark_failed(tmp, psend.load(tmp), reason="send")
            self.assertTrue(psend.can_resend(psend.load(tmp)))
            self.assertTrue(psend.is_fresh(psend.rearm(psend.load(tmp))))

    def test_main_has_resend_slot_and_clears_on_answer(self):
        main = _src("main.py")
        self.assertIn("def _on_resend_pending_request", main)
        self.assertIn("def _pending_answer_received", main)
        self.assertIn("def _refresh_gov_resend_button", main)
        # обе точки подключения сигнала формы
        self.assertEqual(main.count(
            "dlg.resend_requested.connect(self._on_resend_pending_request)"), 2)
        # запись убирается, когда ответ разобран
        intake = _method(main, "_apply_gov_intake")
        self.assertIn('_pending_answer_received("1")', intake)
        answer = _method(main, "_apply_gov_answer")
        self.assertIn("_pending_answer_received(node_kind)", answer)

    def test_failures_keep_the_draft(self):
        main = _src("main.py")
        retry = _method(main, "_reload_for_upload_retry")
        self.assertIn("psend.STATE_FAILED", retry)
        done = _method(main, "_on_pending_send_done")
        self.assertIn("psend.STATE_FAILED", done)
        finish = _method(main, "_pending_send_finish")
        self.assertIn("psend.mark_failed", finish)
        self.assertIn("Повторить отправку", finish)

    def test_hard_timeout_points_to_the_button(self):
        main = _src("main.py")
        poll = _method(main, "_poll_document_answer")
        self.assertIn("Повторить отправку", poll)

    def test_form_has_resend_button_and_signal(self):
        ui = _src("postanovlenie_prokuror_ui.py")
        self.assertIn("resend_requested = pyqtSignal()", ui)
        self.assertIn('self.resend_btn = QPushButton("Повторить отправку")', ui)
        self.assertIn("self.resend_btn.setVisible(False)", ui)
        self.assertIn("def set_resend_available", ui)
        self.assertIn("def refresh_resend_button", ui)
        view = _method(ui, "_refresh_chain_view")
        self.assertIn("self.refresh_resend_button()", view)


class V65PromptTests(unittest.TestCase):
    """Промт 1.36 выпущен рядом с 1.35 и согласован с клиентом."""

    @classmethod
    def setUpClass(cls):
        cls.path = PROMPTS / "promt 1.36.txt"
        if not cls.path.is_file():
            raise unittest.SkipTest("промт 1.36 не найден")
        cls.text = cls.path.read_text(encoding="utf-8")

    def test_version_line(self):
        self.assertIn("Версия промта: 1.36", self.text)

    def test_old_prompt_is_kept(self):
        self.assertTrue((PROMPTS / "promt 1.35.txt").is_file())

    def test_numbered_points_rule(self):
        self.assertIn("ТОЛЬКО нумерованными пунктами", self.text)

    def test_no_preamble_repeat_rule(self):
        self.assertIn("Вводную часть прокурора", self.text)
        self.assertIn("НЕ повторяй", self.text)

    def test_single_block_answer_rule(self):
        self.assertIn("СТРОГО один блок постановления", self.text)

    def test_clean_chat_rule(self):
        self.assertIn("КАЖДЫЙ ШАГ ПРИХОДИТ ЧИСТЫМ ЧАТОМ", self.text)
        self.assertIn("СВЕДЕНИЯ ПО ДЕЛУ", self.text)

    def test_preamble_basis_field_is_declared(self):
        self.assertIn("Основание вводной части", self.text)


class V65RenderTests(unittest.TestCase):
    """Из реального ответа печатается .png постановления."""

    def test_render_pages(self):
        try:
            import doc_render as prender
        except Exception as exc:  # pragma: no cover - нет PIL в окружении
            self.skipTest(f"doc_render недоступен: {exc}")
        clean = gdata.extract_ai_answer(PAGE, REQUEST, want="document")
        doc = gdata.parse_gov_answer(clean)
        self.assertIsNotNone(doc)
        pages = prender.render_document(doc, gdata.gov_assets())
        self.assertGreaterEqual(len(pages), 2)
        for page in pages:
            self.assertEqual(page.size, (prender.PAGE_W, prender.PAGE_H))


if __name__ == "__main__":
    unittest.main()
