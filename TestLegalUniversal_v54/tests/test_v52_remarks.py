"""v52 — лишние строки бланка обращения в блоке «УСТАНОВИЛ».

Замечание пользователя по выгрузке `Постановление_52_для_версии`
(`AG-1_Постановление_Этап_1_принятие_Вид_принятии-обращения-к-собственному-
производству — стр.1.png`):

    УСТАНОВИЛ:
    Событие имело место: 09.11.2026 19:43. 5. Подробное описание ситуации:
    все описано в подробно и верно. 10 ноября 2026 года …

Нужное предложение («10 ноября 2026 года …») стоит третьим, а перед ним две
служебные строки, которых в документе быть не должно:

* «5. Подробное описание ситуации: …» — пункт анкеты обращения (и его
  значение «все описано подробно и верно» — не факт дела);
* «Событие имело место: <дата>» — реквизит шапки, вставленный в начало
  «УСТАНОВИЛ» нашим же кодом.

Точечное исправление: обе строки убраны, дата и место события в документе
остаются (дата — в шапке, место — внутри первого абзаца о событии).
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Ровно тот текст, что был в выгрузке: пункт 5 формы обращения с «ответом»
#: «все описано подробно и верно».
FORM_ITEM = ("5. Подробное описание ситуации: "
             "все описано в подробно и верно.")
STORY = ("10 ноября 2026 года в Офис Генерального прокурора штата "
         "Сан-Андреас поступило обращение гражданина Pravik Killa.")


def _doc(**kwargs):
    import postanovlenie_prokuror_data as g
    params = dict(
        doc_number="AG-1",
        doc_stage=g.GOV_STAGE_PRIMARY,
        doc_type="О принятии обращения к собственному производству",
        applicant_name="Pravik Killa",
        event_date="09.11.2026 19:43",
    )
    params.update(kwargs)
    return g.PostanovlenieProkurora(**params)


class V52FactsCleanupTests(unittest.TestCase):
    """Обе лишние строки уходят, нужный абзац остаётся первым."""

    def setUp(self):
        import postanovlenie_prokuror_data as g
        self.g = g

    def test_form_item_is_dropped_from_facts(self):
        facts = _doc(situation=FORM_ITEM, established=STORY).facts_text()
        self.assertNotIn("Подробное описание ситуации", facts)
        self.assertNotIn("все описано", facts)

    def test_event_line_is_dropped_from_facts(self):
        facts = _doc(situation=FORM_ITEM, established=STORY).facts_text()
        self.assertNotIn("Событие имело место", facts)

    def test_story_starts_the_block(self):
        facts = _doc(situation=FORM_ITEM, established=STORY).facts_text()
        self.assertTrue(facts.startswith("10 ноября 2026 года"), facts)

    def test_story_is_not_lost(self):
        facts = _doc(situation=FORM_ITEM, established=STORY).facts_text()
        self.assertIn("обращение гражданина Pravik Killa", facts)

    def test_form_item_is_dropped_from_established_too(self):
        """Строка анкеты могла попасть и в «УСТАНОВИЛ» от ИИ."""
        facts = _doc(established=f"{FORM_ITEM} {STORY}").facts_text()
        self.assertNotIn("Подробное описание ситуации", facts)
        self.assertIn("10 ноября 2026 года", facts)

    def test_form_item_is_dropped_from_situation_even_without_established(self):
        facts = _doc(situation=f"{FORM_ITEM}\n{STORY}").facts_text()
        self.assertNotIn("Подробное описание ситуации", facts)
        self.assertIn("10 ноября 2026 года", facts)

    def test_date_still_printed_in_the_header(self):
        """Дата события не потеряна — она печатается в шапке бланка."""
        doc = _doc(situation=FORM_ITEM, established=STORY)
        self.assertEqual(doc.get("event_date"), "09.11.2026 19:43")

    def test_place_is_kept_inside_the_first_paragraph(self):
        facts = _doc(situation=FORM_ITEM, established=STORY,
                     event_place="зал заседаний Сената Капитолия").facts_text()
        self.assertIn("Сената Капитолия", facts)
        self.assertNotIn("Событие имело место", facts)
        self.assertNotIn("Место события:", facts)

    def test_ordinary_facts_are_untouched(self):
        text = "Сотрудник мешал проведению заседания Сената."
        facts = _doc(established=text).facts_text()
        self.assertIn(text, facts)


class V52StripperTests(unittest.TestCase):
    """Сам вырезатель строк-ярлыков."""

    def setUp(self):
        import postanovlenie_prokuror_data as g
        self.g = g

    def test_labelled_form_items_are_removed(self):
        raw = ("1. Данные о заявителе: Pravik Killa\n"
               "5. Подробное описание ситуации: все описано в подробно и верно.\n"
               "10 ноября 2026 года поступило обращение.")
        for label in ("Данные о заявителе", "Подробное описание ситуации"):
            self.assertNotIn(label, self.g.strip_appeal_form_items(raw))
        self.assertIn("10 ноября 2026 года", self.g.strip_appeal_form_items(raw))

    def test_sentence_inside_the_story_is_kept(self):
        """Упоминание «предмет обращения» в середине фразы — не ярлык."""
        text = ("Предмет обращения соответствует требованиям статьи 5 "
                "Регламента подачи обращений.")
        self.assertEqual(self.g.strip_appeal_form_items(text), text)

    def test_empty_text(self):
        self.assertEqual(self.g.strip_appeal_form_items(""), "")


class V52RealExportTests(unittest.TestCase):
    """Выгрузка `Постановление_52_для_версии` — ровно тот документ из замечания."""

    ROOT52 = ROOT.parent / "Постановление_52_для_версии"

    def _fields(self, stage_dir):
        import json
        path = self.ROOT52 / "AG-1" / stage_dir / "ИИ-поля.json"
        if not path.exists():
            self.skipTest("выгрузка отсутствует")
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("поля", data)

    def test_export_ag1_intake(self):
        import postanovlenie_prokuror_data as g
        doc = g.PostanovlenieProkurora.from_dict(self._fields("этап-1 — принятие"))
        facts = doc.facts_text()
        self.assertNotIn("Событие имело место", facts)
        self.assertNotIn("Подробное описание ситуации", facts)
        self.assertTrue(facts.startswith("10 ноября 2026 года"), facts[:120])
        # дата/место события не потеряны — они в реквизитах документа
        self.assertEqual(doc.get("event_date"), "09.11.2026 19:43")
        self.assertTrue(doc.get("event_place"))

    def test_export_ag1_sanction_stage(self):
        import postanovlenie_prokuror_data as g
        doc = g.PostanovlenieProkurora.from_dict(self._fields("этап-2 — санкция"))
        facts = doc.facts_text()
        self.assertNotIn("Событие имело место", facts)
        self.assertNotIn("Подробное описание ситуации", facts)
        self.assertNotIn("все описано", facts)


class V52NoRegressionInFixturesTests(unittest.TestCase):
    """Эталоны AG-1/AG-2 рендерятся без новых замечаний."""

    def test_reference_answers_have_no_form_items(self):
        import json
        import postanovlenie_prokuror_data as g
        root = ROOT.parent / "Постановление"
        pairs = (
            (root / "AG-1/ИИ-ответ.txt",
             root / "AG-1/этап-1 — принятие/ИИ-поля.json"),
            (root / "AG-2/этап-1 — принятие/ИИ-документ.txt",
             root / "AG-2/этап-1 — принятие/ИИ-поля.json"),
        )
        for answer_path, fields_path in pairs:
            if not answer_path.exists() or not fields_path.exists():
                self.skipTest("эталоны отсутствуют")
            base = g.PostanovlenieProkurora.from_dict(
                json.loads(fields_path.read_text(encoding="utf-8")))
            doc = g.parse_gov_answer(
                answer_path.read_text(encoding="utf-8"), base=base)
            self.assertIsNotNone(doc, answer_path.name)
            facts = doc.facts_text()
            self.assertNotIn("Событие имело место", facts)
            self.assertNotIn("Подробное описание ситуации", facts)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
