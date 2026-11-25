"""test_v62_ogp_stages.py — три этапа, три цепочки и два поля (v62).

Что проверяем (согласовано с пользователем перед разработкой):

1. Этап 1 — ровно три состояния: принятие / отказ / переадресация; отказ и
   переадресация закрывают дело, этапы 2 и 3 не наступают.
2. Этап 2 наступает только при принятии; ИИ присылает минимум три варианта
   цепочки; прогресс каждого варианта сохраняется при переключении.
3. Подэтапы идут строго по порядку; ручной порядок согласуется с ИИ, а
   выполненные подэтапы при этом не трогаются.
4. Главная кнопка называется по активному узлу: «Этап 1» → «Этап 2.1» →
   «Этап 2.2» → «Этап 3»; у каждого узла своя кнопка «Сформировать».
5. У узла два поля: поле 1 (ввод прокурора, правится) и поле 2 (ответ ИИ,
   только чтение). Правка поля 1 помечает ответ «устаревшим» и блокирует
   переход к следующему подэтапу.
6. Этап 3 без подэтапов: вид завершения выбирает ИИ по смыслу всех этапов.
7. В заявку к ИИ уходит память всей цепочки (обращение + этапы).
8. Старых вкладок «Постановление» и «Обстоятельства» больше нет, а общий блок
   реквизитов переверстан сеткой без наложений.
"""

from __future__ import annotations

import unittest
from pathlib import Path

import gov_case as gc
import ogp_chain
import ogp_stages

ROOT = Path(__file__).resolve().parent.parent

CHAINS_ANSWER = """===ЦЕПОЧКИ ЭТАПА 2===
===ЦЕПОЧКА A: быстрый путь===
ЗАЧЕМ: доказательств достаточно, санкция сразу
1. Об истребовании видеофиксации — установить факт нарушения
2. О наложении штрафной санкции — привлечь к ответственности по ст. 28
===ЦЕПОЧКА B: полный путь===
ЗАЧЕМ: сначала уголовное дело, потом суд
1. О возбуждении уголовного дела по обращению — открыть производство
2. О вызове на допрос — получить объяснения сотрудника
3. О завершении уголовного дела и передаче его в окружной суд — передать дело
===ЦЕПОЧКА C: через проверку===
1. О проведении дополнительной проверки — собрать материалы
2. О наложении штрафной санкции — привлечь к ответственности
===КОНЕЦ ЦЕПОЧЕК==="""


def _case(state: str = ogp_chain.OUTCOME_ACCEPT) -> gc.GovCase:
    case = gc.GovCase(number="AG-9", appeal_raw="Обращение гражданина: нарушение.")
    case.outcome = state
    return case


def _case_with_chains() -> gc.GovCase:
    case = _case()
    case.apply_chains(ogp_stages.split_chains(CHAINS_ANSWER))
    return case


def _src(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


class ChainParseTests(unittest.TestCase):
    """Разбор блока «===ЦЕПОЧКИ ЭТАПА 2===»."""

    def test_three_variants_are_parsed(self):
        chains = ogp_stages.split_chains(CHAINS_ANSWER)
        self.assertEqual([chain["letter"] for chain in chains], ["A", "B", "C"])
        self.assertEqual(len(chains[1]["steps"]), 3)
        self.assertEqual(chains[0]["title"], "быстрый путь")
        self.assertIn("доказательств достаточно", chains[0]["why"])

    def test_steps_get_kind_and_why(self):
        chain = ogp_stages.split_chains(CHAINS_ANSWER)[1]
        self.assertEqual(chain["steps"][0]["kind"], "case")
        self.assertEqual(chain["steps"][1]["kind"], "interrogation")
        self.assertEqual(chain["steps"][2]["kind"], "court")
        self.assertIn("объяснения", chain["steps"][1]["why"])

    def test_numbers_are_renumbered_in_order(self):
        text = ("===ЦЕПОЧКИ ЭТАПА 2===\n===ЦЕПОЧКА A: тест===\n"
                "3. О вызове на допрос — раз\n1. Об истребовании информации — два\n"
                "===КОНЕЦ ЦЕПОЧЕК===")
        chain = ogp_stages.split_chains(text)[0]
        self.assertEqual([step["n"] for step in chain["steps"]], [1, 2])
        self.assertEqual(chain["steps"][0]["title"], "Об истребовании информации")

    def test_old_single_plan_becomes_variant_a(self):
        old = ("===ПЛАН ЭТАПА 2===\n1. О вызове на допрос — нужно\n"
               "===КОНЕЦ ПЛАНА===")
        chains = ogp_stages.split_chains(old)
        self.assertEqual(len(chains), 1)
        self.assertEqual(chains[0]["letter"], "A")
        self.assertEqual(len(chains[0]["steps"]), 1)

    def test_empty_text_gives_nothing(self):
        self.assertEqual(ogp_stages.split_chains(""), [])


class StageOneTests(unittest.TestCase):
    """Этап 1: три состояния и закрытие дела."""

    def test_only_three_states(self):
        self.assertEqual(len(ogp_stages.STAGE1_STATES), 3)
        self.assertEqual([ogp_stages.stage1_label(state)
                          for state in ogp_stages.STAGE1_STATES],
                         ["принятие", "отказ", "переадресация"])

    def test_refuse_and_redirect_close_the_case(self):
        self.assertTrue(ogp_stages.stage1_closes(ogp_stages.STATE_REFUSE))
        self.assertTrue(ogp_stages.stage1_closes(ogp_stages.STATE_REDIRECT))
        self.assertFalse(ogp_stages.stage1_closes(ogp_stages.STATE_ACCEPT))

    def test_old_five_outcomes_collapse(self):
        self.assertEqual(ogp_stages.stage1_from_outcome(ogp_chain.OUTCOME_DEFECTS),
                         ogp_stages.STATE_REFUSE)
        self.assertEqual(ogp_stages.stage1_from_outcome(ogp_chain.OUTCOME_STOP),
                         ogp_stages.STATE_REFUSE)
        self.assertEqual(ogp_stages.stage1_from_outcome(ogp_chain.OUTCOME_REDIRECT),
                         ogp_stages.STATE_REDIRECT)
        self.assertEqual(ogp_stages.stage1_from_outcome(ogp_chain.OUTCOME_ACCEPT),
                         ogp_stages.STATE_ACCEPT)

    def test_closed_case_has_no_stage_three(self):
        case = _case(ogp_chain.OUTCOME_REDIRECT)
        case.apply_chains(ogp_stages.split_chains(CHAINS_ANSWER))
        self.assertFalse(ogp_stages.node_state(case, "3")["can_run"])
        self.assertIn("не наступают", ogp_stages.node_state(case, "3")["hint"])
        self.assertEqual(ogp_stages.node_caption(case), "Этап 1")


class ChainSwitchTests(unittest.TestCase):
    """Варианты цепочки и сохранение прогресса."""

    def test_three_chains_are_stored(self):
        case = _case_with_chains()
        self.assertEqual(len(case.work_chains), 3)
        self.assertEqual(case.work_letter, "A")
        self.assertEqual(len(case.work_plan), 2)

    def test_progress_survives_switching(self):
        case = _case_with_chains()
        case.set_work_node("2.1", answer="ПОСТАНОВИЛ: истребовать", done=True)
        case.set_work_node("2.2", input="оператор подтвердил запись")
        self.assertTrue(case.use_chain("B"))
        self.assertEqual(case.work_node("2.1"), {}, "в цепочке B прогресса нет")
        self.assertEqual(len(case.work_plan), 3)
        self.assertTrue(case.use_chain("A"))
        self.assertEqual(case.work_node("2.1")["answer"],
                         "ПОСТАНОВИЛ: истребовать")
        self.assertIn("оператор", case.work_node("2.2")["input"])

    def test_switch_keeps_plan_in_sync(self):
        case = _case_with_chains()
        case.use_chain("C")
        self.assertEqual([step["title"] for step in case.work_plan],
                         ["О проведении дополнительной проверки",
                          "О наложении штрафной санкции"])
        self.assertEqual(case.active_chain()["letter"], "C")

    def test_unknown_letter_is_ignored(self):
        case = _case_with_chains()
        self.assertFalse(case.use_chain("Z"))
        self.assertEqual(case.work_letter, "A")

    def test_progress_rows_show_all_chains(self):
        case = _case_with_chains()
        case.set_work_node("2.1", answer="ответ ИИ", done=True)
        rows = ogp_stages.chain_progress(case)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["done"], 1)
        self.assertEqual(rows[1]["done"], 0)
        self.assertTrue(rows[0]["active"])


class CaptionTests(unittest.TestCase):
    """Подпись главной кнопки — активный узел."""

    def test_captions_follow_the_chain(self):
        case = _case_with_chains()
        self.assertEqual(ogp_stages.node_caption(case), "Этап 2.1")
        case.set_work_node("2.1", answer="ответ", done=True)
        self.assertEqual(ogp_stages.node_caption(case), "Этап 2.2")
        case.set_work_node("2.2", answer="ответ", done=True)
        self.assertEqual(ogp_stages.node_caption(case), "Этап 3")

    def test_new_case_shows_stage_one(self):
        case = gc.GovCase(number="1", appeal_raw="текст")
        self.assertEqual(ogp_stages.node_caption(case), "Этап 1")

    def test_empty_appeal_has_no_run(self):
        case = gc.GovCase(number="1")
        self.assertFalse(ogp_stages.node_state(case, "1")["can_run"])

    def test_save_captions(self):
        self.assertEqual(ogp_stages.save_caption("1"),
                         "Сформировать постановление для Этапа 1")
        self.assertEqual(ogp_stages.save_caption("2.3"),
                         "Сформировать постановление для Этапа 2.3")
        self.assertEqual(ogp_stages.save_caption("3"),
                         "Сформировать постановление для Этапа 3")


class OrderTests(unittest.TestCase):
    """Строгий порядок и калибровка порядка ИИ."""

    def test_next_step_blocks_until_answer(self):
        case = _case_with_chains()
        self.assertEqual(case.next_work_step(), 1)
        state = ogp_stages.node_state(case, "2.2")
        self.assertFalse(state["can_run"])
        self.assertIn("2.1", state["hint"])

    def test_previous_step_needs_answer(self):
        case = _case_with_chains()
        case.set_work_node("2.1", done=True)      # ответа нет
        self.assertFalse(ogp_stages.node_state(case, "2.2")["can_run"])

    def test_stale_blocks_the_next_step(self):
        case = _case_with_chains()
        case.set_work_node("2.1", answer="ответ", done=True)
        self.assertTrue(ogp_stages.node_state(case, "2.2")["can_run"])
        case.set_node_input("2.1", "новые сведения")
        self.assertTrue(case.work_node("2.1")["stale"])
        self.assertFalse(ogp_stages.node_state(case, "2.2")["can_run"])

    def test_change_input_marks_answer_stale(self):
        case = _case_with_chains()
        case.set_work_node("2.1", answer="ответ ИИ", done=True)
        self.assertTrue(case.set_node_input("2.1", "другое"))
        self.assertTrue(case.work_node("2.1")["stale"])
        # возврат прежнего текста не снимает устаревание автоматически
        case.set_node_input("2.1", "ответ ИИ")
        self.assertTrue(case.work_node("2.1")["stale"])

    def test_custom_order_keeps_done_steps(self):
        case = _case_with_chains()
        case.set_work_node("2.1", answer="ответ", done=True)
        added = case.apply_new_order([
            {"title": "Об истребовании информации", "why": "нужны данные"},
            {"title": "О наложении штрафной санкции", "why": "санкция"},
        ])
        self.assertEqual(added, 2)
        self.assertEqual(case.work_plan[0]["title"],
                         "Об истребовании видеофиксации")
        self.assertEqual(case.work_plan[1]["title"], "Об истребовании информации")
        self.assertEqual(case.work_node("2.1")["answer"], "ответ")
        self.assertEqual(case.work_custom_order, [])

    def test_stage_three_needs_all_steps(self):
        case = _case_with_chains()
        self.assertFalse(ogp_stages.node_state(case, "3")["can_run"])
        case.set_work_node("2.1", answer="ответ", done=True)
        self.assertFalse(ogp_stages.node_state(case, "3")["can_run"])
        case.set_work_node("2.2", answer="ответ", done=True)
        self.assertTrue(ogp_stages.node_state(case, "3")["can_run"])


class VerdictTests(unittest.TestCase):
    def test_positive_and_negative(self):
        self.assertEqual(ogp_stages.verdict_of(
            "ПОСТАНОВИЛ:\n1. Истребовать видеофиксацию."), "positive")
        self.assertEqual(ogp_stages.verdict_of(
            "ПОСТАНОВИЛ:\n1. В удовлетворении требований отказать."), "negative")
        self.assertEqual(ogp_stages.verdict_of(""), "")


class ContextTests(unittest.TestCase):
    """Память цепочки в заявке к ИИ."""

    def test_context_has_all_stages(self):
        case = _case_with_chains()
        case.intake_basis = "ст. 14 Регламента"
        case.set_work_node("1", input="обращение целиком",
                           answer="===ПРОВЕРКА ОБРАЩЕНИЯ===\nСостояние: Принято")
        case.set_work_node("2.1", input="что было на записи",
                           answer="ПОСТАНОВИЛ: истребовать")
        text = ogp_stages.context_block(case, "2.2")
        self.assertIn("ЭТАП 1 — решение: принятие", text)
        self.assertIn("ст. 14 Регламента", text)
        self.assertIn("ПОДЭТАП 2.1", text)
        self.assertIn("ПОСТАНОВИЛ: истребовать", text)
        self.assertIn("ВАРИАНТЫ ЦЕПОЧКИ ЭТАПА 2", text)

    def test_current_input_is_not_duplicated(self):
        case = _case_with_chains()
        case.set_work_node("2.1", answer="ответ", done=True)
        case.set_node_input("2.2", "текущий ввод прокурора")
        text = ogp_stages.context_block(case, "2.2")
        self.assertNotIn("текущий ввод прокурора", text)

    def test_node_request_contains_input_and_task(self):
        case = _case_with_chains()
        case.set_work_node("2.1", answer="ответ", done=True)
        case.set_node_input("2.2", "оператор подтвердил, что запись есть")
        text = gc.node_request(case, "2.2", "Прокурор X")
        self.assertIn("этап 2.2", text.lower())
        self.assertIn("оператор подтвердил", text)
        self.assertIn("ПОДЭТАП 2.1", text)
        self.assertIn("ЗАДАЧА", text)
        self.assertNotIn("текущий ввод", text)

    def test_stage_three_request_lets_ai_choose(self):
        case = _case_with_chains()
        text = gc.node_request(case, "3")
        self.assertIn("ИТОГОВОЕ решение", text)
        self.assertIn("Сам выбери вид завершения", text)

    def test_reorder_request_lists_custom_order(self):
        case = _case_with_chains()
        case.set_custom_order(["О наложении штрафной санкции",
                               "Об истребовании информации"])
        text = gc.reorder_request(case)
        self.assertIn("ПРОКУРОР ИЗМЕНИЛ ПОРЯДОК", text)
        self.assertIn("1. О наложении штрафной санкции", text)
        self.assertIn("Выполненные подэтапы не меняй", text)


class PrettyAnswerTests(unittest.TestCase):
    """Поле 2 показывается как документ, а не как JSON."""

    def test_fields_go_in_document_order(self):
        class FakeDoc:
            def to_dict(self):
                return {"author_name": "Marcus Nomad", "doc_type": "О принятии",
                        "doc_number": "AG-4", "established": "Установлено …",
                        "resolved": "1. Принять к производству."}

        text = ogp_stages.pretty_answer(FakeDoc())
        self.assertTrue(text.startswith("Номер постановления: AG-4"))
        self.assertIn("Постановление вынес: Marcus Nomad", text)
        self.assertIn("УСТАНОВИЛ:", text)
        self.assertIn("ПОСТАНОВИЛ:", text)
        self.assertNotIn('"author_name"', text)

    def test_raw_answer_strips_service_markers(self):
        text = ogp_stages.pretty_answer(None, "===ПОСТАНОВЛЕНИЕ===\nПОСТАНОВИЛ: тест")
        self.assertNotIn("===", text)
        self.assertIn("ПОСТАНОВИЛ: тест", text)


class SerializeTests(unittest.TestCase):
    """Дело сохраняет и восстанавливает цепочки и узлы."""

    def test_round_trip(self):
        case = _case_with_chains()
        case.set_work_node("2.1", answer="ответ", done=True,
                           png="/tmp/AG-9/эт.2.1/page1.png")
        case.set_node_input("2.2", "ввод")
        restored = gc.GovCase.from_dict(case.to_dict())
        self.assertEqual(len(restored.work_chains), 3)
        self.assertEqual(restored.work_letter, "A")
        self.assertEqual(restored.work_node("2.1")["png"],
                         "/tmp/AG-9/эт.2.1/page1.png")
        self.assertEqual(restored.work_node("2.2")["input"], "ввод")

    def test_legacy_case_becomes_single_chain(self):
        legacy = {"number": "7", "appeal_raw": "текст",
                  "status": "in_progress", "outcome": "accept",
                  "work_plan": [{"n": 1, "title": "О вызове на допрос",
                                 "done": True, "doc_number": "AG-7"}]}
        case = gc.GovCase.from_dict(legacy)
        self.assertEqual(len(case.work_chains), 1)
        self.assertEqual(case.work_chains[0]["letter"], "A")
        self.assertEqual(case.work_node("2.1")["doc_number"], "AG-7")
        self.assertTrue(case.work_node("2.1")["done"])


class FormSourceTests(unittest.TestCase):
    """Раскладка формы после v62."""

    def setUp(self):
        self.ui = _src("postanovlenie_prokuror_ui.py")

    def test_old_tabs_are_removed(self):
        self.assertNotIn('self.tabs.addTab(self._build_work_tab()', self.ui)
        self.assertNotIn('self.tabs.addTab(self._build_document_tab()', self.ui)
        for tab in ("Обращение", "Цепочка этапов", "История"):
            self.assertIn(f'"{tab}")', self.ui)

    def test_chain_tab_has_all_three_stages(self):
        block = self.ui[self.ui.index("def _build_chain_tab"):
                        self.ui.index("def _refresh_chain_view")]
        for marker in ("Этап 1. Решение по обращению",
                       "Этап 2. Цепочка постановлений по существу",
                       "Этап 3. Итог по обращению"):
            self.assertIn(marker, block)
        self.assertIn("ogp_stages.STAGE1_STATES", block)
        self.assertIn("_save_node_document", block)
        self.assertIn("Согласовать порядок", block)

    def test_each_stage_has_its_own_save_button(self):
        for name in ("stage1_save", "step_save", "stage3_save"):
            self.assertIn(f"self.{name} = QPushButton", self.ui)
        self.assertIn("save_caption", self.ui)

    def test_input_change_marks_answer_stale(self):
        block = self.ui[self.ui.index("def _on_facts_input_changed"):
                        self.ui.index("def _save_facts")]
        self.assertIn("set_node_input", block)

    def test_answer_field_is_read_only(self):
        self.assertIn("self.facts_answer.setReadOnly(True)", self.ui)

    def test_main_button_sends_node_not_print(self):
        block = self.ui[self.ui.index("def _run_main_action"):
                        self.ui.index("def _send_node")]
        self.assertIn("_send_node", block)
        self.assertNotIn("print_node_document", block)

    def test_requisites_use_grid(self):
        block = self.ui[self.ui.index("def _build_requisites"):
                        self.ui.index("def _refresh_main_action")]
        self.assertIn("QGridLayout", block)
        self.assertIn("Сохранение", block)
        # длинной строки про путь PNG больше нет
        self.assertNotIn("PNG сохраняется сюда", self.ui)

    def test_long_hints_are_removed(self):
        for text in ("Сначала вставьте текст обращения и нажмите",
                     "Эти блоки — данные для ИИ",
                     "Проверь ТОЛЬКО само обращение"):
            self.assertNotIn(text, self.ui)
        self.assertNotIn("нажмите «Этап 1»: ИИ решит", self.ui)

    def test_main_prints_node_png(self):
        main = _src("main.py")
        self.assertIn("def _print_gov_node", main)
        self.assertIn("dlg.print_node_document.connect", main)
        self.assertIn("_doc_node_kind", main)

    def test_prompt_133_exists(self):
        prompt = (ROOT.parent / "Для новой версии" / "Текущий промт"
                  / "promt 1.33.txt")
        self.assertTrue(prompt.exists(), "нужен новый файл промта 1.33")
        text = prompt.read_text(encoding="utf-8")
        self.assertIn("1.33", text.splitlines()[0])
        self.assertIn("ЦЕПОЧКИ ЭТАПА 2", text)


if __name__ == "__main__":
    unittest.main()
