"""v54 — правки по выгрузке «Постановление 54 версия».

1. **Папка IAD.** Замечание: «Имеется папка „Обращение №“ под постановления
   прокурора, нужно сделать аналогично, но теперь под IAD, под названием
   „Постановление №“, и внутри уже будут, например, „LSCSD-IAD-1“». Теперь
   документ IAD лежит в `Постановление №1/LSCSD-IAD-1/`.

2. **`[cite: 2]` и чат-хвост.** В выгрузке AG-1 «УСТАНОВИЛ» заканчивался
   пометками сервиса и абзацем интерфейса: «… [cite: 2]», «Прокурор штата
   Сан-Андреас Jonathan Raine Я могу продолжить работу по этому делу …
   ИИ может ошибаться. Проверяйте его ответы. Ваш файл Доступно только вам …
   Page 18 … Показать все».

3. **Предложения помощи.** В выгрузке AG-2 четвёртый пункт «ПОСТАНОВИЛ» был
   репликой ИИ: «Jonathan Raine Я могу составить для вас исковое заявление в
   суд или официальное обращение по данному инциденту…».

4. **IC-термины.** В выгрузке AG-2 в «УСТАНОВИЛ» печаталось «0:40 Дм без
   причины» и «Установлено, что LSPD удо не видел…», в IAD — внутренний блок
   «ПОСТАНОВЛЕНИЕ INTERNAL AFFAIRS DIVISION / УСТАНОВИЛ:».

5. **Форма IAD** — без «Предпросмотра» и «Сохранить без отправки».
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import postanovlenie_data as pdoc
import postanovlenie_prokuror_data as pgov

ROOT = Path(__file__).resolve().parents[1]
EXPORT = ROOT.parent / "Постановление 54 версия"
PROMPT = ROOT.parent / "Для новой версии" / "Текущий промт" / "promt 1.30.txt"

AG1 = EXPORT / "Обращение №1" / "AG-1" / "ИИ-ответ.txt"
AG2 = EXPORT / "Обращение №1" / "AG-2" / "ИИ-ответ.txt"
IAD = EXPORT / "LSCSD-IAD-1" / "ИИ-ответ.txt"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _gov_answer(path: Path, number: str):
    return pgov.parse_gov_answer(_read(path),
                                 base=pgov.PostanovlenieProkurora(doc_number=number))


class V54CitationsTests(unittest.TestCase):
    """Пометки сервиса «[cite: N]» в документ не попадают."""

    def test_citation_is_removed(self):
        self.assertEqual(pdoc.strip_citations("обжалуются [cite: 2]."),
                         "обжалуются.")
        self.assertEqual(pdoc.strip_citations("производству [cite: 2, 3]."),
                         "производству.")
        self.assertEqual(pdoc.strip_citations("норма [cite:2]."), "норма.")

    def test_text_without_citations_is_untouched(self):
        text = "В соответствии со статьёй 8 Закона «О деятельности офиса …»"
        self.assertEqual(pdoc.strip_citations(text), text)

    def test_has_citations(self):
        self.assertTrue(pdoc.has_citations("текст [cite: 3]"))
        self.assertFalse(pdoc.has_citations("текст без пометок"))

    def test_real_ag1_answer_has_no_citations(self):
        doc = _gov_answer(AG1, "AG-1")
        for field in (doc.facts_text(), doc.get("established"),
                      doc.get("situation"), " ".join(doc.points)):
            self.assertNotIn("cite", field.lower())


class V54ChatTailTests(unittest.TestCase):
    """Чат-хвост после «===КОНЕЦ ОТВЕТА===» и строки интерфейса."""

    def test_tail_is_cut(self):
        text = ("УСТАНОВИЛ:\nФакт установлен.\n===КОНЕЦ ОТВЕТА===\n"
                "Я могу продолжить работу по этому делу.\n"
                "Ваш файл\n·\nДоступно только вам\nPage 18\nПоказать все\n")
        kept = pdoc.cut_chat_tail(text)
        self.assertIn("Факт установлен.", kept)
        self.assertNotIn("Я могу продолжить", kept)
        self.assertNotIn("Page 18", kept)
        self.assertNotIn("Показать все", kept)

    def test_ui_words_inside_a_line_are_removed(self):
        text = "Доступно только вам Page 18 Ваш файл Доступно только вам Показать все."
        cleaned = pdoc.strip_ui_noise_inside(text)
        self.assertNotIn("Page 18", cleaned)
        self.assertNotIn("Показать все", cleaned)
        self.assertNotIn("Ваш файл", cleaned)

    def test_real_ag1_established_has_no_chat_tail(self):
        doc = _gov_answer(AG1, "AG-1")
        facts = doc.facts_text()
        for junk in ("Ваш файл", "Доступно только вам", "Page 18",
                     "Показать все", "Я могу продолжить", "ИИ может ошибаться",
                     "Что именно мы делаем на следующем шаге"):
            self.assertNotIn(junk, facts)


class V54ChatOffersTests(unittest.TestCase):
    """Предложения помощи и вопросы к пользователю."""

    def test_offer_is_removed(self):
        text = ("Jonathan Raine Я могу составить для вас исковое заявление в суд "
                "или официальное обращение по данному инциденту. Напишите, если "
                "вам требуется шаблон документа или дополнительная консультация "
                "по статьям законов. ИИ может ошибаться. Проверяйте его ответы.")
        self.assertEqual(pdoc.strip_chat_offers(text).strip(), "")

    def test_normal_decision_is_kept(self):
        text = "Настоящее постановление вступает в законную силу с момента подписания."
        self.assertEqual(pdoc.strip_chat_offers(text), text)

    def test_real_ag2_resolution_has_no_offer(self):
        doc = _gov_answer(AG2, "AG-2")
        joined = " ".join(doc.points).lower()
        self.assertNotIn("я могу составить", joined)
        self.assertNotIn("напишите, если", joined)
        self.assertNotIn("ии может ошибаться", joined)
        self.assertEqual(len(doc.points), 3)

    def test_real_iad_resolution_stops_at_the_last_decision(self):
        doc = pdoc.parse_ai_answer(_read(IAD))
        joined = " ".join(doc.points).lower()
        self.assertNotIn("я могу подготовить", joined)
        self.assertNotIn("какой документ мы сформируем", joined)


class V54RawObservationTests(unittest.TestCase):
    """Сырые наблюдения заявителя и внеигровые обороты."""

    EVIDENCE = ("https://youtu.be/N16KNtXF_88 ( от 0 сек до 13 сек еду просто "
                "видно мегафонов нету. 00:13 секунда пробивают колёса, 0:40 Дм "
                "без причины, 0:45 предъявили не правильную статью. 1:10 едем "
                "на сломанном авто. 1:33 миранда зачитана 1 раз.)")

    def test_observation_list_is_not_evidence(self):
        self.assertTrue(pgov.is_raw_observation(self.EVIDENCE))
        self.assertEqual(pgov.evidence_mention(self.EVIDENCE), "материалы обращения")

    def test_timecodes_of_the_prosecutor_are_kept(self):
        text = "00:14 — сотрудник надевает наручники; 00:22 — уводит заявителя."
        self.assertFalse(pgov.has_raw_observations(text))
        self.assertIn("00:14", pgov.strip_raw_observations(text))

    def test_real_ag2_facts_have_no_raw_notes(self):
        doc = _gov_answer(AG2, "AG-2")
        facts = doc.facts_text()
        for junk in ("Дм ", "без причины", "еду просто", "пробивают",
                     "миранда зачитана", "удо не видел"):
            self.assertNotIn(junk, facts)
        # время события и факты остаются
        self.assertIn("18:15", facts)
        self.assertIn("без законных на то оснований", facts)

    def test_ic_hyphenated_phrase_is_not_mangled(self):
        cleaned = pdoc.sanitize_ooc("без законных IC-требований")
        self.assertEqual(cleaned, "без законных требований")


class V54IadInnerHeadingTests(unittest.TestCase):
    """Внутренние заголовки бланка в значении поля (выгрузка IAD)."""

    def test_real_iad_situation_starts_with_facts(self):
        doc = pdoc.parse_ai_answer(_read(IAD))
        situation = doc.get("situation")
        self.assertTrue(situation.startswith("10 ноября 2026 года"), situation)
        self.assertNotIn("ПОСТАНОВЛЕНИЕ INTERNAL AFFAIRS DIVISION", situation)
        self.assertNotIn("УСТАНОВИЛ:", situation)
        self.assertNotIn("без причины", situation)
        self.assertIn("в отсутствие законных и фактических оснований", situation)

    def test_inner_headers_are_dropped(self):
        text = ("ПОСТАНОВЛЕНИЕ INTERNAL AFFAIRS DIVISION\nУСТАНОВИЛ:\n"
                "10 ноября 2026 года было инициировано производство.")
        cleaned = pdoc.strip_inner_headings(text)
        self.assertNotIn("INTERNAL AFFAIRS DIVISION", cleaned)
        self.assertNotIn("УСТАНОВИЛ", cleaned)
        self.assertIn("10 ноября 2026", cleaned)


class V54IadFolderTests(unittest.TestCase):
    """Папка «Постановление №N» вокруг документа IAD."""

    def test_number_is_taken_from_the_document(self):
        self.assertEqual(pdoc.iad_folder_name("LSCSD-IAD-1"), "Постановление №1")
        self.assertEqual(pdoc.iad_folder_name("LSCSD-IAD-14"), "Постановление №14")
        self.assertEqual(pdoc.iad_folder_name("LSPD-IAD-7"), "Постановление №7")

    def test_is_iad_number(self):
        self.assertTrue(pdoc.is_iad_number("LSCSD-IAD-1"))
        self.assertFalse(pdoc.is_iad_number("AG-1"))
        self.assertFalse(pdoc.is_iad_number(""))

    def test_document_folder_is_inside_the_iad_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = pdoc.doc_folder(tmp, "LSCSD-IAD-1")
            self.assertEqual(folder.name, "LSCSD-IAD-1")
            self.assertEqual(folder.parent.name, "Постановление №1")

    def test_save_ai_answer_writes_into_the_iad_folder(self):
        doc = pdoc.parse_ai_answer(_read(IAD))
        with tempfile.TemporaryDirectory() as tmp:
            written = pdoc.save_ai_answer(Path(tmp), doc, _read(IAD))
            names = sorted(p.name for p in written)
            self.assertEqual(names, ["ИИ-документ.txt", "ИИ-ответ.txt", "ИИ-поля.json"])
            self.assertEqual(written[0].parent.name, "LSCSD-IAD-1")
            self.assertEqual(written[0].parent.parent.name, "Постановление №1")

    def test_prosecutor_folders_stay_under_the_appeal(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = pdoc.doc_folder(tmp, "AG-1", "1")
            self.assertEqual(folder.parent.name, "Обращение №1")

    def test_main_prints_iad_png_into_the_document_folder(self):
        src = (ROOT / "main.py").read_text(encoding="utf-8")
        block = src[src.index("def _apply_iad_answer"):]
        block = block[:block.index("def _schedule_fresh_chat_after_answer")]
        self.assertIn("save_pages(pages, folder, stem)", block)
        self.assertNotIn("save_pages(pages, root, stem)", block)


class V54IadFormTests(unittest.TestCase):
    """Форма IAD: без «Предпросмотра» и «Сохранить без отправки»."""

    def setUp(self):
        self.src = (ROOT / "postanovlenie_ui.py").read_text(encoding="utf-8")

    def test_no_preview_tab(self):
        self.assertNotIn('"Предпросмотр"', self.src)
        self.assertNotIn("def _build_preview_tab", self.src)

    def test_no_save_without_sending(self):
        self.assertNotIn('"Сохранить без отправки"', self.src)
        self.assertNotIn("def _save_only", self.src)

    def test_no_preview_renderer(self):
        self.assertNotIn("def _make_preview", self.src)

    def test_form_still_sends_the_request(self):
        form = self.src[self.src.index("def _form(self)"):]
        form = form[:form.index("def mousePressEvent")]
        self.assertIn("to_chat_txt", form)
        self.assertIn("self.formed.emit", form)


class V54PromptTests(unittest.TestCase):
    """Промт 1.30: запреты на cite, реплики и сырые наблюдения."""

    def setUp(self):
        self.text = _read(PROMPT)

    def test_prompt_exists(self):
        self.assertTrue(PROMPT.is_file())

    def test_prompt_forbids_citations(self):
        self.assertIn("8.1.1.2", self.text)
        self.assertIn("[cite: 2]", self.text)
        self.assertIn("КАТЕГОРИЧЕСКИ запрещено писать", self.text)

    def test_prompt_forbids_offers(self):
        self.assertIn("8.1.1.3", self.text)
        self.assertIn("Я могу продолжить работу", self.text)
        self.assertIn("Я могу составить для вас исковое заявление", self.text)

    def test_prompt_forbids_raw_observations(self):
        self.assertIn("8.1.1.4", self.text)
        self.assertIn("Дм без причины", self.text)
        self.assertIn("8.1.1.5", self.text)

    def test_prompt_keeps_previous_rules(self):
        self.assertIn("8.1.1.1", self.text)       # никаких адресов (v53)
        self.assertIn("===ПРОВЕРКА ОБРАЩЕНИЯ===", self.text)
        self.assertNotIn("замечание v", self.text)
        self.assertNotIn("выгрузк", self.text)


if __name__ == "__main__":
    unittest.main()
