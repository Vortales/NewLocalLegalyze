"""v43.2: по делу со статусом «Отказано» постановление (PNG) всё равно печатается.

Замечание пользователя: «если обращение имеет статус "Отказано", сформировать
.png постановление всё равно нужно».
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gov_case as cases  # noqa: E402
import postanovlenie_prokuror_data as gdata  # noqa: E402


def _P(name):
    return Path(__file__).resolve().parent.parent / name


def _refused_case(number="5"):
    case = cases.GovCase(number=number)
    case.appeal_raw = "Обращение гражданина текст"
    case.apply_intake({"state": cases.INTAKE_REFUSED,
                       "basis": "п. 2 ч. 1 ст. 16 Регламента (обращение анонимно)"})
    return case


class RefusalDocumentTests(unittest.TestCase):
    """Отказ в принятии — первичное решение (этап 1), а не санкция."""

    def test_refused_case_prints_primary_stage(self):
        case = _refused_case()
        self.assertTrue(case.refused())
        self.assertFalse(case.accepted())
        self.assertEqual(cases.primary_stage_kind(case), cases.STAGE_INTAKE)
        self.assertEqual(case.stage_number(cases.primary_stage_kind(case)), 1)

    def test_refused_case_has_no_blocking_reason(self):
        case = _refused_case()
        kind = cases.primary_stage_kind(case)
        self.assertEqual(cases.stage_block_reason(case, kind), "")
        self.assertTrue(cases.stage_is_ready(case, kind, ""))

    def test_refusal_does_not_require_facts(self):
        case = _refused_case()
        self.assertFalse(case.has_facts())
        self.assertFalse(cases.stage_meta(cases.primary_stage_kind(case))["needs_facts"])

    def test_measure_still_blocked_without_acceptance(self):
        case = _refused_case()
        self.assertIn("принятия", cases.stage_block_reason(case, cases.STAGE_MEASURE))
        self.assertFalse(cases.stage_is_ready(case, cases.STAGE_MEASURE, "нет"))

    def test_accepted_case_is_not_redirected_to_intake(self):
        case = cases.GovCase(number="6")
        case.appeal_raw = "x"
        case.apply_intake({"state": cases.INTAKE_ACCEPTED})
        self.assertEqual(cases.primary_stage_kind(case), case.open_stage_kind())
        self.assertEqual(cases.stage_block_reason(case, cases.STAGE_INTAKE), "")

    def test_refusal_request_carries_basis_and_forbids_sanction(self):
        case = _refused_case()
        doc = gdata.PostanovlenieProkurora()
        doc.set("doc_type", "Об отказе в принятии обращения к производству")
        text = cases.refusal_intake_request(case, doc)
        self.assertIn("анонимно", text)              # основание отказа из проверки
        self.assertIn("НЕ назначай санкцию", text)
        self.assertIn("статьи 16", text)
        self.assertIn("ПОСТАНОВЛЕНИЕ", text.upper())

    def test_form_opens_primary_stage_for_refused_case(self):
        src = (_P("postanovlenie_prokuror_ui.py")).read_text(encoding="utf-8")
        self.assertIn("self._select_stage(cases.primary_stage_kind(case))", src)
        block = src[src.index("def _form("):]
        block = block[:block.index("def showEvent")]
        self.assertIn("cases.stage_block_reason(self.case, kind)", block)
        self.assertIn("cases.refusal_intake_request", block)
        self.assertNotIn('problems.append("Сначала примите обращение (этап 1)")', block)

    def test_png_stage_forced_to_primary_for_refused_case(self):
        src = (_P("main.py")).read_text(encoding="utf-8")
        block = src[src.index("def _apply_gov_answer"):]
        block = block[:block.index("def _open_prompts_folder")]
        self.assertIn("case.refused() and not case.accepted()", block)
        self.assertIn("stage_kind = pcases.STAGE_INTAKE", block)

    def test_request_forbids_sanction_on_refusal(self):
        src = (_P("main.py")).read_text(encoding="utf-8")
        self.assertIn("ЕСЛИ В ЗАЯВКЕ УКАЗАНО, ЧТО В ПРИНЯТИИ ОБРАЩЕНИЯ ОТКАЗАНО",
                      src)


if __name__ == "__main__":
    unittest.main()
