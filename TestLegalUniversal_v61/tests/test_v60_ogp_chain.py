"""Тесты v60: три этапа обращения в ОГП и «Документы → Обращение в ОГП».

Замечание пользователя (`Этапы Постановления.txt`):

* этап 1 — только судьба обращения, пользователь ИИ не уточняет;
* этап 2 — не один документ, а цепочка шагов, которую ИИ моделирует сам и
  отдаёт планом в GUI;
* этап 3 — итог, только если этап 1 «принято» и этап 2 пройден полностью;
* «Обращение в ОГП» — анкета уходит ИИ, обратно приходит готовый BB-код.

Тесты проверяют чистую логику (без Qt): разбор плана, исходы, нумерацию
шагов и сборку/разбор заявки обращения.
"""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

import gov_case as cases
import ogp_appeal as appeal_mod
import ogp_chain
import postanovlenie_prokuror_data as gdata

ROOT = Path(__file__).resolve().parent.parent

PLAN_ANSWER = """===ПРОВЕРКА ОБРАЩЕНИЯ===
Состояние: Принято
Основание: ст. 14 Регламента, форма соблюдена
Вид решения: О принятии обращения к производству
Что проверить: изъятие боеприпасов при первичном обыске
===КОНЕЦ ПРОВЕРКИ===
===ПЛАН ЭТАПА 2===
1. О возбуждении уголовного дела по обращению — нужно зафиксировать состав
2. О внесении изменений в постановление — уточнить квалификацию
3. О приостановлении действия трудового договора — отстранить до итога
4. Об истребовании информации — получить служебные документы
5. О вызове на допрос — получить объяснения нарушителя
6. О наложении штрафной санкции — итоговая мера
===КОНЕЦ ПЛАНА===
===КОНЕЦ ОТВЕТА==="""


class OutcomeTests(unittest.TestCase):
    """Этап 1: пять исходов по блок-схеме ОГП."""

    def test_five_outcomes_are_declared(self):
        self.assertEqual(len(ogp_chain.OUTCOMES), 5)
        self.assertEqual(set(ogp_chain.OUTCOME_ORDER), set(ogp_chain.OUTCOMES))

    def test_defects_are_recognised_before_refusal(self):
        # «оставить без движения» содержит и «оставить», и признаки отказа —
        # порядок проверки не должен путать эти исходы.
        self.assertEqual(
            ogp_chain.outcome_from_text("Оставить без движения на 24 часа"),
            ogp_chain.OUTCOME_DEFECTS)

    def test_refusal_is_not_mistaken_for_acceptance(self):
        text = "Состояние: Отказано\nОснование: пункт 14 части 1 статьи 16"
        self.assertEqual(ogp_chain.outcome_from_text(text),
                         ogp_chain.OUTCOME_REFUSE)

    def test_redirect_and_stop(self):
        self.assertEqual(ogp_chain.outcome_from_text("переадресовать в IB FIB"),
                         ogp_chain.OUTCOME_REDIRECT)
        self.assertEqual(ogp_chain.outcome_from_text("обращение утратило предмет"),
                         ogp_chain.OUTCOME_STOP)

    def test_only_acceptance_opens_etap_2(self):
        self.assertEqual(ogp_chain.next_etap(ogp_chain.OUTCOME_ACCEPT),
                         ogp_chain.ETAP_2)
        for outcome in (ogp_chain.OUTCOME_REFUSE, ogp_chain.OUTCOME_REDIRECT,
                        ogp_chain.OUTCOME_STOP):
            self.assertEqual(ogp_chain.next_etap(outcome), "")

    def test_each_outcome_has_its_own_document(self):
        docs = {ogp_chain.outcome_doc(outcome) for outcome in ogp_chain.OUTCOMES}
        self.assertEqual(len(docs), 5, "у каждого исхода свой документ")


class PlanParsingTests(unittest.TestCase):
    """План этапа 2 из ответа ИИ."""

    def test_plan_is_parsed_in_order(self):
        steps = ogp_chain.parse_plan(PLAN_ANSWER)
        self.assertEqual([step["n"] for step in steps], [1, 2, 3, 4, 5, 6])
        self.assertEqual(steps[0]["kind"], "case")
        self.assertEqual(steps[3]["kind"], "demand")
        self.assertEqual(steps[5]["kind"], "penalty")

    def test_plan_reason_is_kept(self):
        steps = ogp_chain.parse_plan(PLAN_ANSWER)
        self.assertIn("зафиксировать состав", steps[0]["why"])

    def test_no_plan_means_empty_list(self):
        self.assertEqual(ogp_chain.parse_plan("обычный ответ без плана"), [])

    def test_plan_tolerates_other_numbering(self):
        text = ("===ПЛАН ЭТАПА 2===\n"
                "1) Об истребовании видеофиксации; нужны тайм-коды\n"
                "Шаг 2 - О вызове на допрос — объяснения\n"
                "===КОНЕЦ ПЛАНА===")
        steps = ogp_chain.parse_plan(text)
        self.assertEqual([s["kind"] for s in steps], ["video", "interrogation"])

    def test_unknown_step_is_kept(self):
        steps = ogp_chain.parse_plan(
            "===ПЛАН ЭТАПА 2===\n1. О чём-то неведомом — зачем-то\n"
            "===КОНЕЦ ПЛАНА===")
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["kind"], "")


class CaseChainTests(unittest.TestCase):
    """Дело: исход этапа 1 + цепочка шагов этапа 2 + готовность этапа 3."""

    def setUp(self):
        self.case = cases.GovCase(number="1")

    def _accept_with_plan(self):
        self.case.set_outcome(ogp_chain.OUTCOME_ACCEPT)
        self.case.apply_plan(ogp_chain.parse_plan(PLAN_ANSWER))

    def test_acceptance_switches_case_to_work(self):
        self.case.set_outcome(ogp_chain.OUTCOME_ACCEPT)
        self.assertTrue(self.case.accepted())
        self.assertEqual(self.case.outcome, ogp_chain.OUTCOME_ACCEPT)

    def test_refusal_closes_the_case(self):
        self.case.set_outcome(ogp_chain.OUTCOME_REFUSE)
        self.assertFalse(self.case.etap3_ready())
        self.assertEqual(self.case.work_plan, [])

    def test_defects_do_not_close_the_case(self):
        self.case.set_outcome(ogp_chain.OUTCOME_DEFECTS)
        self.assertEqual(self.case.status, cases.STATUS_NEW,
                         "24 часа на устранение — обращение ещё живо")

    def test_plan_is_stored_with_numbers(self):
        self._accept_with_plan()
        self.assertEqual(len(self.case.work_plan), 6)
        self.assertEqual(self.case.work_plan[0]["n"], 1)
        self.assertEqual(self.case.next_work_step(), 1)

    def test_etap_3_needs_all_steps_done(self):
        self._accept_with_plan()
        self.assertFalse(self.case.etap3_ready())
        for number in range(1, 7):
            # v61: одного «шаг выполнен» мало — нужен положительный итог ИИ
            # по каждому шагу (замечание пользователя).
            self.case.mark_work_step(number, doc_number=f"10{number}",
                                     verdict="positive")
        self.assertTrue(self.case.work_done())
        self.assertTrue(self.case.etap3_ready())

    def test_repeated_plan_keeps_done_steps(self):
        self._accept_with_plan()
        self.case.mark_work_step(1, doc_number="1088", png="/tmp/a.png")
        self.case.apply_plan(ogp_chain.parse_plan(PLAN_ANSWER))
        first = self.case.work_plan[0]
        self.assertTrue(first["done"], "повторный план не должен стирать работу")
        self.assertEqual(first["doc_number"], "1088")

    def test_own_step_is_added(self):
        self._accept_with_plan()
        self.case.add_work_step("О запросе личного дела сотрудника")
        self.assertEqual(len(self.case.work_plan), 7)
        self.assertEqual(self.case.work_plan[-1]["kind"], "personnel")

    def test_step_can_be_removed(self):
        self._accept_with_plan()
        self.assertTrue(self.case.remove_work_step(2))
        self.assertEqual([step["n"] for step in self.case.work_plan],
                         [1, 2, 3, 4, 5])
        self.assertEqual(self.case.work_plan[1]["title"],
                         "О приостановлении действия трудового договора")

    def test_summary_shows_chain(self):
        self._accept_with_plan()
        summary = self.case.etaps_summary()
        self.assertIn("этап 1: принять к производству", summary)
        self.assertIn("этап 2: шагов 0 из 6", summary)

    def test_stage_request_names_the_step(self):
        self._accept_with_plan()
        self.case.mark_work_step(1, doc_number="1088")
        request = cases.stage_request(self.case, cases.STAGE_MEASURE, _Doc())
        self.assertIn("шаг 2 из 6", request)
        self.assertIn("О внесении изменений в постановление", request)
        self.assertIn("УЖЕ ВЫНЕСЕНО", request)
        self.assertIn("№1088", request)


class FileNameTests(unittest.TestCase):
    """Имена и папки шагов этапа 2: «эт.2.3», «Этап_2.3_допрос»."""

    def test_step_number_in_stem(self):
        stem = cases.stage_stem("AG-1", 2, cases.STAGE_MEASURE,
                                "О вызове на допрос", step_number=3)
        self.assertIn("Этап_2.3", stem)
        self.assertIn("допрос", stem)

    def test_step_folder_is_per_step(self):
        root = Path(tempfile.mkdtemp())
        folder = cases.stage_folder(root, "AG-1", 2, cases.STAGE_MEASURE,
                                    case_number="1", step_number=3,
                                    doc_type="О вызове на допрос")
        self.assertTrue(folder.exists())
        self.assertIn("этап-2.3", folder.name)
        self.assertIn("допрос", folder.name)

    def test_etap_folders_do_not_collide(self):
        root = Path(tempfile.mkdtemp())
        first = cases.stage_folder(root, "AG-1", 2, cases.STAGE_MEASURE,
                                   case_number="1", step_number=1,
                                   doc_type="О возбуждении уголовного дела")
        second = cases.stage_folder(root, "AG-1", 2, cases.STAGE_MEASURE,
                                    case_number="1", step_number=2,
                                    doc_type="О внесении изменений")
        self.assertNotEqual(first, second,
                            "постановления разных шагов не должны затирать друг друга")

    def test_etap_folder_round_trip(self):
        self.assertEqual(ogp_chain.etap_from_folder("эт.2.3"), ("work", 3))
        self.assertEqual(ogp_chain.etap_from_folder("эт.1"), ("intake", 0))
        self.assertEqual(ogp_chain.etap_folder("work", 3), "эт.2.3")


class IntakeStatesTests(unittest.TestCase):
    """Пятый исход дошёл до разбора ответа ИИ и не путается с заявкой."""

    def test_defects_state_is_known(self):
        self.assertIn(gdata.INTAKE_DEFECTS, gdata.GOV_INTAKE_STATES)
        self.assertEqual(gdata.normalize_intake_state("Недостатки"),
                         gdata.INTAKE_DEFECTS)

    def test_plan_block_does_not_break_state_parsing(self):
        result = cases.parse_intake_answer(PLAN_ANSWER)
        self.assertTrue(result["ok"])
        self.assertEqual(result["state"], gdata.INTAKE_ACCEPTED)

    def test_request_is_not_mistaken_for_answer(self):
        case = cases.GovCase(number="1")
        case.appeal_raw = "Обращение гражданина Sae Gucci"
        request = cases.intake_request(case, "Jonathan Raine")
        self.assertFalse(cases.parse_intake_answer(request)["ok"],
                         "собственная заявка не должна давать состояние")
        self.assertIn("ПЛАН", request.upper(),
                      "заявка обязана требовать план этапа 2")
        self.assertIn(gdata.INTAKE_DEFECTS, request)


class AppealTests(unittest.TestCase):
    """«Обращение в ОГП»: анкета → заявка → BB-код."""

    VALUES = {
        "applicant": "Max Ferstapaninov",
        "applicant_id": "54011",
        "phone": "16473399",
        "email": "pofibuem@ds.com",
        "category": appeal_mod.CATEGORIES[3],
        "offender": "Danilich Suspect",
        "offender_id": "97849",
        "offender_org": "LSPD",
        "date_time": "15.11.2026 15:56",
        "situation": "При первичном обыске изъяли патроны 7.62.",
        "evidence": "Видео: 03:12 — изъятие патронов; 05:01 — бездействие.",
        "id_copy": "https://forum.example/id.jpg",
    }

    ANSWER = """===ОБРАЩЕНИЕ BB===
[B]В Офис Генерального прокурора[/B]
От: гражданина штата Сан-Андреас Max Ferstapaninov
В: Уважаемый Офис Генерального Прокурора штата Сан-Андреас
1. Данные о заявителе: Max Ferstapaninov; ID 54011
2. Предмет обращения: заявление о нарушении законодательства
3. Сведения о нарушителе: Danilich Suspect; 97849; LSPD
4. Дата и время события: 15.11.2026 15:56
5. Подробное описание ситуации: при первичном обыске изъяты патроны.
6. Ксерокопия идентификационной карты: https://forum.example/id.jpg
7. Электронную почту и номер телефона: pofibuem@ds.com, 16473399
8. Доказательства правонарушения: видео 03:12
Подпись и расшифровка заявителя: M. Ferstapaninov
===КОНЕЦ ОБРАЩЕНИЯ===
Чего не хватает: нет"""

    def test_subject_matches_regulation(self):
        self.assertEqual(appeal_mod.subject_for("Max Ferstapaninov"),
                         "Обращение в ОГП от гр-а Max Ferstapaninov")
        self.assertIn("Генеральную прокуратуру",
                      appeal_mod.subject_for("Max", against_officer=True))

    def test_categories_are_from_article_5(self):
        self.assertEqual(len(appeal_mod.CATEGORIES), 7)
        self.assertIn("заявление об обжаловании решения", appeal_mod.CATEGORIES[-1])

    def test_prefill_takes_constants_from_template(self):
        filled = appeal_mod.prefill({"Name": "Max", "Phone": "16473399",
                                     "Email": "pofibuem@ds.com"})
        self.assertEqual(filled["phone"], "16473399")
        self.assertEqual(filled["email"], "pofibuem@ds.com")
        self.assertEqual(filled["applicant"], "Max")

    def test_empty_template_field_is_reported(self):
        missing = dict(appeal_mod.missing_fields({**self.VALUES,
                                                  "phone": "", "email": ""}))
        self.assertIn("phone", missing)
        self.assertIn("email", missing)

    def test_required_fields_are_listed(self):
        missing = dict(appeal_mod.missing_fields({"applicant": "Max"}))
        self.assertIn("applicant_id", missing)
        self.assertIn("evidence", missing)
        self.assertIn("date_time", missing)

    def test_request_contains_form_and_rules(self):
        text = appeal_mod.build_request(self.VALUES, subject="Обращение в ОГП")
        for mark in ("От:", "В:", "1. Данные о заявителе",
                     "5. Подробное описание ситуации",
                     "6. Ксерокопия идентификационной карты",
                     "8. Доказательства правонарушения",
                     "Подпись и расшифровка"):
            self.assertIn(mark, text, mark)
        self.assertIn(appeal_mod.APPEAL_START, text)
        self.assertIn("тайм-коды", text)

    def test_request_skips_empty_optional_fields(self):
        values = {key: value for key, value in self.VALUES.items()
                  if key not in ("representative", "contract")}
        text = appeal_mod.build_request(values)
        self.assertNotIn("Представитель (Имя Фамилия, ID", text.split(
            "Форма ответа")[0])

    def test_answer_is_extracted(self):
        appeal = appeal_mod.extract_appeal(self.ANSWER)
        self.assertTrue(appeal.startswith("[B]В Офис"))
        self.assertTrue(appeal.endswith("M. Ferstapaninov"))
        self.assertNotIn(appeal_mod.APPEAL_END, appeal)

    def test_form_check_passes(self):
        self.assertEqual(appeal_mod.check_form(self.ANSWER), [])

    def test_form_check_finds_missing_points(self):
        broken = self.ANSWER.replace("8. Доказательства правонарушения", "8. Что-то")
        missing = dict(appeal_mod.check_form(broken))
        self.assertIn("evidence", missing)

    def test_notes_are_read(self):
        self.assertEqual(appeal_mod.extract_notes(self.ANSWER), "нет")

    def test_answer_without_markers_gives_nothing(self):
        self.assertEqual(appeal_mod.extract_appeal("обычный ответ"), "")

    def test_file_name_is_safe(self):
        name = appeal_mod.docx_name({"applicant": "Max Ferstapaninov"},
                                    date="15.11.2026")
        self.assertTrue(name.endswith(".txt"))
        self.assertNotIn("/", name)
        self.assertIn("Max Ferstapaninov", name)


class SourceIntegrationTests(unittest.TestCase):
    """Связки в исходниках: меню, разбор ответа, подсказки формы."""

    @staticmethod
    def _src(name: str) -> str:
        return (ROOT / name).read_text(encoding="utf-8")

    def test_menu_has_ogp_appeal(self):
        source = self._src("main.py")
        self.assertIn('QPushButton("Обращение в ОГП")', source)
        self.assertIn("_open_appeal_ogp", source)

    def test_answer_route_is_wired(self):
        source = self._src("main.py")
        self.assertIn('mode == "appeal"', source)
        self.assertIn("_apply_appeal_ogp", source)

    def test_chain_tab_exists(self):
        source = self._src("postanovlenie_prokuror_ui.py")
        self.assertIn('self.tabs.addTab(self._build_chain_tab(), "Цепочка этапов")', source)
        self.assertIn("ogp_chain.parse_plan(text)", source)
        self.assertIn("_set_outcome", source)

    def test_template_has_contact_constants(self):
        source = self._src("config.py")
        for field in ("Phone", "Email", "Discord"):
            self.assertIn(f'"{field}"', source, field)

    def test_version(self):
        config = self._src("config.py")
        match = re.search(r'CURRENT_VERSION = "(\d+)\.(\d+)\.', config)
        self.assertIsNotNone(match)
        self.assertGreaterEqual((int(match.group(1)), int(match.group(2))), (2, 16))


class _Doc:
    """Заглушка документа для `stage_request` (без PyQt и рендера)."""

    def to_chat_txt(self) -> str:
        return "ЗАЯВКА"


if __name__ == "__main__":      # pragma: no cover
    unittest.main()
