"""v57 — русская типографика итоговых PNG по «Рекомендация.txt».

Рекомендация (файл `Рекомендация.txt` в корне репозитория) требует:

1. **Неразрывные связки.** Предлоги и союзы из 1–2 букв («в», «и», «по»…),
   частицы «не»/«ни», инициалы, сокращения («г.», «стр.», «рис.», «№», «§»,
   «т.», «д.», «к.») и числа не должны оставаться в конце строки в одиночестве.
   Частицы «бы/б/ли/ль/же/ж», тире и «и т. д.» наоборот не отрываются от
   ПРЕДЫДУЩЕГО слова.

2. **Красная строка.** Отступ 1,25 см, только в начале абзаца. Не ставится в
   первом абзаце после заголовка, в центрированном тексте, в списках. Отступ
   и пустая строка между абзацами одновременно — ошибка.

3. **Выравнивание по ширине** сохраняется, между абзацами нет «пустых строк».

Проверки делятся на три группы: чистая типографика (`_wrap_rows`), настройки
документа (варианты, размеры) и реальный рендер обоих бланков, если рядом
лежит выгрузка пользователя.
"""

from __future__ import annotations

import math
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

needs_export = unittest.skipUnless(
    (ROOT.parent / "Постановление").is_dir() or (ROOT.parent / "Постановление_4").is_dir(),
    "нет выгрузки пользователя рядом с репозиторием")


def _render():
    import doc_render

    return doc_render


def _draw_and_font(size: int = 28):
    from PIL import Image, ImageDraw

    render = _render()
    image = Image.new("RGB", (10, 10), "white")
    draw = ImageDraw.Draw(image)
    return draw, render.DocAssets().fonts.get(size, "body")


def _lines(text: str, width: int = 1300, first_width: int | None = None) -> list:
    draw, font = _draw_and_font()
    render = _render()
    rows = render._wrap_rows(draw, text, font, width, first_width=first_width)
    return [render._row_text(row) for row in rows]


# --------------------------------------------------------------------------- #
#  1. Связки: что не должно висеть в конце строки
# --------------------------------------------------------------------------- #
class ShortWordsGlueTests(unittest.TestCase):
    """Предлоги/союзы/частицы уходят на новую строку вместе со словом."""

    PREPOSITIONS = ["в", "к", "о", "с", "у", "во", "до", "за", "из", "ко",
                    "на", "об", "от", "по", "со"]
    CONJUNCTIONS = ["а", "и", "но", "да", "ни", "то", "же", "бы"]

    def test_preposition_is_never_the_last_word_of_a_line(self):
        text = ("Обстоятельства дела требуют проверки в отношении сотрудника в "
                "помещении больницы на основании заявления от гражданина и "
                "приложенных к обращению материалов по существу вопроса")
        for width in (700, 900, 1100, 1300):
            for line in _lines(text, width=width):
                last = line.split()[-1].strip(",;:.").lower()
                self.assertNotIn(
                    last, self.PREPOSITIONS + self.CONJUNCTIONS,
                    f"строка «{line}» заканчивается служебным словом «{last}»")

    def test_particle_ne_stays_with_the_next_word(self):
        lines = _lines("Сотрудник не пришел на службу и не выполнил приказ "
                       "руководства, о чем был составлен рапорт", width=760)
        for line in lines:
            self.assertNotEqual(line.split()[-1].strip(",;:").lower(), "не",
                                f"частица «не» осталась в конце строки: «{line}»")

    def test_every_binding_is_inside_one_chunk(self):
        render = _render()
        chunks = render._bind_chunks("пойти в кино и ночь дежурства по ст. 12 Устава")
        texts = [render._chunk_text(chunk) for chunk in chunks]
        self.assertIn("в кино", texts)
        self.assertIn("и ночь", texts)
        # цепочка «по ст. 12» — единый кусок, а не три отдельных слова
        self.assertTrue(any(t.startswith("по ст. 12") for t in texts), texts)


class AbbreviationGlueTests(unittest.TestCase):
    """Сокращения, инициалы и числа не отрываются от того, к чему относятся."""

    def test_number_sign_goes_with_the_number(self):
        for width in (620, 780, 950, 1200):
            for line in _lines("Сотрудник Federal Investigation Bureau с "
                               "опознавательным знаком № 301 находился на месте "
                               "происшествия при исполнении обязанностей", width=width):
                self.assertNotEqual(line.split()[-1], "№",
                                    f"«№» остался в конце строки: «{line}»")

    def test_initials_go_with_the_surname(self):
        lines = _lines("Заявление подписал А. С. Пушкин, о чем свидетельствует "
                       "отметка в журнале регистрации обращений граждан", width=700)
        for line in lines:
            last = line.split()[-1]
            self.assertFalse(last.strip(",;:").rstrip(".").isalpha() and
                             len(last.strip(",;:").rstrip(".")) <= 2 and
                             last.endswith("."),
                             f"инициалы остались в конце строки: «{line}»")

    def test_abbreviation_goes_with_the_next_word(self):
        lines = _lines("Согласно стр. 15 и рис. 3 приложения к обращению, "
                       "а также г. Лос-Сантос указан как место события", width=680)
        for line in lines:
            last = line.split()[-1].lower().strip(",;:")
            self.assertNotIn(last, {"стр.", "рис.", "г.", "т.", "д.", "к."},
                             f"сокращение осталось в конце строки: «{line}»")

    def test_number_goes_with_the_unit_or_word(self):
        lines = _lines("Назначить наказание в виде штрафа 20 000 $ в казну "
                       "штата и 50 % от суммы ущерба, 2026 г. признать отчетным",
                       width=640)
        for line in lines:
            last = line.split()[-1]
            self.assertFalse(last.replace(" ", "").isdigit() and not last.endswith("."),
                             f"число осталось в конце строки: «{line}»")


class LeftGlueTests(unittest.TestCase):
    """То, что привязывается к ПРЕДЫДУЩЕМУ слову."""

    def test_postfix_particles_stay_with_their_word(self):
        lines = _lines("Он сделал бы это, вряд ли кто-то сомневался, все же "
                       "факт установлен и подтвержден материалами дела", width=620)
        for line in lines:
            first = line.split()[0].lower().strip(",;:")
            self.assertNotIn(first, {"бы", "б", "ли", "ль", "же", "ж"},
                             f"частица начала строку: «{line}»")

    def test_line_never_starts_with_a_dash(self):
        lines = _lines("Москва — столица России. Событие произошло в больнице — "
                       "там же находился сотрудник — и было записано на видео",
                       width=640)
        for line in lines:
            self.assertFalse(line.startswith("—") or line.startswith("–"),
                             f"строка начинается с тире: «{line}»")

    def test_tail_of_enumeration_is_one_chunk(self):
        render = _render()
        chunks = [render._chunk_text(c)
                  for c in render._bind_chunks("материалы, документы и т. д. приложены")]
        self.assertIn("и т. д.", chunks)
        # хвост перечисления не притягивает следующее слово
        self.assertNotIn("и т. д. приложены", chunks)


# --------------------------------------------------------------------------- #
#  2. Настройки и красная строка
# --------------------------------------------------------------------------- #
class RedLineTests(unittest.TestCase):
    """Красная строка 1,25 см печатается только в начале абзаца."""

    def test_red_line_is_125_cm(self):
        render = _render()
        self.assertEqual(render.DPI, 200)
        expected = round(1.25 / 2.54 * 200)
        self.assertAlmostEqual(render.RED_LINE_PX, expected, delta=1)
        self.assertAlmostEqual(render.RED_LINE_PX / 200 * 2.54, 1.25, places=1)

    def test_selected_variants(self):
        """v57: отступ у ВСЕХ абзацев (решение пользователя), связки строгие.

        Сначала был выбран вариант «gost» (первый абзац после заголовка без
        отступа — как в «Рекомендация.txt»). Затем пользователь выбрал
        «Вариант 2 „all“ — отступ у ВСЕХ абзацев».
        """
        render = _render()
        self.assertEqual(render.RED_LINE_VARIANT, "all")
        self.assertEqual(render.BIND_VARIANT, "always")

    def test_first_line_of_a_paragraph_is_shorter(self):
        draw, font = _draw_and_font()
        render = _render()
        text = ("При проведении проверки установлено, что сотрудник допустил "
                "нарушение утвержденного дресс-кода и отказался выполнять "
                "законные приказы вышестоящего руководства")
        plain = render._wrap_rows(draw, text, font, 1300)
        shifted = render._wrap_rows(draw, text, font, 1300, first_width=1300 - 98)
        self.assertNotEqual(render._row_text(plain[0]), render._row_text(shifted[0]))
        width_plain = draw.textlength(render._row_text(plain[0]), font=font)
        width_shifted = draw.textlength(render._row_text(shifted[0]), font=font)
        self.assertLess(width_shifted, width_plain)
        self.assertLessEqual(width_shifted, 1300 - 98)

    def test_rows_fill_the_width_without_holes(self):
        """Ни одна строка, кроме последней, не «жидкая» (нет огромных дыр)."""
        render = _render()
        draw, font = _draw_and_font()
        text = ("Гражданин обратился в Офис Генерального прокурора штата "
                "Сан-Андреас с заявлением в отношении сотрудника Federal "
                "Investigation Bureau с опознавательным знаком № 301")
        rows = render._wrap_rows(draw, text, font, 1354)
        for row in rows[:-1]:
            width = draw.textlength(render._row_text(row), font=font)
            self.assertLessEqual(width, 1354 + 1)
            self.assertGreater(width, 1354 * 0.6,
                               f"слишком короткая строка: «{render._row_text(row)}»")


class ParagraphSpacingTests(unittest.TestCase):
    """Отступ и пустая строка между абзацами одновременно не применяются."""

    def test_gost_mode_has_no_extra_blank_line(self):
        source = (ROOT / "doc_render.py").read_text(encoding="utf-8")
        block = source[source.index("    def para("):]
        block = block[:block.index("    # Вводная часть")]
        self.assertIn('RED_LINE_VARIANT == "with_gaps"', block,
                      "отбивка между абзацами должна зависеть от варианта")

    def test_heading_exception_only_for_gost(self):
        """Исключение «без отступа после заголовка» действует только в «gost».

        В выбранном варианте «all» вводная часть и первый абзац «УСТАНОВИЛ»
        печатаются С отступом: `red_line` не передан явно (значит `None` →
        вариант `all` → отступ).
        """
        source = (ROOT / "doc_render.py").read_text(encoding="utf-8")
        self.assertIn('para(doc.preamble(), gap=22, red_line=RED_LINE_VARIANT != "gost")',
                      source)
        self.assertIn('para(facts, gap=20, red_line=RED_LINE_VARIANT != "gost")', source)

    def test_all_mode_indents_every_paragraph(self):
        render = _render()
        self.assertIn(render.RED_LINE_VARIANT, ("all", "with_gaps"),
                      "выбран режим, где отступ есть у всех абзацев")


# --------------------------------------------------------------------------- #
#  3. Реальный рендер обоих бланков
# --------------------------------------------------------------------------- #
@needs_export
class RealDocumentTests(unittest.TestCase):
    """Итоговые PNG обоих бланков: связки и красная строка на живой выгрузке."""

    SHORT = {"в", "к", "о", "с", "у", "а", "и", "во", "до", "за", "из", "ко",
             "на", "об", "от", "по", "со", "но", "да", "ни", "то", "же", "бы",
             "не", "№", "г.", "стр.", "рис.", "п.", "т.", "д.", "к.", "§"}

    def _doc(self):
        import postanovlenie_prokuror_data as pgov

        base = pgov.PostanovlenieProkurora()
        base.set("case_number", "AG-2")
        base.set("doc_number", "AG-2")
        raw = ("Я, Прокурор штата Сан-Андреас Marcus Nomad, удостоверение № 85028, "
               "рассмотрев обращение гражданина Lordik Claw от 06.11.2026.\n\n"
               "УСТАНОВИЛ:\n\nГражданин Lordik Claw обратился в Офис Генерального "
               "прокурора штата Сан-Андреас с заявлением в отношении сотрудника "
               "Federal Investigation Bureau с опознавательным знаком № 301. Из "
               "текста обращения следует, что 06.11.2026 около 10:17 заявитель "
               "находился в помещении больницы города Лос-Сантос и подвергся "
               "нападению.\n\nПОСТАНОВИЛ:\n\n1. Признать сотрудника виновным в "
               "нарушении пункта 2.5 Главы II Устава FIB.\n\n===КОНЕЦ ОТВЕТА===")
        return pgov.parse_gov_answer(raw, base=base)

    def test_rendered_pages_have_no_dangling_short_words(self):
        render = _render()
        doc = self._doc()
        if doc is None:                     # pragma: no cover
            self.skipTest("документ не собрался из тестового ответа")
        pages = render.render_document(doc, render.DocAssets())
        self.assertTrue(pages)
        # Проверяем не пиксели, а текст, который рендер реально раскладывал.
        text = doc.facts_text()
        draw, font = _draw_and_font()
        rows = render._wrap_rows(draw, text, font, 1354, first_width=1354 - render.RED_LINE_PX)
        for row in rows:
            words = render._row_text(row).split()
            self.assertNotIn(words[-1].lower().strip(",;:"), self.SHORT,
                             f"висячее слово в строке: «{render._row_text(row)}»")


class VersionTests(unittest.TestCase):
    """Версия выпуска поднята (правило проекта: своя папка на версию)."""

    def test_version_is_bumped(self):
        config = (ROOT / "config.py").read_text(encoding="utf-8")
        self.assertIn('CURRENT_VERSION = "2.13.0"', config)

    def test_release_revision(self):
        diag = (ROOT / "diagnostics.py").read_text(encoding="utf-8")
        self.assertIn("universal-embedded-v57", diag)


if __name__ == "__main__":                 # pragma: no cover
    unittest.main()
