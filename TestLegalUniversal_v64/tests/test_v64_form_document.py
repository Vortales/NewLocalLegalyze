"""test_v64_form_document.py — «Сформировать» отправляет документ в ИИ (v64).

Замечания пользователя по выгрузке `этап-1 — принятие.7z`:

1. «Постановление Обращение №X» → «Обстоятельства» → поле «Ответ ИИ (только
   чтение)» — «каша», всё вперемешку;
2. «по нажатию „Сформировать“ ДЛЯ ВСЕХ ЭТАПОВ данные отправляются в ИИ и по
   ответу ты уже формируешь .png постановление»;
3. «находясь на „Этап 2.2 — Об истребовании“ при нажатии „Сформировать“ в чат
   с ИИ должна попасть информация из полей „Что стало известно“ + „Ответ ИИ“
   от Этап 1, Этап 2.1, Этап 2.2 (если есть)», «желательно всю информацию в
   .txt файле, прямой текст по минимуму»;
4. промт привести в соответствие с клиентом (решение: «промт под клиент»).

Образцы в `tests/samples_v64/` — реальные файлы из выгрузки:

* `страница_чата_этап1.txt` — то, что клиент писал в поле 2 (вся страница чата);
* `брак_установил_этап1.txt` — текст PNG, который из этого получился;
* `брак_поля_этап1.json` — поля того документа (`points: []`, `doc_stage:
  final` у этапа 1, в «УСТАНОВИЛ» — блок рекомендаций).
"""

from __future__ import annotations

import ast
import json
import re
import unittest
from pathlib import Path

import gov_case as gc
import ogp_stages
import pending_send as psend
import postanovlenie_prokuror_data as gdata

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = Path(__file__).resolve().parent / "samples_v64"
PROMPTS = ROOT.parent / "Для новой версии" / "Текущий промт"


def _sample(name: str) -> str:
    return (SAMPLES / name).read_text(encoding="utf-8", errors="replace")


def _src(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _block(src: str, start: str, end: str) -> str:
    body = src[src.index(start):]
    return body[:body.index(end)]


def _current_prompt() -> Path | None:
    """Самый свежий промт в «Для новой версии/Текущий промт»."""
    if not PROMPTS.is_dir():
        return None
    found = []
    for path in PROMPTS.glob("promt 1.*.txt"):
        match = re.search(r"promt 1\.(\d+)\.txt$", path.name)
        if match:
            found.append((int(match.group(1)), path))
    return max(found)[1] if found else None


def _accepted_case(answer: str) -> gc.GovCase:
    """Дело с принятым обращением и цепочкой из ответа этапа 1."""
    case = gc.GovCase(number="1")
    case.appeal_raw = "ОБРАЩЕНИЕ. От гражданина Lorenzo Shroud 34074."
    result = gc.parse_intake_answer(answer)
    case.apply_intake(result)
    case.set_outcome("accept")
    case.apply_chains(ogp_stages.split_chains(answer))
    case.work_letter = "A"
    case.set_work_node("1", input=case.appeal_raw, answer=answer, done=True)
    return case


# --------------------------------------------------------------------------- #
#  П.1 — «каша» в поле 2: в деле хранится ответ ИИ, а не страница чата
# --------------------------------------------------------------------------- #

class V64ChatScrapeCleanTests(unittest.TestCase):
    """`extract_ai_answer` оставляет только ответ ИИ."""

    def setUp(self):
        self.raw = _sample("страница_чата_этап1.txt")
        self.clean = gdata.extract_ai_answer(self.raw)

    def test_page_chrome_is_gone(self):
        for junk in ("Memphis.pdf", "Шаблон.txt", "Обращение.txt",
                     "Перейти к предыдущему запросу",
                     "Вы отправили 4 файлов", "Ответ в режиме ИИ"):
            self.assertNotIn(junk, self.clean, junk)

    def test_own_request_echo_is_gone(self):
        self.assertNotIn("Проверь обращение. Текст обращения", self.clean)
        self.assertNotIn("порядок проверки и формат ответа", self.clean)

    def test_answer_blocks_survive(self):
        for marker in ("ПРОВЕРКА ОБРАЩЕНИЯ", "Состояние: Принято",
                       "КОНЕЦ ПРОВЕРКИ", "ЦЕПОЧКИ ЭТАПА 2", "ЦЕПОЧКА A:",
                       "КОНЕЦ ЦЕПОЧЕК", "РЕКОМЕНДАЦИИ", "КОНЕЦ РЕКОМЕНДАЦИЙ"):
            self.assertIn(marker, self.clean, marker)

    def test_chip_word_is_not_glued_to_the_next_line(self):
        """«File РЕКОМЕНДАЦИИ» — склейка чипа и заголовка блока (v64)."""
        self.assertNotIn("File РЕКОМЕНДАЦИИ", self.clean)

    def test_answer_is_shorter_than_the_page(self):
        self.assertLess(len(self.clean), len(self.raw))

    def test_intake_result_still_parses_from_the_page(self):
        """Разбор служебных строк терпим к мусору страницы."""
        result = gc.parse_intake_answer(self.raw)
        self.assertTrue(result["ok"])
        self.assertEqual(result["state"], gdata.INTAKE_ACCEPTED)
        self.assertNotIn("Memphis.pdf", result["raw"])
        self.assertIn("ПРОВЕРКА ОБРАЩЕНИЯ", result["raw"])

    def test_request_lines_are_dropped_when_request_is_known(self):
        request = ("Проверь обращение. Текст обращения — во вложении "
                   "«Обращение.txt», порядок проверки и формат ответа — в "
                   "файле заявки.")
        clean = gdata.extract_ai_answer(self.raw, request)
        self.assertNotIn("во вложении «Обращение.txt»", clean)

    def test_main_stores_the_clean_answer(self):
        main = _src("main.py")
        intake = _block(main, "def _apply_gov_intake", "def _gov_intake_watch_stop")
        self.assertIn("pgov.extract_ai_answer", intake)
        answer = _block(main, "def _apply_gov_answer", "def _open_prompts_folder")
        self.assertIn("pgov.extract_ai_answer", answer)
        self.assertIn('answer=answer_text or str(saved_text or "")', answer)


# --------------------------------------------------------------------------- #
#  П.1 (продолжение) — блок «РЕКОМЕНДАЦИИ» не печатается в постановлении
# --------------------------------------------------------------------------- #

class V64RecommendationsTests(unittest.TestCase):
    """Рекомендации ИИ — подсказка прокурору, а не текст документа."""

    def setUp(self):
        self.raw = _sample("страница_чата_этап1.txt")
        self.clean = gdata.extract_ai_answer(self.raw)

    def test_block_is_cut_from_the_document(self):
        stripped = gdata.strip_service_blocks(self.clean)
        self.assertNotIn("РЕКОМЕНДАЦИИ", stripped)
        self.assertNotIn("Итоговый прогноз", stripped)
        self.assertEqual(gdata.clean_established(self.clean), "")

    def test_block_is_split_into_items(self):
        rows = ogp_stages.split_recommendations(self.clean)
        self.assertEqual(len(rows), 3)
        self.assertIn("реестру Коллегии адвокатов", rows[0]["text"])
        self.assertIn("48 часов", rows[1]["text"])
        self.assertIn("Итоговый прогноз", rows[2]["text"])

    def test_case_stores_recommendations_on_intake(self):
        case = _accepted_case(self.clean)
        self.assertEqual(len(case.recommendations), 3)
        self.assertEqual(case.intake_doc_type,
                         "О принятии обращения к собственному производству")
        # переживают сохранение/чтение дела
        again = gc.GovCase.from_dict(json.loads(json.dumps(case.to_dict())))
        self.assertEqual(len(again.recommendations), 3)

    def test_recommendations_do_not_go_back_to_ai(self):
        case = _accepted_case(self.clean)
        context = ogp_stages.answer_for_context(case, "1")
        self.assertIn("Состояние: Принято", context)
        self.assertNotIn("РЕКОМЕНДАЦИИ", context)
        self.assertNotIn("ЦЕПОЧКИ ЭТАПА 2", context)

    def test_form_shows_recommendations_as_a_hint(self):
        ui = _src("postanovlenie_prokuror_ui.py")
        self.assertIn("self.stage1_reco", ui)
        self.assertIn("def _recommendations_text", ui)
        block = _block(ui, "def _recommendations_text", "def _chain_hint_text")
        self.assertIn('"recommendations"', block)
        self.assertIn("Рекомендации ИИ прокурору", block)
        refresh = _block(ui, "def _refresh_chain_view", "def _recommendations_text")
        self.assertIn("self.stage1_reco.setText(self._recommendations_text())",
                      refresh)

    def test_archived_png_text_is_no_longer_produced(self):
        """Брак из выгрузки: «УСТАНОВИЛ» = рекомендации + чип «File»."""
        broken = _sample("брак_установил_этап1.txt")
        self.assertIn("File РЕКОМЕНДАЦИИ", broken)      # так было
        case = _accepted_case(self.clean)
        doc = gdata.parse_gov_answer(self.clean, base=gdata.PostanovlenieProkurora())
        # этап 1 — служебный ответ, документа в нём нет: печатать нечего,
        # документ запрашивается отдельно кнопкой «Сформировать».
        self.assertIsNone(doc)
        facts = case.intake_basis
        self.assertNotIn("РЕКОМЕНДАЦИИ", facts)


# --------------------------------------------------------------------------- #
#  П.2 — «Сформировать» отправляет заявку на ДОКУМЕНТ в чат ИИ
# --------------------------------------------------------------------------- #

class V64FormDocumentRequestTests(unittest.TestCase):
    """Содержимое заявки кнопки «Сформировать»."""

    def setUp(self):
        self.answer = gdata.extract_ai_answer(_sample("страница_чата_этап1.txt"))
        self.case = _accepted_case(self.answer)

    def test_stage1_names_the_document_of_stage_one(self):
        text = gc.node_document_request(self.case, "1", "Jonathan Raine")
        self.assertTrue(text.startswith(
            "ЗАЯВКА НА ПОСТАНОВЛЕНИЕ ПО ОБРАЩЕНИЮ (этап 1) — ДОКУМЕНТ"))
        self.assertIn("О принятии обращения к собственному производству", text)
        self.assertIn("Санкцию не назначай", text)
        self.assertIn("Ответ ИИ по этапу 1:", text)
        self.assertNotIn("РЕКОМЕНДАЦИИ", text)
        self.assertNotIn("ЦЕПОЧКА B:", text)

    def test_step_request_carries_memory_and_prosecutor_input(self):
        case = self.case
        case.set_work_node("2.1", answer="ПОСТАНОВИЛ: истребовать сведения",
                           done=True, input="Запрошены данные ГИБДД")
        case.set_work_node("2.2", input="Видеофиксация получена, тайм-код 00:04")
        text = gc.node_document_request(case, "2.2", "Jonathan Raine")
        self.assertIn("(этап 2.2) — ДОКУМЕНТ", text)
        # память дела: этап 1 и пройденный 2.1
        self.assertIn("ЭТАП 1 — решение:", text)
        self.assertIn("ПОДЭТАП 2.1", text)
        self.assertIn("ПОСТАНОВИЛ: истребовать сведения", text)
        # поле 1 текущего узла
        self.assertIn("ЧТО СТАЛО ИЗВЕСТНО (ввод прокурора, учти обязательно):",
                      text)
        self.assertIn("Видеофиксация получена, тайм-код 00:04", text)
        # что уже вынесено и что осталось
        self.assertIn("УЖЕ ВЫНЕСЕНО ПО ДЕЛУ:", text)
        self.assertIn("ОСТАЛОСЬ ПО ПЛАНУ:", text)

    def test_step_request_works_without_its_own_answer(self):
        """«Ответ ИИ» текущего узла идёт в заявку, только если он есть."""
        text = gc.node_document_request(self.case, "2.2", "Jonathan Raine")
        self.assertIn("ЗАДАЧА: вынеси постановление по подэтапу 2.2", text)
        self.assertNotIn("ПОДЭТАП 2.2", text)

    def test_stage3_request_is_the_final_decision(self):
        text = gc.node_document_request(self.case, "3", "Jonathan Raine")
        self.assertIn("(этап 3) — ДОКУМЕНТ", text)
        self.assertIn("ИТОГОВОЕ решение по обращению", text)

    def test_request_has_no_answer_markers(self):
        """Клиент не должен принимать свою заявку за ответ ИИ (урок v44)."""
        for kind in ("1", "2.1", "3"):
            text = gc.node_document_request(self.case, kind, "Jonathan Raine")
            self.assertNotIn("===ПОСТАНОВЛЕНИЕ===", text)
            self.assertNotIn("===КОНЕЦ ОТВЕТА===", text)
            self.assertNotIn("===ПРОВЕРКА ОБРАЩЕНИЯ===", text)
            self.assertFalse(gdata.parse_gov_answer(text))

    def test_document_message_is_short(self):
        """«прямой текст по минимуму»: в чат — короткая реплика и файлы."""
        import ai_request
        message = ai_request.document_message("Заявка (Сформировать).txt",
                                              "Обращение.txt")
        self.assertLess(len(message), 400)
        self.assertIn("Заявка (Сформировать).txt", message)


class V64FormButtonSendsTests(unittest.TestCase):
    """Кнопка «Сформировать» в форме: отправка, а не локальная печать."""

    def setUp(self):
        self.ui = _src("postanovlenie_prokuror_ui.py")
        self.block = _block(self.ui, "def _save_node_document",
                            "def _close_case_requested")

    def test_request_is_built_and_sent(self):
        self.assertIn("cases.node_document_request", self.block)
        self.assertIn("pending_send.build_record", self.block)
        self.assertIn("pending_send.save", self.block)
        self.assertIn("self.send_in_chat.emit", self.block)

    def test_no_local_print_and_no_browser_reload(self):
        self.assertNotIn("print_node_document", self.block)
        self.assertNotIn("restart_requested", self.block)
        self.assertNotIn("print_node_document", self.ui)

    def test_payload_goes_as_files(self):
        self.assertIn('folder / "Заявка (Сформировать).txt"', self.block)
        self.assertIn("ai_request.write_appeal_file", self.block)
        self.assertIn("ai_request.document_message", self.block)
        self.assertIn('folder / "Заявка-поля.json"', self.block)

    def test_gate_is_node_availability_not_an_existing_answer(self):
        self.assertIn('state.get("can_run")', self.block)

    def test_document_is_collected_for_the_node_being_printed(self):
        self.assertIn("self._collect(kind)", self.block)
        collect = _block(self.ui, "def _collect(self", "def set_document")
        self.assertIn("cases.stage_kind_for_node(kind)", collect)
        self.assertIn("doc.case_stage_kind = stage_kind", collect)

    def test_record_carries_node_and_stage_separately(self):
        self.assertIn("node_kind=kind", self.block)
        self.assertIn("stage_kind=stage_kind", self.block)
        record = psend.build_record(kind="document", request_text="т",
                                    stage_kind="measure", node_kind="2.1")
        self.assertEqual(record["stage_kind"], "measure")
        self.assertEqual(record["node_kind"], "2.1")

    def test_main_reads_the_node_from_its_own_field(self):
        main = _src("main.py")
        self.assertIn('record.get("node_kind")', main)
        self.assertNotIn("_print_gov_node", main)

    def test_all_three_save_buttons_send(self):
        tree = ast.parse(self.ui)
        calls = [node for node in ast.walk(tree)
                 if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute)
                 and node.func.attr == "connect"]
        targets = []
        for call in calls:
            for arg in call.args:
                if isinstance(arg, ast.Lambda) and isinstance(arg.body, ast.Call):
                    func = arg.body.func
                    if isinstance(func, ast.Attribute):
                        targets.append((func.attr,
                                        [a.value for a in arg.body.args
                                         if isinstance(a, ast.Constant)]))
        saves = [args for name, args in targets if name == "_save_node_document"]
        self.assertIn(["1"], saves)
        self.assertIn(["3"], saves)


# --------------------------------------------------------------------------- #
#  Этап и узел больше не путаются (папка «этап-3» для подэтапа 2.1)
# --------------------------------------------------------------------------- #

class V64StageKindTests(unittest.TestCase):
    """Узел цепочки приводится к виду этапа там, где считается этап."""

    def test_node_kinds_map_to_stage_kinds(self):
        self.assertEqual(gc.stage_kind_for_node("1"), gc.STAGE_INTAKE)
        self.assertEqual(gc.stage_kind_for_node("2.1"), gc.STAGE_MEASURE)
        self.assertEqual(gc.stage_kind_for_node("3"), gc.STAGE_CLOSE)

    def test_stage_number_and_doc_stage_accept_nodes(self):
        case = gc.GovCase(number="1")
        self.assertEqual(case.stage_number("1"), 1)
        self.assertEqual(case.stage_number("2.1"), 2)
        self.assertEqual(case.stage_number("3"), 3)
        self.assertEqual(gc.gov_stage_for_kind("2.1"), gdata.GOV_STAGE_FINAL)
        self.assertEqual(gc.gov_stage_for_kind("1"), gdata.GOV_STAGE_PRIMARY)
        self.assertEqual(gc.gov_stage_for_kind("3"), gdata.GOV_STAGE_FINAL)

    def test_stage_kinds_are_untouched(self):
        self.assertEqual(gc.stage_index_for([{"kind": gc.STAGE_INTAKE},
                                             {"kind": gc.STAGE_MEASURE}],
                                            gc.STAGE_MEASURE), 2)
        self.assertEqual(gc.gov_stage_for_kind(gc.STAGE_MEASURE),
                         gdata.GOV_STAGE_FINAL)

    def test_folder_and_name_follow_the_node(self):
        import tempfile
        case = gc.GovCase(number="1")
        with tempfile.TemporaryDirectory() as root:
            folder = gc.stage_folder(root, "AG-1", case.stage_number("2.1"),
                                     gc.stage_kind_for_node("2.1"),
                                     case_number="1", step_number=1,
                                     doc_type="Об истребовании видеофиксации")
        self.assertIn("этап-2.1", folder.name)
        stem = gc.stage_stem("AG-1", case.stage_number("2.1"),
                             gc.stage_kind_for_node("2.1"),
                             "Об истребовании видеофиксации", step_number=1)
        self.assertIn("Этап_2.1", stem)
        self.assertNotIn("Этап_3", stem)


# --------------------------------------------------------------------------- #
#  П.4 — промт приведён к клиенту
# --------------------------------------------------------------------------- #

class V64PromptTests(unittest.TestCase):
    """Текущий промт описывает реальные заголовки заявок клиента."""

    def setUp(self):
        self.path = _current_prompt()
        if self.path is None:
            self.skipTest("папка промтов вне версии не найдена")
        self.text = self.path.read_text(encoding="utf-8")
        self.answer = gdata.extract_ai_answer(_sample("страница_чата_этап1.txt"))
        self.case = _accepted_case(self.answer)

    def test_prompt_is_at_least_135(self):
        self.assertRegex(self.path.name, r"promt 1\.(3[5-9]|[4-9]\d)\.txt")

    def test_stage_is_taken_from_the_first_request_line(self):
        self.assertNotIn("указан в заявке строкой «Этап:»", self.text)
        self.assertNotIn("который указан строкой «Этап:»", self.text)
        self.assertIn("этап назван в ПЕРВОЙ строке заявки", self.text)

    def test_section7_is_not_bound_to_a_file_name(self):
        self.assertNotIn("имя которого начинается со слов «Постановление Прокурора»",
                         self.text)
        self.assertIn("ЗАЯВКА НА ПОСТАНОВЛЕНИЕ ПО ОБРАЩЕНИЮ (этап …) — ДОКУМЕНТ",
                      self.text)

    def test_dead_request_labels_are_gone(self):
        for label in ("«МЕСТО В ЦЕПОЧКЕ: шаг N из M»",
                      "«НАЗВАНИЕ ШАГА: …»",
                      "«ОБСТОЯТЕЛЬСТВА ПРОКУРОРА»"):
            self.assertNotIn(label, self.text, label)

    def test_two_requests_per_stage_are_described(self):
        self.assertIn("ДВЕ ЗАЯВКИ НА ОДИН ЭТАП", self.text)
        self.assertIn("«Сформировать»", self.text)

    def test_prompt_labels_exist_in_the_client_request(self):
        """Главная проверка: что обещано промтом — то клиент и отправляет."""
        stage1 = gc.node_document_request(self.case, "1", "Jonathan Raine")
        # подэтап 2.1 пройден — тогда в заявке 2.2 есть и память о нём, и
        # строки «УЖЕ ВЫНЕСЕНО ПО ДЕЛУ» / «ОСТАЛОСЬ ПО ПЛАНУ».
        case = self.case
        case.set_work_node("2.1", answer="ПОСТАНОВИЛ: истребовать сведения",
                           done=True, input="Запрошены данные")
        case.mark_work_step(1, doc_number="AG-1")
        step = gc.node_document_request(case, "2.2", "Jonathan Raine")
        node = gc.node_request(case, "2.2", "Jonathan Raine")
        for label in ("ЗАЯВКА НА ПОСТАНОВЛЕНИЕ ПО ОБРАЩЕНИЮ (этап 1) — ДОКУМЕНТ",
                      "СВЕДЕНИЯ ПО ДЕЛУ (память предыдущих этапов):",
                      "ОБРАЩЕНИЕ (текст целиком — во вложении «Обращение.txt»):",
                      "ЭТАП 1 — решение:",
                      "ЗАДАЧА:"):
            self.assertIn(label, stage1, label)
        for label in ("УЖЕ ВЫНЕСЕНО ПО ДЕЛУ:", "ОСТАЛОСЬ ПО ПЛАНУ:",
                      "ЗАЧЕМ ЭТОТ ПОДЭТАП:", "ВАРИАНТ ЦЕПОЧКИ:",
                      "ПОДЭТАП 2.1", "ЗАДАЧА: вынеси постановление по подэтапу 2.2"):
            self.assertIn(label, step + node, label)

    def test_intake_request_label_is_described(self):
        self.assertIn("ЗАЯВКА НА ПРОВЕРКУ ОБРАЩЕНИЯ (этап 1)", self.text)
        request = gc.intake_request(self.case, "Jonathan Raine")
        self.assertTrue(request.startswith("ЗАЯВКА НА ПРОВЕРКУ ОБРАЩЕНИЯ (этап 1)"))

    def test_reorder_request_label_is_described(self):
        self.assertIn("ЗАЯВКА НА КАЛИБРОВКУ ЦЕПОЧКИ ЭТАПА 2", self.text)
        request = gc.reorder_request(self.case, "Jonathan Raine")
        self.assertTrue(request.startswith("ЗАЯВКА НА КАЛИБРОВКУ ЦЕПОЧКИ ЭТАПА 2"))

    def test_recommendations_are_claimed_by_the_client(self):
        self.assertIn("показывает прокурору подсказкой", self.text)
        self.assertIn("ВНУТРЬ постановления", self.text)

    def test_v63_rules_are_kept(self):
        self.assertIn("ТРИ СОСТОЯНИЯ ЭТАПА 1", self.text)
        self.assertIn("ДАТЫ БЕРУТСЯ ТОЛЬКО ИЗ ОБРАЩЕНИЯ", self.text)
        for marker in (ogp_stages.CHAIN_START, ogp_stages.CHAIN_END):
            self.assertIn(marker, self.text)


if __name__ == "__main__":
    unittest.main()
