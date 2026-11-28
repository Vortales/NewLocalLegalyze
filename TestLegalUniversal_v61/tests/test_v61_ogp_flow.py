"""test_v61_ogp_flow.py — одна главная кнопка «Этап 1 / Этап 2 / Этап 3» (v61).

Замечания пользователя, которые проверяются здесь:

1. «На 1 этапе, когда обращение ещё не было отправлено в чат с ИИ, „Проверка
   ИИ“ должна быть на месте „Сформировать“ и называться „Этап 1“».
2. «Как только 1 этап выполнен, кнопка меняет название на „Этап 2“».
3. «На 2 этапе… в „Цепочка этапов“ появляется рекомендуемый порядок
   автоматически после ответа ИИ на 1 этапе».
4. «На каждый подэтап Этапа 2 — формирование Постановления (.png)».
5. «Как только все подэтапы этапа 2 пройдены и ИИ дал положительный ответ,
   то мы уже можем закрывать обращение и переходить к этапу 3».
6. Реквизиты (номер, дата, ФИО, должность, удостоверение) — отдельный общий
   блок снизу главного окна.
"""

from __future__ import annotations

import unittest
from pathlib import Path

import gov_case
import ogp_chain
import ogp_flow
import postanovlenie_prokuror_data as pgov

ROOT = Path(__file__).resolve().parent.parent


def _src(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _plan(count: int = 3) -> list:
    kinds = list(ogp_chain.STEP_KINDS)
    return [{"n": i + 1,
             "kind": kinds[i % len(kinds)],
             "title": f"Шаг {i + 1}",
             "why": "по плану ИИ"}
            for i in range(count)]


def _case(raw: str = "Текст обращения") -> gov_case.GovCase:
    case = gov_case.GovCase(number="AG-77")
    case.appeal_raw = raw
    return case


class MainButtonTests(unittest.TestCase):
    """Подпись главной кнопки зависит только от состояния дела."""

    def test_empty_appeal_disables_etap_1(self):
        button = ogp_flow.main_action(_case(""))
        self.assertEqual(button["caption"], "Этап 1")
        self.assertEqual(button["action"], ogp_flow.ACTION_INTAKE)
        self.assertFalse(button["enabled"], "без текста обращения этап 1 не идёт")

    def test_fresh_appeal_shows_etap_1(self):
        button = ogp_flow.main_action(_case())
        self.assertEqual(button["caption"], "Этап 1")
        self.assertTrue(button["enabled"])
        self.assertIn("этап 1", button["hint"].lower())

    def test_after_accept_button_becomes_etap_2(self):
        case = _case()
        case.outcome = ogp_chain.OUTCOME_ACCEPT
        case.apply_plan(_plan(4))
        button = ogp_flow.main_action(case)
        self.assertEqual(button["caption"], "Этап 2")
        self.assertEqual(button["action"], ogp_flow.ACTION_STEP)
        self.assertIn("шаг 1 из 4", button["hint"])

    def test_refusal_closes_chain(self):
        case = _case()
        case.outcome = ogp_chain.OUTCOME_REFUSE
        button = ogp_flow.main_action(case)
        self.assertFalse(button["enabled"])
        self.assertIn("завершён", button["caption"])

    def test_accept_without_plan_waits_for_intake(self):
        case = _case()
        case.outcome = ogp_chain.OUTCOME_ACCEPT
        button = ogp_flow.main_action(case)
        self.assertEqual(button["caption"], "Этап 2")
        self.assertFalse(button["enabled"],
                         "без плана этапа 2 кнопка не должна печатать документ")

    def test_all_steps_positive_switches_to_etap_3(self):
        case = _case()
        case.outcome = ogp_chain.OUTCOME_ACCEPT
        case.apply_plan(_plan(2))
        case.mark_work_step(1, verdict="positive")
        self.assertEqual(ogp_flow.main_action(case)["caption"], "Этап 2")
        case.mark_work_step(2, verdict="positive")
        button = ogp_flow.main_action(case)
        self.assertEqual(button["caption"], "Этап 3")
        self.assertEqual(button["action"], ogp_flow.ACTION_FINAL)
        self.assertTrue(case.etap3_ready())

    def test_step_without_positive_verdict_blocks_etap_3(self):
        case = _case()
        case.outcome = ogp_chain.OUTCOME_ACCEPT
        case.apply_plan(_plan(2))
        case.mark_work_step(1, verdict="positive")
        case.mark_work_step(2, verdict="negative")
        self.assertTrue(case.work_done())
        self.assertFalse(case.etap3_ready(),
                         "отрицательный итог шага не закрывает обращение")
        self.assertEqual(ogp_flow.main_action(case)["caption"], "Этап 3")
        self.assertFalse(ogp_flow.all_steps_positive(case))

    def test_step_without_verdict_blocks_etap_3(self):
        case = _case()
        case.outcome = ogp_chain.OUTCOME_ACCEPT
        case.apply_plan(_plan(1))
        case.mark_work_step(1)
        self.assertTrue(case.work_done())
        self.assertFalse(case.etap3_ready())


class BadgeTests(unittest.TestCase):
    """Короткая подпись этапа для заголовка окна."""

    def test_badges(self):
        case = _case()
        self.assertEqual(ogp_flow.stage_badge(case), "этап 1")
        case.outcome = ogp_chain.OUTCOME_ACCEPT
        case.apply_plan(_plan(3))
        self.assertEqual(ogp_flow.stage_badge(case), "этап 2 — шаг 1 из 3")
        case.mark_work_step(1, verdict="positive")
        self.assertEqual(ogp_flow.stage_badge(case), "этап 2 — шаг 2 из 3")
        case.mark_work_step(2, verdict="positive")
        case.mark_work_step(3, verdict="positive")
        self.assertEqual(ogp_flow.stage_badge(case), "этап 3")

    def test_refusal_badge(self):
        case = _case()
        case.outcome = ogp_chain.OUTCOME_REFUSE
        self.assertIn("этап 1", ogp_flow.stage_badge(case))


class VerdictTests(unittest.TestCase):
    """Итог шага этапа 2 распознаётся по резолютивной части ответа ИИ."""

    def test_positive(self):
        text = ("ПОСТАНОВИЛ:\n1. Признать гражданина виновным в нарушении.\n"
                "2. Назначить штраф 3000 рублей.")
        self.assertEqual(ogp_flow.verdict_of(text), "positive")

    def test_negative(self):
        text = ("ПОСТАНОВИЛ:\n1. В удовлетворении требований отказать.\n"
                "2. Материалы проверки прекратить.")
        self.assertEqual(ogp_flow.verdict_of(text), "negative")

    def test_motivation_part_does_not_decide(self):
        text = ("УСТАНОВИЛ:\nОснований для отказа не выявлено, нарушение "
                "подтверждено.\nПОСТАНОВИЛ:\n1. Привлечь к ответственности.")
        self.assertEqual(ogp_flow.verdict_of(text), "positive")

    def test_empty_text(self):
        self.assertEqual(ogp_flow.verdict_of(""), "")
        self.assertEqual(ogp_flow.verdict_of(None), "")

    def test_apply_verdict_records_step(self):
        case = _case()
        case.outcome = ogp_chain.OUTCOME_ACCEPT
        case.apply_plan(_plan(2))
        verdict = ogp_flow.apply_verdict(
            case, 1, "ПОСТАНОВИЛ: истребовать документы.")
        self.assertEqual(verdict, "positive")
        self.assertEqual(case.work_plan[0]["verdict"], "positive")

    def test_apply_verdict_unknown_step(self):
        case = _case()
        case.apply_plan(_plan(1))
        self.assertEqual(ogp_flow.apply_verdict(
            case, 9, "ПОСТАНОВИЛ: истребовать документы."), "")


class FormSourceTests(unittest.TestCase):
    """Раскладка формы: одна главная кнопка и общий блок реквизитов."""

    def test_main_button_is_etap(self):
        src = _src("postanovlenie_prokuror_ui.py")
        self.assertIn('QPushButton("Этап 1")', src)
        self.assertIn("self.form_btn", src)
        self.assertIn("_run_main_action", src)
        self.assertIn("_refresh_main_action", src)
        # «Проверка ИИ» осталась только как возврат на этап 1
        self.assertIn('QPushButton("Вернуться на этап 1")', src)
        self.assertNotIn('QPushButton("Проверка ИИ")', src)

    def test_requisites_are_shared_block_below_tabs(self):
        src = _src("postanovlenie_prokuror_ui.py")
        self.assertIn("def _build_requisites", src)
        self.assertIn("root.addWidget(self._build_requisites())", src)
        block = src[src.index("def _build_requisites"):
                    src.index("def _refresh_main_action")]
        for key in ("doc_number", "doc_date", "author_name", "author_rank",
                    "author_id"):
            self.assertIn(key, block)
        # подписи, которые просил пользователь
        for label in ("Номер", "Дата", "ФИО", "Должность", "Удостоверение"):
            self.assertIn(label, block)

    def test_main_action_refreshed_with_case(self):
        src = _src("postanovlenie_prokuror_ui.py")
        block = src[src.index("def _refresh_case_view"):
                    src.index("def _on_appeal_changed")]
        self.assertIn("_refresh_main_action()", block)
        block = src[src.index("def _refresh_chain_view"):
                    src.index("def _refresh_chain_view") + 4000]
        self.assertIn("ogp_flow.stage_badge", block)

    def test_each_step_has_own_png_folder(self):
        src = _src("main.py")
        self.assertIn("step_number=step_number", src)
        self.assertIn("verdict=verdict", src)

    def test_prompt_version_bumped(self):
        prompt = (ROOT.parent / "Для новой версии" / "Текущий промт"
                  / "promt 1.32.txt")
        self.assertTrue(prompt.exists(), "нужен новый файл промта 1.32")
        text = prompt.read_text(encoding="utf-8")
        self.assertIn("1.32", text.splitlines()[0] if text else "")


class DocumentFieldsTests(unittest.TestCase):
    """Реквизиты, вынесенные из документа в общий блок, не теряются."""

    def test_document_keeps_requisites(self):
        doc = pgov.PostanovlenieProkurora()
        for key in ("doc_number", "doc_date", "author_name", "author_rank"):
            self.assertIn(key, doc.to_dict())


if __name__ == "__main__":
    unittest.main()
