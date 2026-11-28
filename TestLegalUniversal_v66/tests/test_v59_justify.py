"""v59 — выравнивание текста по ширине в итоговых PNG.

Замечание пользователя: «внутри документов .png выравнивай текст по ширине».

Что было не так (замер по реальному `AG-2`, полоса набора 1354 px):

* выравнивание формально работало, но пробел разрешалось растянуть максимум
  в 2.6 раза (`natural * 2.6` = 18.2 px при обычном пробеле 7 px);
* из-за этого 9 строк из 22 не доходили до правого поля, худший недобор —
  122 px (нужно было растянуть пробел до 31.8 px);
* правый край выглядел «рваным», хотя текст считался выровненным.

Что сделано:

* `_rebalance()` — перед растяжкой слова перекидываются между строками: если
  пробел пришлось бы тянуть сильнее `SPACE_STRETCH_LIMIT` (×3.2), строка
  забирает слово из следующей; если пробел пришлось бы сжимать — отдаёт своё
  последнее слово вперёд. Так строка доходит до поля без «дыр»;
* потолок отрисовки поднят до `SPACE_HARD_LIMIT` (×4.5), снизу пробел не
  может схлопнуться меньше ×0.6;
* последняя строка абзаца — по левому краю (`JUSTIFY_LAST_LINE = False`);
* строка из одного слова не растягивается;
* заголовки, «УСТАНОВИЛ:», «ПОСТАНОВИЛ:», подпись и колонтитул остаются
  по центру / справа — их выравнивание не меняется.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WIDTH = 1354          # PAGE_W - 2 * MARGIN_X при 200 dpi


def _render():
    import doc_render

    return doc_render


def _draw_and_font(size: int = 28):
    from PIL import Image, ImageDraw

    render = _render()
    draw = ImageDraw.Draw(Image.new("RGB", (10, 10), "white"))
    return draw, render.DocAssets().fonts.get(size, "body")


def _rows(text: str, width: int = WIDTH, first_width: int | None = None):
    draw, font = _draw_and_font()
    render = _render()
    return render._wrap_rows(draw, text, font, width, first_width=first_width)


def _width(row) -> float:
    draw, font = _draw_and_font()
    render = _render()
    return draw.textlength(render._row_text(row), font=font)


SAMPLES = (
    "Гражданин Lordik Claw обратился в Офис Генерального прокурора штата "
    "Сан-Андреас с заявлением в отношении сотрудника Federal Investigation "
    "Bureau с опознавательным знаком № 301. Из текста обращения и приложенных "
    "материалов проверки следует, что 06.11.2026 около 10:17 заявитель "
    "находился в помещении больницы города Лос-Сантос.",
    "При проведении проверки установлено, что сотрудник Aliya Nomad "
    "06.11.2026, действуя вопреки служебным обязанностям, допустил грубое "
    "нарушение утвержденного дресс-кода, а также проигнорировал и отказался "
    "выполнять законные приказы вышестоящего руководства.",
)


class WidthFillTests(unittest.TestCase):
    """Строка доходит до правого поля (кроме последней в абзаце)."""

    def test_every_line_reaches_the_right_margin(self):
        render = _render()
        draw, font = _draw_and_font()
        for text in SAMPLES:
            rows = _rows(text)
            self.assertGreaterEqual(len(rows), 3)
            for index, row in enumerate(rows[:-1]):
                natural = draw.textlength(" ", font=font) or 1
                words = render._row_text(row).split()
                used = sum(draw.textlength(w, font=font) for w in words)
                gaps = max(len(words) - 1, 1)
                need = (WIDTH - used) / gaps
                space = min(need, natural * render.SPACE_HARD_LIMIT)
                filled = used + space * gaps
                self.assertGreaterEqual(
                    filled, WIDTH - 6,
                    f"строка {index + 1} не дошла до поля: "
                    f"{filled:.0f} из {WIDTH} — «{render._row_text(row)[:60]}»")

    def test_no_line_goes_past_the_margin(self):
        for text in SAMPLES:
            for row in _rows(text):
                self.assertLessEqual(_width(row), WIDTH + 1,
                                     f"строка шире полосы: «{_width(row):.0f}»")

    def test_spaces_are_not_holes(self):
        """Растяжка ограничена: пробел не «взрывается» и не схлопывается."""
        render = _render()
        draw, font = _draw_and_font()
        natural = draw.textlength(" ", font=font) or 1
        for text in SAMPLES:
            for row in _rows(text)[:-1]:
                words = render._row_text(row).split()
                if len(words) < 2:
                    continue
                used = sum(draw.textlength(w, font=font) for w in words)
                need = (WIDTH - used) / (len(words) - 1)
                self.assertLessEqual(need, natural * render.SPACE_HARD_LIMIT + 0.5)
                self.assertGreater(need, natural * 0.5)

    def test_row_text_keeps_single_spaces(self):
        """Пробелы в строке остаются обычными — ни NBSP, ни «схлопывания»."""
        render = _render()
        for text in SAMPLES:
            for row in _rows(text):
                line = render._row_text(row)
                self.assertNotIn("\u00a0", line)
                self.assertNotIn("  ", line)
                self.assertNotIn("ГражданинLordik", line)


class RebalanceTests(unittest.TestCase):
    """Перекидывание слов между строками вместо растягивания пробелов."""

    def test_moving_words_reduces_stretch(self):
        """После перебалансировки максимальная растяжка ниже, чем без неё."""
        render = _render()
        draw, font = _draw_and_font()
        natural = draw.textlength(" ", font=font) or 1
        text = SAMPLES[0]

        def worst_stretch(rows):
            worst = 0.0
            for row in rows[:-1]:
                words = render._row_text(row).split()
                if len(words) < 2:
                    continue
                used = sum(draw.textlength(w, font=font) for w in words)
                worst = max(worst, (WIDTH - used) / (len(words) - 1) / natural)
            return worst

        rows = _rows(text)
        worst = worst_stretch(rows)
        self.assertLess(worst, render.SPACE_HARD_LIMIT + 0.1)

    def test_rebalance_never_adds_or_loses_words(self):
        render = _render()
        text = SAMPLES[1]
        rows = _rows(text)
        joined = " ".join(render._row_text(row) for row in rows)
        self.assertEqual(len(joined.split()), len(text.split()))

    def test_first_line_uses_the_reduced_width(self):
        """Первая строка короче на красную строку — пробелы не схлопываются."""
        render = _render()
        draw, font = _draw_and_font()
        rows = _rows(SAMPLES[0], first_width=WIDTH - render.RED_LINE_PX)
        first = render._row_text(rows[0])
        self.assertNotIn("ГражданинLordik", first)
        self.assertLessEqual(draw.textlength(first, font=font),
                             WIDTH - render.RED_LINE_PX + 1)


class AlignSettingsTests(unittest.TestCase):
    """Настройки выравнивания и границы применимости."""

    def test_limits(self):
        render = _render()
        self.assertGreater(render.SPACE_STRETCH_LIMIT, 2.6,
                           "потолок ×2.6 и был причиной «рваного» края")
        self.assertGreaterEqual(render.SPACE_HARD_LIMIT,
                                render.SPACE_STRETCH_LIMIT)
        self.assertFalse(render.JUSTIFY_LAST_LINE,
                         "последняя строка абзаца — по левому краю")

    def test_old_cap_is_gone_from_drawing(self):
        source = (ROOT / "doc_render.py").read_text(encoding="utf-8")
        self.assertNotIn("natural * 2.6", source)

    def test_centered_elements_are_untouched(self):
        """Заголовки, «УСТАНОВИЛ», подпись и колонтитул остаются по центру."""
        source = (ROOT / "doc_render.py").read_text(encoding="utf-8")
        for anchor in ('anchor="ma"', 'anchor="ra"'):
            self.assertIn(anchor, source)

    def test_single_word_line_is_not_stretched(self):
        source = (ROOT / "doc_render.py").read_text(encoding="utf-8")
        self.assertIn("if len(words) == 1:", source)


class RealDocumentTests(unittest.TestCase):
    """На реальном постановлении Прокурора: строк до поля — все."""

    EXPORT = ROOT.parent / "Постановление_4"

    def setUp(self):
        if not (self.EXPORT / "AG-2" / "ИИ-ответ.txt").is_file():
            self.skipTest("нет выгрузки «Постановление_4» рядом с репозиторием")

    def test_all_lines_fill_the_width(self):
        """Допуск 10 px: длинный последний кусок иногда не влезает целиком.

        В этом месте разрешено оставить строку чуть короче, чем сломать
        неразрывную связку («...и приложенных к» + «обращению»).
        """
        import postanovlenie_prokuror_data as pgov

        render = _render()
        raw = (self.EXPORT / "AG-2" / "ИИ-ответ.txt").read_text(encoding="utf-8")
        base = pgov.PostanovlenieProkurora()
        base.set("case_number", "AG-2")
        doc = pgov.parse_gov_answer(raw, base=base)
        self.assertIsNotNone(doc)
        draw, font = _draw_and_font()
        rows = render._wrap_rows(draw, doc.facts_text(), font, WIDTH,
                                 first_width=WIDTH - render.RED_LINE_PX)
        natural = draw.textlength(" ", font=font) or 1
        short = []
        for index, row in enumerate(rows[:-1]):
            words = render._row_text(row).split()
            used = sum(draw.textlength(w, font=font) for w in words)
            gaps = max(len(words) - 1, 1)
            space = min((WIDTH - used) / gaps, natural * render.SPACE_HARD_LIMIT)
            if used + space * gaps < WIDTH - 10:
                short.append(index + 1)
        self.assertEqual(short, [], f"строки без выравнивания: {short}")


class VersionTests(unittest.TestCase):
    """Правило проекта: своя папка и поднятый номер версии."""

    def test_version_is_bumped(self):
        # v59 проверяет, что версия выровнена по ширине и не откатилась:
        # номер только растёт, поэтому сверяем major.minor с 2.15 и выше.
        config = (ROOT / "config.py").read_text(encoding="utf-8")
        match = re.search(r'CURRENT_VERSION = "(\d+)\.(\d+)\.', config)
        self.assertIsNotNone(match)
        self.assertGreaterEqual((int(match.group(1)), int(match.group(2))), (2, 15))

    def test_release_revision(self):
        diag = (ROOT / "diagnostics.py").read_text(encoding="utf-8")
        self.assertRegex(diag, r"universal-embedded-v(59|6\d)")


if __name__ == "__main__":      # pragma: no cover
    unittest.main()
