"""doc_render.py — постановление в PNG формата A4 (v27).

Что здесь важно (и почему именно так):

* **A4 при 200 dpi** = 1654×2339 px. Это «полноценный документ», который
  можно распечатать, а не картинка «на глазок».
* **Шапка**: герб по центру, дата и номер справа, название ведомства и
  подразделения по центру, заголовок документа — как в примерах IAD.
* **Водяной знак** — очень бледный герб по центру текста: он не мешает
  читать и печатать документ.
* **Многостраничность**: текст постановляющей части разбивается на страницы
  по реальной высоте строк; на каждой странице повторяется колонтитул
  «стр. N из M». Подписи и печать — только на последней странице.
* **Реквизиты внизу**: печать слева, подпись (инициал + фамилия «красивым»
  шрифтом) и должность справа.

Шрифты: кириллица гарантированно есть в DejaVu; для подписи используется
любой найденный курсивный/рукописный шрифт, иначе — крупный наклонный DejaVu.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

#: A4 при 200 dpi.
DPI = 200
PAGE_W = int(8.27 * DPI)   # 1654
PAGE_H = int(11.69 * DPI)  # 2339

MARGIN_X = 150
MARGIN_TOP = 120
MARGIN_BOTTOM = 150

INK = (24, 26, 32)
MUTED = (90, 96, 108)
LINE = (170, 176, 188)
ACCENT = (18, 38, 84)

CANCEL = ImageFont.Layout.RAQM if hasattr(ImageFont, "Layout") else None

#: Кандидаты рукописных шрифтов (если пользователь положит свой в data/fonts).
SCRIPT_FONT_NAMES = (
    # Порядок = предпочтение. Caveat — самый читаемый «рукописный» шрифт с
    # полной кириллицей; Marck Script — классическая пропись.
    "Caveat-Regular.ttf", "Caveat.ttf", "MarckScript-Regular.ttf",
    "GreatVibes-Regular.ttf", "GreatVibes.ttf", "BadScript-Regular.ttf",
    "Pacifico-Regular.ttf",
)

#: PT Serif — профессиональная гарнитура с кириллицей (ParaType, OFL).
#: Именно она даёт «официальный документ», а не «текст из блокнота».
SERIF_REGULAR = ("PTSerif-Regular.ttf", "DejaVuSerif.ttf", "times.ttf")
SERIF_BOLD = ("PTSerif-Bold.ttf", "DejaVuSerif-Bold.ttf", "timesbd.ttf")
SERIF_ITALIC = ("PTSerif-Italic.ttf", "DejaVuSerif-Italic.ttf", "timesi.ttf")

SANS_NAMES = ("DejaVuSans.ttf", "arial.ttf", "segoeui.ttf")


def _first_existing(candidates) -> Path | None:
    for path in candidates:
        try:
            if path and Path(path).is_file():
                return Path(path)
        except Exception:
            continue
    return None


class Fonts:
    """Набор шрифтов документа с безопасным фоллбэком."""

    def __init__(self, base_dir: Path | None = None, bold=None, data_dirs=None):
        base = Path(base_dir) if base_dir else Path(__file__).resolve().parent
        data = base / "data"
        # Папки со шрифтами: `data/fonts` рядом с программой и в штатных местах
        # приложения (см. `_resource_dirs`) + системные шрифты Windows.
        font_dirs = []
        for folder in list(data_dirs or []) + [data, base]:
            candidate = Path(folder) / "fonts"
            font_dirs.append(candidate)
            font_dirs.append(Path(folder))
        fonts_dir = data / "fonts"
        system = Path("/usr/share/fonts/truetype/dejavu")
        win = Path("C:/Windows/Fonts")

        def pick(names, extra_dirs, fallback):
            paths = []
            for folder in extra_dirs:
                for name in names:
                    paths.append(Path(folder) / name)
            found = _first_existing(paths)
            return found or fallback

        dirs = tuple(font_dirs) + (data, fonts_dir, system, win)
        sans = pick(SANS_NAMES, dirs, None)
        regular = pick(SERIF_REGULAR, dirs, sans)
        bold = pick(SERIF_BOLD, dirs, regular)
        italic = pick(SERIF_ITALIC, dirs, regular)
        self.body_path = regular or sans
        self.bold_path = bold or self.body_path
        self.italic_path = italic or self.body_path
        self.sans_path = sans or regular
        script = pick(SCRIPT_FONT_NAMES, tuple(font_dirs) + (fonts_dir, data, system, win), None)
        self.script_is_handwritten = script is not None
        self.script_path = script or self.italic_path
        self._cache: dict = {}

    def get(self, size: int, kind: str = "body"):
        """kind: body | bold | italic | sans | script."""
        path = {
            "body": self.body_path,
            "bold": self.bold_path,
            "italic": self.italic_path,
            "sans": self.sans_path,
            "script": self.script_path,
        }.get(kind, self.body_path)
        key = (str(path), int(size))
        if key not in self._cache:
            try:
                self._cache[key] = ImageFont.truetype(str(path), int(size))
            except Exception:
                self._cache[key] = ImageFont.load_default()
        return self._cache[key]


# --------------------------------------------------------------------------- #
#  v57: русская типографика — неразрывные связки и красная строка
# --------------------------------------------------------------------------- #
# Источник правил — «Рекомендация.txt» в корне репозитория:
#   * короткие служебные слова (предлоги, союзы, частицы), инициалы,
#     сокращения («г.», «стр.», «№», «§»), числа с единицами не должны
#     оставаться в конце строки в одиночестве;
#   * частицы «бы/б/ли/ль/же/ж», тире и «и т. д.» наоборот не отрываются от
#     ПРЕДЫДУЩЕГО слова;
#   * отступ (красная строка) — только в начале абзаца, 1,25 см;
#   * если из-за связки строка становится «жидкой», правилом можно пренебречь.

NB = "\u00a0"            # неразрывный пробел
BREAK_SOFT = "\u200b"    # здесь переносить МОЖНО (мягкая связка)
BREAK_HARD = "\u200c"    # здесь переносить можно только ради плотности

# Вариант красной строки (ВЫБРАН вариант «all» — решение пользователя v57:
# «Сделай вариант 2 „all“ — отступ у ВСЕХ абзацев»):
#   "all"       — отступ у всех абзацев основного текста (ВЫБРАН);
#   "gost"      — по ГОСТ: первый абзац после заголовка без отступа;
#   "with_gaps" — отступ у всех + отбивка между абзацами.
# Значения «all»/«with_gaps» оставлены для сравнения: см.
# «Примеры_v57_красная_строка.png».
RED_LINE_VARIANT = "all"
# Вариант неразрывных связок (выбран «always» — строго по рекомендации):
#   "always"    — связки не рвутся никогда (ВЫБРАН);
#   "mixed"     — «жёсткие» (инициалы, «№», числа, тире) не рвутся, предлоги
#                 могут уехать ради плотности строки;
#   "exception" — ради плотности рвётся любая связка.
# Исключение из рекомендации («если строка стала слишком жидкой») реализовано
# и доступно вариантами «mixed»/«exception» — сравнение в
# «Примеры_v57_связки_узкая_колонка.png».
BIND_VARIANT = "always"

# v59: выравнивание по ширине. Раньше пробел разрешалось тянуть максимум в
# 2.6 раза, поэтому на строках с малым числом пробелов правый край оставался
# «рваным» (в AG-2 — 9 строк из 22, худший недобор 122 px). Теперь строка
# доходит до поля, а чтобы не появлялись «дыры», перед растяжкой слова
# перекидываются из следующей строки (`_rebalance`).
JUSTIFY_LAST_LINE = False     # последняя строка абзаца — по левому краю
SPACE_STRETCH_LIMIT = 3.2     # выше этого пробел не тянем — перекидываем слово
SPACE_HARD_LIMIT = 4.5        # абсолютный максимум, если перекинуть нечего

RED_LINE_PX = int(round(1.25 / 2.54 * DPI))     # 1,25 см при 200 dpi ≈ 98 px
# Насколько «жидкой» должна быть строка, чтобы связку разрешили порвать.
DENSITY_FILL_MIN = 0.78

# Предлоги и союзы из 1–2 букв: «в кино», «и ночь», частицы «не»/«ни».
SHORT_WORDS = {
    "в", "к", "о", "с", "у", "а", "и",
    "во", "до", "за", "из", "ко", "на", "об", "от", "по", "со",
    "но", "да", "ни", "то", "же", "бы", "не",
}
# Сокращения, которые не отрываются от следующего слова: «стр. 15», «№ 5».
SHORT_ABBR = {
    "г.", "гг.", "стр.", "рис.", "№", "п.", "пп.", "т.", "д.", "к.", "§",
    "ст.", "ч.", "пункт", "пункта", "пунктом", "статья", "статьи", "статьей",
    "глава", "главы", "главой", "часть", "части", "частью",
}
# Частицы ПОСЛЕ слова остаются на строке со словом перед ними.
POSTFIX_PARTICLES = {"бы", "б", "ли", "ль", "же", "ж"}


def _is_number(token: str) -> bool:
    clean = token.replace(",", "").replace(".", "").replace("-", "")
    return bool(clean) and clean.isdigit()


def _is_initials(token: str) -> bool:
    """«А.», «А.С.» — инициалы перед фамилией."""
    if not token.endswith("."):
        return False
    core = token.replace(".", "")
    return 1 <= len(core) <= 2 and core.isalpha() and core.upper() == core


def _bind_flags(words: list) -> tuple:
    """Возвращает (glue, hard) для пробелов между словами (v57).

    `glue[i]` — пробел между `words[i]` и `words[i+1]` рвать нельзя;
    `hard[i]` — связку нельзя рвать даже ради плотности строки.
    """
    count = max(len(words) - 1, 0)
    glue = [False] * count
    hard = [False] * count
    for index, word in enumerate(words):
        low = word.lower().strip(",;:«»\"'()")
        prev_low = words[index - 1].lower().strip(",;:«»\"'()") if index else ""
        last = index >= len(words) - 1
        # 1. Предлог/союз/частица перед словом — мягкая связка: «в кино».
        if low in SHORT_WORDS and not last:
            glue[index] = True
        # 2. Сокращение перед цифрой или словом — жёсткая: «стр. 15», «№ 5».
        # Исключение: хвост перечисления «и т. д.», «и т. п.» — он уже склеен
        # с «т.», а следующее слово притягивать не должен.
        tail = low in ("д.", "п.", "др.", "пр.") and prev_low in ("т.", "и")
        if low in SHORT_ABBR and not last and not tail:
            glue[index] = hard[index] = True
        # 3. Инициалы: «А. С. Пушкин» — цепочка до фамилии.
        if _is_initials(word) and not last:
            glue[index] = hard[index] = True
        # 4. Число перед словом/значком: «100 руб.», «50 %», «2026 г.».
        if _is_number(word) and not last:
            glue[index] = hard[index] = True
        # 5. Частица после слова: «сделал бы» — держим со словом слева.
        if low in POSTFIX_PARTICLES and index > 0:
            glue[index - 1] = hard[index - 1] = True
        # 6. Тире остаётся на строке с предыдущим словом, новую строку с него
        #    не начинаем: «Москва — столица».
        if word in ("—", "–", "-") and index > 0:
            glue[index - 1] = hard[index - 1] = True
            if not last:
                glue[index] = hard[index] = True
        # 7. «и т. д.», «и т. п.», «и др.», «и пр.» — одним куском.
        if low in ("д.", "п.", "др.", "пр.") and prev_low == "т.":
            glue[index - 1] = hard[index - 1] = True
        if low == "т." and prev_low == "и":
            glue[index - 1] = hard[index - 1] = True
    return glue, hard


def _bind_chunks(paragraph: str) -> list:
    """Делит абзац на куски, которые НЕЛЬЗЯ разрывать (v57).

    Возвращает список `[текст, можно_ли_порвать_ради_плотности]`. Внутри куска
    части склеены неразрывным пробелом, поэтому строка всегда переносится
    целиком: «в кино», «№ 301», «А. С. Пушкин», «100 руб.».
    """
    words = str(paragraph or "").split()
    if not words:
        return []
    glue, hard = _bind_flags(words)
    chunks: list = []
    for index, word in enumerate(words):
        if index and glue[index - 1]:
            chunks[-1][0] = f"{chunks[-1][0]}{NB}{word}"
            # связка жёсткая, если её нельзя рвать ради плотности
            chunks[-1][1] = not hard[index - 1]
        else:
            chunks.append([word, False])
    return chunks


def _chunk_text(chunk) -> str:
    """Текст куска без служебных маркеров (кусок — пара [текст, рвать_можно])."""
    text = str(chunk[0]) if isinstance(chunk, (list, tuple)) else str(chunk)
    return text.replace(NB, " ").replace(BREAK_SOFT, "").replace(BREAK_HARD, "")


def _row_text(row: list) -> str:
    """Строка для измерения/рисования: куски через обычный пробел."""
    return " ".join(_chunk_text(chunk) for chunk in row)


def _wrap_rows(draw, text: str, font, max_width: int,
               first_width: int | None = None) -> list:
    """Перенос по словам с учётом реальной ширины строки.

    v57: строка режется не по пробелам, а по «кускам» `_bind_words` —
    предлог уезжает на новую строку вместе со словом, «№» — с номером,
    инициалы — с фамилией. Затем (вариант `BIND_VARIANT`) слишком «жидкая»
    строка забирает первое слово следующей, то есть связка рвётся ради
    плотности — как разрешает «Рекомендация.txt».
    """
    lines: list = []
    for paragraph in str(text or "").split("\n"):
        chunks = _bind_chunks(paragraph)
        if not chunks:
            lines.append("")
            continue
        rows: list = []          # каждая строка — список кусков
        current: list = []
        for chunk in chunks:
            # v57: САМАЯ первая строка абзаца короче на красную строку —
            # лимит действует, пока не начата вторая строка (`not rows`).
            limit = first_width if (first_width and not rows) else max_width
            # Защита: связка из 3–4 слов может оказаться шире строки. Тогда
            # кусок всё равно режется по пробелам — иначе текст уехал бы за
            # поля (в рекомендации это «жидкая строка», а не вылет за край).
            parts = [chunk]
            if draw.textlength(_chunk_text(chunk), font=font) > limit:
                parts = [[word, False] for word in _chunk_text(chunk).split()]
            for part in parts:
                probe = _row_text(current + [part])
                if not current or draw.textlength(probe, font=font) <= limit:
                    current.append(part)
                else:
                    rows.append(current)
                    current = [part]
        rows.append(current)
        if len(rows) > 1:
            if BIND_VARIANT != "always":
                rows = _fix_density(draw, rows, font, max_width)
            # v59: снимаем «рваный» правый край — слова перекидываются между
            # строками до того, как пробелы начнут растягиваться.
            rows = _rebalance(draw, rows, font, max_width, first_width=first_width)
        lines.extend([row for row in rows if row])
    return lines


def _fix_density(draw, rows: list, font, max_width: int) -> list:
    """Разрешает «жидким» строкам забрать начало следующей (v57).

    Правило из «Рекомендация.txt»: если из-за неразрывной связки строка стала
    слишком разреженной, связкой можно пренебречь ради ровной плотности.
    Работает только когда это реально нужно (`BIND_VARIANT`) и только если
    строка после переноса всё ещё влезает в ширину абзаца.
    """
    if BIND_VARIANT == "always":
        return rows
    for index in range(len(rows) - 1):
        row, nxt = rows[index], rows[index + 1]
        while nxt:
            width = draw.textlength(_row_text(row), font=font)
            if width >= max_width * DENSITY_FILL_MIN:
                break
            may_break = nxt[0][1] or BIND_VARIANT == "exception"
            if not may_break:
                break
            head, _, tail = _chunk_text(nxt[0]).partition(" ")
            probe = _row_text(row + [[head, False]])
            if draw.textlength(probe, font=font) > max_width:
                break
            row.append([head, False])
            if tail:
                nxt[0] = [tail, False]
            else:
                nxt.pop(0)
    return [row for row in rows if row]


def _wrap(draw, text: str, font, max_width: int, first_width: int | None = None) -> list:
    """Строки абзаца как текст (для проверок и простых мест)."""
    return [_row_text(row) for row in
            _wrap_rows(draw, text, font, max_width, first_width=first_width)]


def _rebalance(draw, rows: list, font, max_width: int,
               first_width: int | None = None) -> list:
    """Выравнивает строки абзаца по плотности (v59).

    Задача: строка должна доходить до правого поля, но пробелы не должны
    превращаться в «дыры». Поэтому перед растяжкой считаем, во сколько раз
    придётся увеличить пробел. Если больше `SPACE_STRETCH_LIMIT` — забираем
    слово из следующей строки; если пробел пришлось бы наоборот сжимать —
    отдаём последнее слово вперёд.

    v59: у ПЕРВОЙ строки абзаца ширина меньше на красную строку. Без этого она
    наполнялась до полной ширины, при отрисовке места не хватало, и пробелы
    «схлопывались» («ГражданинLordikClaw…»).

    Работает только с готовыми строками: слова заново не переносятся, поэтому
    красная строка и отступы не меняются.
    """
    rows = [list(row) for row in (rows or [])]
    if not rows:
        return rows
    natural = draw.textlength(" ", font=font) or 1

    def row_width(row) -> float:
        return draw.textlength(_row_text(row), font=font)

    for index in range(len(rows) - 1):
        row, nxt = rows[index], rows[index + 1]
        limit = first_width if (first_width and index == 0) else max_width
        # 1. Строка «жидкая» — забираем слова из следующей, пока не станет
        #    плотной и пока влезает в ширину. v59: переносим только ЦЕЛЫЕ
        #    куски — иначе отрывался предлог от своего слова
        #    («заявления от гражданина и приложенных к» + «обращению»).
        for _ in range(3):
            words = _row_text(row).split()
            if len(words) < 2 or not nxt:
                break
            need = (limit - row_width(row)) / (len(words) - 1)
            if need <= natural * SPACE_STRETCH_LIMIT:
                break
            piece = nxt[0]
            if row_width(row + [piece]) > limit:
                # Крайний случай: целый кусок не влезает, а строка осталась бы
                # «жидкой». Забираем ПЕРВОЕ СЛОВО куска, но только если оно
                # самостоятельное (не предлог/союз/сокращение) — иначе снова
                # оторвали бы служебное слово от своего слова.
                head, _, tail = _chunk_text(piece).partition(" ")
                if not tail:
                    break
                # Делим кусок по границе слова. Если первая часть — служебное
                # слово («к»), берём вместе с ним и следующее («к обращению»),
                # чтобы предлог не остался в конце строки в одиночестве.
                head_words = head.split()
                if len(head_words) == 1 and head_words[0].strip().lower() in (
                        SHORT_WORDS | SHORT_ABBR):
                    two, _, rest = tail.partition(" ")
                    head = head + " " + two
                    tail = rest
                    if not tail:
                        break
                if row_width(row + [[head, False]]) > limit:
                    break
                row.append([head, False])
                nxt[0] = [tail, False]
                continue
            row.append(piece)
            nxt.pop(0)
        # 2. Обратный случай: пробел пришлось бы сжимать сильнее нормы —
        #    отдаём последнее слово в следующую строку.
        for _ in range(2):
            words = _row_text(row).split()
            if len(words) < 3:
                break
            need = (limit - row_width(row)) / (len(words) - 1)
            if need >= natural * 0.55:
                break
            tail_word = row.pop()
            if row_width([tail_word] + nxt) > max_width:
                row.append(tail_word)
                break
            nxt.insert(0, tail_word)
    return [row for row in rows if row]


def draw_rows(draw, rows: list, font, x: int, y: int, max_width: int,
              line_h: int, fill=None, first_indent: int = 0,
              justify_last: bool = False) -> int:
    """Рисует готовые строки-ряды с выравниванием ПО ШИРИНЕ (v57).

    Ряды уже нарезаны с учётом неразрывных связок и красной строки, поэтому
    здесь НЕ происходит повторный перенос — только раскладка пробелов.
    Первая строка ряда сдвинута на `first_indent` и короче на столько же.
    Возвращает новое значение `y`.
    """
    fill = fill or INK
    for index, row in enumerate(rows):
        text_line = _row_text(row)
        words = text_line.split()
        shift = first_indent if index == 0 else 0
        line_x = x + shift
        line_width = max_width - shift
        last_line = index == len(rows) - 1
        if not words:
            y += line_h
            continue
        if last_line and not justify_last:
            draw.text((line_x, y), text_line, font=font, fill=fill, anchor="la")
            y += line_h
            continue
        if len(words) == 1:
            draw.text((line_x, y), words[0], font=font, fill=fill, anchor="la")
            y += line_h
            continue
        words_width = sum(draw.textlength(word, font=font) for word in words)
        natural = draw.textlength(" ", font=font) or 1
        space = (line_width - words_width) / (len(words) - 1)
        # v59: строки уже перебалансированы (`_rebalance`), поэтому здесь
        # пробел тянется ровно до поля. Потолок оставлен только как страховка
        # от совсем уж «дырявой» строки.
        space = min(space, natural * SPACE_HARD_LIMIT)
        cursor = float(line_x)
        for word_index, word in enumerate(words):
            draw.text((cursor, y), word, font=font, fill=fill, anchor="la")
            cursor += draw.textlength(word, font=font)
            if word_index < len(words) - 1:
                cursor += space
        y += line_h
    return y


def draw_justified(draw, text: str, font, x: int, y: int, max_width: int,
                   line_h: int, fill=None, justify_last: bool = False,
                   first_indent: int = 0) -> int:
    """Рисует текст с выравниванием ПО ШИРИНЕ (как в документе Word).

    v33 (замечание): текст в бланке был выровнен по левому краю — строки
    заканчивались «лесенкой». Теперь каждая строка абзаца, кроме последней,
    растягивается до ширины абзаца за счёт межсловных пробелов.

    Реализация: для каждой строки считаем «лишнее» место и раздаём его
    пробелам. Слова рисуются по одному, поэтому ширина строки получается
    ровно `max_width` без искажения самих слов.

    Возвращает новое значение `y` (низ последней строки).
    """
    fill = fill or INK
    lines = _wrap(draw, text, font, max_width, first_width=max_width - first_indent)
    for index, line in enumerate(lines):
        words = line.split()
        last_line = index == len(lines) - 1
        # v57: первая строка сдвинута вправо на красную строку и короче.
        shift = first_indent if index == 0 else 0
        line_x = x + shift
        line_width = max_width - shift
        if not words:
            y += line_h
            continue
        if last_line and not justify_last:
            # последняя строка абзаца остаётся как есть — иначе «рваный» край
            draw.text((line_x, y), line, font=font, fill=fill, anchor="la")
            y += line_h
            continue
        if len(words) == 1:
            draw.text((line_x, y), words[0], font=font, fill=fill, anchor="la")
            y += line_h
            continue
        words_width = sum(draw.textlength(word, font=font) for word in words)
        gap_total = line_width - words_width
        space = gap_total / (len(words) - 1)
        natural = draw.textlength(" ", font=font) or 1
        # v59: до поля, с той же страховкой, что и в `draw_rows`.
        space = min(space, natural * SPACE_HARD_LIMIT)
        space = max(space, natural * 0.6)
        cursor = float(line_x)
        for word_index, word in enumerate(words):
            draw.text((cursor, y), word, font=font, fill=fill, anchor="la")
            cursor += draw.textlength(word, font=font)
            if word_index < len(words) - 1:
                cursor += space
        y += line_h
    return y


def _background_color(image: Image.Image) -> tuple:
    """Цвет фона по углам картинки (гербы сданы на «почти белом», не на #FFF)."""
    rgba = image.convert("RGBA")
    width, height = rgba.size
    points = [(2, 2), (width - 3, 2), (2, height - 3), (width - 3, height - 3)]
    samples = [rgba.getpixel(p) for p in points if 0 <= p[0] < width and 0 <= p[1] < height]
    if not samples:
        return (255, 255, 255, 255)
    return max(samples, key=lambda s: sum(s[:3]))


def _knock_out_background(image: Image.Image, tolerance: int = 42) -> Image.Image:
    """Делает фон PNG прозрачным по цвету углов.

    Герб и печать приходят на «почти белом» фоне (не ровно #FFFFFF) — на
    бланке это давало белый/серый квадрат вокруг эмблемы. Убираем только
    пиксели, близкие к цвету фона; линии и цвета герба остаются как есть.
    """
    rgba = image.convert("RGBA")
    base_r, base_g, base_b, _ = _background_color(rgba)
    pixels = rgba.load()
    width, height = rgba.size
    tol2 = tolerance * tolerance
    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            if not a:
                continue
            dr_, dg, db = r - base_r, g - base_g, b - base_b
            if dr_ * dr_ + dg * dg + db * db <= tol2:
                pixels[x, y] = (base_r, base_g, base_b, 0)
    return rgba


def _trim(image: Image.Image, padding: int = 4) -> Image.Image:
    """Обрезает прозрачные поля вокруг рисунка (гербы сданы с большим полем)."""
    bbox = image.split()[-1].getbbox()
    if not bbox:
        return image
    left, top, right, bottom = bbox
    left = max(0, left - padding)
    top = max(0, top - padding)
    right = min(image.width, right + padding)
    bottom = min(image.height, bottom + padding)
    return image.crop((left, top, right, bottom))


def load_asset(path: Path | None) -> Image.Image | None:
    """Герб/печать с прозрачным фоном и без лишних полей."""
    if not path or not Path(path).is_file():
        return None
    try:
        image = Image.open(path).convert("RGBA")
    except Exception:
        return None
    return _trim(_knock_out_background(image))


def _paste_logo(page: Image.Image, path: Path | None, size: int, center_x: int, top: int,
                opacity: float = 1.0) -> int:
    """Вставляет герб/печать с сохранением пропорций. Возвращает нижнюю границу."""
    stamp = load_asset(path)
    if stamp is None:
        return top
    ratio = size / max(1, stamp.width)
    stamp = stamp.resize((size, max(1, int(stamp.height * ratio))), Image.LANCZOS)
    if opacity < 1.0:
        alpha = stamp.split()[-1].point(lambda a: int(a * opacity))
        stamp.putalpha(alpha)
    page.alpha_composite(stamp, (center_x - stamp.width // 2, top))
    return top + stamp.height


def _watermark(page: Image.Image, path: Path | None, opacity: float = 0.10) -> None:
    """Бледный герб-«водяной знак» по центру листа.

    Полупрозрачность считается от «темноты» пикселя: белые части герба
    (и его фон) полностью прозрачны, поэтому на бланке не появляется
    серый прямоугольник — видна только сама эмблема.
    """
    logo = load_asset(path)
    if logo is None:
        return
    size = int(PAGE_W * 0.58)
    ratio = size / max(1, logo.width)
    logo = logo.resize((size, max(1, int(logo.height * ratio))), Image.LANCZOS)

    # Берём непрозрачные пиксели эмблемы: чем темнее пиксель, тем заметнее
    # водяной знак. Фон уже вырезан, поэтому прямоугольника не будет.
    luminance = logo.convert("L")
    mask = logo.split()[-1]
    alpha = Image.composite(
        luminance.point(lambda value: int((255 - value) / 255 * 255 * 1.1 * opacity)),
        Image.new("L", logo.size, 0),
        mask.point(lambda value: 255 if value > 24 else 0),
    )
    watermark = Image.new("RGBA", logo.size, (70, 78, 96, 255))
    watermark.putalpha(alpha)
    page.alpha_composite(
        watermark,
        ((PAGE_W - logo.width) // 2, (PAGE_H - logo.height) // 2),
    )


def _resource_dirs() -> tuple:
    """Все места, где могут лежать `data/gerb_lscsd.png`, `data/fonts/*.ttf`.

    Важно: рабочая папка приложения (`APP_DIR`) — это `%APPDATA%\\Legalyze`,
    а ресурсы едут вместе с программой. Из-за этого в v28 герб, печать и
    рукописный шрифт не находились, хотя в v27 подхватывались из папки
    рядом с `main.py`. Перебираем оба варианта и не зависим от того, как
    запущено приложение (скрипт, exe, Nuitka).
    """
    dirs = []
    try:
        import config as _cfg
        dirs.extend([Path(_cfg.DATA_DIR), Path(getattr(_cfg, "BUNDLE_DATA_DIR", ""))])
    except Exception:
        pass
    module_dir = Path(__file__).resolve().parent
    dirs.extend([module_dir / "data", module_dir])
    # рядом с исполняемым файлом (сборка standalone кладёт data/ туда)
    try:
        dirs.append(Path(sys.executable).resolve().parent / "data")
    except Exception:
        pass
    seen, result = set(), []
    for folder in dirs:
        try:
            key = str(folder)
        except Exception:
            continue
        if key and key not in seen and folder.is_dir():
            seen.add(key)
            result.append(folder)
    return tuple(result)


class DocAssets:
    """Реквизиты бланка: герб, печать, подписи, шрифты."""

    def __init__(self, base_dir: Path | None = None, coat: Path | None = None,
                 stamp: Path | None = None):
        base = Path(base_dir) if base_dir else Path(__file__).resolve().parent
        # Папки данных: сначала переданная явно, затем штатные места приложения.
        data_dirs = []
        for folder in [base / "data", base] + list(_resource_dirs()):
            if folder not in data_dirs:
                data_dirs.append(folder)

        self.folder = data_dirs[0] / "postanovleniya"
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
        except Exception:
            diag_silent()

        def find(names):
            candidates = []
            for folder in data_dirs:
                for name in names:
                    candidates.append(folder / name)
            return _first_existing(candidates)

        self.coat = Path(coat) if coat else find(
            ["gerb_lscsd.png", "gerb.png", "postanovleniya/gerb.png"])
        self.stamp = Path(stamp) if stamp else find(
            ["stamp_lscsd.png", "pechat.png", "stamp.png", "postanovleniya/pechat.png"])
        # v38: у Офиса Генерального прокурора свой герб и своя печать. Раньше
        # на бланке постановления Прокурора стояли герб и печать LSCSD
        # (выгрузки «Постановление_3»), и шапка спорила с «OFFICE OF THE
        # ATTORNEY GENERAL».
        self.coat_gov = find(["gerb_gov.png", "postanovleniya/gerb_gov.png"])
        self.stamp_gov = find(["stamp_gov.png", "stamp_gov_gov.png",
                               "postanovleniya/stamp_gov.png"])
        self.fonts = Fonts(base, data_dirs=data_dirs)

    def for_gov(self) -> "DocAssets":
        """Копия реквизитов с гербом и печатью Офиса Генерального прокурора."""
        clone = DocAssets.__new__(DocAssets)
        clone.__dict__.update(self.__dict__)
        if self.coat_gov and Path(self.coat_gov).is_file():
            clone.coat = Path(self.coat_gov)
        if self.stamp_gov and Path(self.stamp_gov).is_file():
            clone.stamp = Path(self.stamp_gov)
        return clone

    def describe(self) -> dict:
        return {
            "gerb": str(self.coat) if self.coat and Path(self.coat).is_file() else "",
            "pechat": str(self.stamp) if self.stamp and Path(self.stamp).is_file() else "",
            "shrift_podpisi": str(self.fonts.script_path or ""),
        }


def diag_silent() -> None:
    """Диагностика не должна ломать рендер: модуль работает и без неё."""
    try:
        import diagnostics as _diag
        _diag.exception("doc_render.postanovleniya_dir")
    except Exception:
        pass


def _new_page(assets: DocAssets) -> Image.Image:
    page = Image.new("RGBA", (PAGE_W, PAGE_H), (255, 255, 255, 255))
    _watermark(page, assets.coat)
    return page


def _draw_header(page: Image.Image, draw, doc, assets: DocAssets) -> int:
    """Шапка бланка. Возвращает Y, с которого начинается текст."""
    center = PAGE_W // 2
    y = MARGIN_TOP

    # Герб по центру (первое, что видно на бланке).
    logo_bottom = _paste_logo(page, assets.coat, 260, center, y)
    if logo_bottom > y:
        y = logo_bottom + 18
    else:
        y += 8

    # Ведомство и подразделение.
    f_dept = assets.fonts.get(32, "bold")
    for text in (doc.get("vedomstvo"), doc.get("podrazdelenie"), doc.get("state")):
        if not text:
            continue
        draw.text((center, y), text.upper(), font=f_dept, fill=ACCENT, anchor="ma")
        y += 42

    # v36: бланк Прокурора — «ПОСТАНОВЛЕНИЕ № N» по центру и «город, дата»
    # под ним, как в примерах Офиса Генерального прокурора.
    government = hasattr(doc, "number_line") and callable(getattr(doc, "number_line"))
    if government:
        f_num = assets.fonts.get(30, "bold")
        draw.text((center, y), doc.number_line(), font=f_num, fill=INK, anchor="ma")
        y += 44
        city_line = ", ".join(x for x in (doc.get("city"), doc.get("doc_date")) if x)
        if city_line:
            f_small_city = assets.fonts.get(24, "body")
            draw.text((center, y), city_line, font=f_small_city, fill=MUTED, anchor="ma")
            y += 34
        y += 6
        draw.line((MARGIN_X, y, PAGE_W - MARGIN_X, y), fill=LINE, width=2)
        y += 40

    # Дата и номер справа.
    f_small = assets.fonts.get(26, "body")
    y += 6
    right = PAGE_W - MARGIN_X
    if government:
        pass                       # номер и дата уже напечатаны по центру
    elif doc.get("doc_date"):
        draw.text((right, y), f"Дата: {doc.get('doc_date')}", font=f_small, fill=MUTED, anchor="ra")
        y += 34
    if not government and doc.get("doc_number"):
        draw.text((right, y), f"№ {doc.get('doc_number')}", font=f_small, fill=MUTED, anchor="ra")
        y += 34

    # Линия под шапкой.
    y += 10
    draw.line((MARGIN_X, y, PAGE_W - MARGIN_X, y), fill=LINE, width=2)
    y += 44

    # Заголовок документа: жирный, по центру — главный акцент бланка.
    f_title = assets.fonts.get(40, "bold")
    for _n, line in enumerate(_wrap(draw, doc.document_title(), f_title, PAGE_W - 2 * MARGIN_X)):
        draw.text((center, y), line, font=f_title, fill=INK, anchor="ma")
        y += 52
    y += 28
    return y


def _draw_signature(page: Image.Image, text: str, font, right: int, baseline_y: int,
                    handwritten: bool) -> None:
    """Рисует подпись у правого края.

    Настоящий рукописный шрифт (Caveat / Great Vibes / Marck Script) можно
    просто положить .ttf в `data/fonts` — код найдёт его сам и рисовать будет
    без наклона. Если такого шрифта нет, подпись слегка наклоняется, чтобы
    не выглядела машинописной строкой.
    """
    if not text:
        return
    probe = Image.new("RGBA", (10, 10))
    bbox = ImageDraw.Draw(probe).textbbox((0, 0), text, font=font)
    width = max(1, bbox[2] - bbox[0]) + 24
    height = max(1, bbox[3] - bbox[1]) + 32
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    ImageDraw.Draw(layer).text(
        (12 - bbox[0], 12 - bbox[1]), text, font=font, fill=ACCENT)
    if not handwritten:
        layer = layer.rotate(3.5, resample=Image.BICUBIC, expand=True)
    page.alpha_composite(layer, (max(0, right - layer.width), max(0, baseline_y)))


def _draw_footer(page: Image.Image, draw, doc, assets: DocAssets, page_no: int, total: int,
                 last: bool) -> None:
    """Подписи, печать и колонтитул. Подписи — только на последней странице."""
    bottom = PAGE_H - MARGIN_BOTTOM
    # v36: бланк Прокурора подписывает прокурор — рядом с должностью печатаем
    # номер удостоверения (так в примерах Офиса Генерального прокурора).
    government = hasattr(doc, "number_line") and callable(getattr(doc, "number_line"))

    if last:
        # Печать слева.
        # v34 (замечание): печать была 330 px и налезала на текст. Уменьшена
        # ВДВОЕ (165 px) и прижата к нижнему краю так, чтобы её верхняя граница
        # гарантированно была ниже последней строки документа.
        stamp_size = 330 // 2
        if assets.stamp and Path(assets.stamp).is_file():
            stamp_top = bottom - stamp_size - 30
            # не поднимаемся выше safe-линии текста
            stamp_top = max(stamp_top, getattr(doc, "_text_bottom", 0) + 8)
            # v35 (замечание): печать чуть правее — ближе к центру левой части,
            # чтобы не прилипала к полю и не спорила с подписью.
            stamp_center_x = MARGIN_X + stamp_size // 2 + 70
            _paste_logo(page, assets.stamp, stamp_size, stamp_center_x,
                        stamp_top, opacity=0.92)

        # Подпись справа: рукописный шрифт + должность под ней.
        f_script = assets.fonts.get(104, "script")
        f_role = assets.fonts.get(26, "body")
        right = PAGE_W - MARGIN_X
        sign = doc.signature() or "—"
        _draw_signature(page, sign, f_script, right, bottom - 190,
                        assets.fonts.script_is_handwritten)
        draw.line((right - 460, bottom - 80, right, bottom - 80), fill=LINE, width=2)
        role = doc.get("author_rank")
        ident = doc.get("author_id") if government else ""
        if ident:
            role = f"{role}, удостоверение № {ident}" if role else f"Удостоверение № {ident}"
        draw.text((right, bottom - 66), role, font=f_role, fill=MUTED, anchor="ra")

    f_foot = assets.fonts.get(22, "sans")
    draw.text((PAGE_W // 2, PAGE_H - 84), f"стр. {page_no} из {total}",
              font=f_foot, fill=MUTED, anchor="ma")
    draw.text((MARGIN_X, PAGE_H - 84), doc.get("vedomstvo", "")[:60],
              font=f_foot, fill=MUTED, anchor="la")


def render_document(doc, assets: DocAssets | None = None) -> list:
    """Рендер постановления в список страниц PNG (PIL.Image), A4 200 dpi."""
    assets = assets or DocAssets()
    # v36: бланк Прокурора определяется наличием `number_line()` и `facts_text()`.
    government = hasattr(doc, "facts_text") and callable(getattr(doc, "facts_text"))
    if government and hasattr(assets, "for_gov"):
        assets = assets.for_gov()
    pages: list = []
    page = _new_page(assets)
    draw = ImageDraw.Draw(page)
    y = _draw_header(page, draw, doc, assets)

    max_width = PAGE_W - 2 * MARGIN_X
    # v39: нижняя граница текста. Раньше здесь вычиталось ещё 260 px «под
    # подписи», но запас считался от `MARGIN_BOTTOM` — а колонтитул «стр. N из M»
    # рисуется на `PAGE_H - 84`, то есть ВЫШЕ этой границы почти на 70 px.
    # Из-за этого последняя строка абзаца наезжала на колонтитул (видно на
    # выгрузке AG-2). Теперь граница — над колонтитулом с честным зазором.
    limit = PAGE_H - MARGIN_BOTTOM - 120

    f_body = assets.fonts.get(28, "body")
    f_head = assets.fonts.get(30, "body")
    line_h = 44

    def ensure(height: int) -> None:
        """Перенос на новую страницу, если блок не помещается."""
        nonlocal page, draw, y
        # v39: подпись и печать занимают нижние ~210 px последней страницы, но
        # занимать их «заранее» на каждой странице нельзя — иначе теряется
        # четверть листа. Держим текст в границах `limit`, а если на последней
        # странице он всё-таки дошёл до зоны подписи — поднимаем подпись/печать
        # не выше `_text_bottom` (это уже сделано в _draw_footer).
        if y + height <= limit:
            return
        pages.append(page)
        page = _new_page(assets)
        draw = ImageDraw.Draw(page)
        y = MARGIN_TOP
        draw.text((PAGE_W // 2, y), doc.document_title().upper(),
                  font=assets.fonts.get(24, "bold"), fill=MUTED, anchor="ma")
        draw.line((MARGIN_X, y + 42, PAGE_W - MARGIN_X, y + 42), fill=LINE, width=1)
        y += 78

    def para(text: str, font=None, indent: int = 0, gap: int = 10,
             justify: bool = True, red_line: bool | None = None) -> None:
        """Абзац документа. v33: по умолчанию выравнивание по ширине.

        v57: красная строка (абзацный отступ 1,25 см) — по «Рекомендация.txt».
        `red_line=None` — решает вариант `RED_LINE_VARIANT`. Выбран «all»:
        отступ у ВСЕХ абзацев основного текста. В режиме «gost» первый абзац
        после заголовка печатался бы без отступа.
        """
        nonlocal y
        font = font or f_body
        first_indent = 0
        if red_line is None:
            red_line = RED_LINE_VARIANT in ("all", "with_gaps")
        if red_line:
            first_indent = RED_LINE_PX
        width = max_width - indent
        if not justify:
            for line in _wrap(draw, text, font, width - first_indent):
                ensure(line_h)
                if line:
                    draw.text((MARGIN_X + indent, y), line, font=font, fill=INK,
                              anchor="la")
                y += line_h
            y += gap
            return
        # v39: перенос страницы ВНУТРИ абзаца. Раньше здесь стоял цикл
        # `for line in _wrap(...): ensure(line_h)`, но `y` внутри цикла не
        # менялся — `ensure` всегда пропускал, и длинный абзац «УСТАНОВИЛ»
        # дорисовывался поверх колонтитула (выгрузка AG-2). Теперь режем абзац
        # на столько строк, сколько влезает до `limit`, и переносим остаток.
        # v57: строки считаются ЗАРАНЕЕ (с учётом связок и красной строки), а
        # потом рисуются как есть — повторного переноса нет, иначе первая
        # строка с отступом рисовалась бы без пробелов.
        rows = _wrap_rows(draw, text, font, width, first_width=width - first_indent)
        printed = 0
        while printed < len(rows):
            room = int((limit - y) // line_h)
            if room <= 0:
                ensure(10 ** 6)          # принудительный новый лист
                room = int((limit - y) // line_h)
                if room <= 0:
                    break
            chunk = rows[printed:printed + room]
            printed += len(chunk)
            # v57: красная строка есть только у ПЕРВОЙ части абзаца; при
            # переносе на следующий лист остаток печатается без отступа.
            hang = first_indent if printed - len(chunk) == 0 else 0
            y = draw_rows(draw, chunk, font, MARGIN_X + indent, y, width, line_h,
                          INK, first_indent=hang)
            if printed < len(rows):
                ensure(10 ** 6)          # остаток абзаца — на следующий лист
        # «with_gaps» — отступ плюс отбивка; «gost»/«all» — только отступ,
        # пустой строки между абзацами не добавляем (по «Рекомендация.txt»).
        y += gap if RED_LINE_VARIANT == "with_gaps" else max(6, gap - 4)

    # Вводная часть. v57: в режиме «gost» первый абзац после заголовка
    # печатается БЕЗ красной строки (исключение из «Рекомендация.txt»);
    # в выбранном режиме «all» отступ есть у ВСЕХ абзацев (решение v57).
    para(doc.preamble(), gap=22, red_line=RED_LINE_VARIANT != "gost")

    # v36: бланк Прокурора — сначала «УСТАНОВИЛ» (факты и правовая оценка),
    # потом «ПОСТАНОВИЛ» (резолюция). У IAD раздела «Установил» нет.
    def section(title: str) -> None:
        """Ключевое слово раздела — по центру листа (v37).

        Замечание: «сделай ключевые слова на подобии "УСТАНОВИЛ" "ПОСТАНОВИЛ"
        по средине». Раньше они прижимались к левому полю и терялись в тексте;
        теперь это отдельная строка по центру, с отбивкой сверху и снизу.
        """
        nonlocal y
        ensure(84)
        y += 14
        draw.text((PAGE_W // 2, y), title, font=f_head, fill=ACCENT, anchor="ma")
        y += 58

    if hasattr(doc, "facts_text"):
        section("УСТАНОВИЛ:")
        facts = doc.facts_text()
        if facts:
            # v57: первый абзац после «УСТАНОВИЛ:» — без красной строки.
            para(facts, gap=20, red_line=RED_LINE_VARIANT != "gost")
        else:
            para("Обстоятельства обращения требуют проверки на предмет наличия "
                 "либо отсутствия признаков нарушения законодательства штата "
                 "Сан-Андреас.", gap=20)

    # Постановляющая часть.
    section("ПОСТАНОВИЛ:")
    for i, point in enumerate(doc.decision_points(), 1):
        para(f"{i}. {point}", indent=0, gap=14)

    # Основания: нарушенный акт и доказательства (если заданы).
    facts = []
    # v56 (замечание п.2 по выгрузке «Постановление»): блок «Основание:» в PNG
    # БОЛЬШЕ НЕ ПЕЧАТАЕТСЯ — ни в бланке IAD, ни в бланке Прокурора.
    # Замечание пользователя: «„Основание: пункт 2.11 (5.1, 5.5… Устава LSCSD“
    # не включай в принципе блок в .png документ такой как „Основание:“».
    # В выгрузке блок к тому же печатался с задвоенным названием акта
    # («… Устава LSCSD) Устава Los Santos County Sheriff Department»).
    # Сами нормы остаются в полях документа: «ИИ-документ.txt», «ИИ-поля.json»
    # и «История» их сохраняют — из PNG уходит только строка бланка.
    show_basis = bool(getattr(doc, "PNG_SHOW_BASIS", True))
    basis = doc.basis_text() if hasattr(doc, "basis_text") else ""
    if basis and show_basis:
        facts.append("Основание: " + basis)
    # v36: у бланка Прокурора обстоятельства уже стоят в разделе «УСТАНОВИЛ»,
    # второй раз их печатать нельзя. У IAD раздел один — там всё как было.
    if doc.get("situation") and not government:
        facts.append("Обстоятельства: " + doc.get("situation"))
    # v33 (замечание): строку «Доказательства:» в бланке не печатаем вовсе.
    if doc.get("extra"):
        facts.append("Дополнительно: " + doc.get("extra"))
    if facts:
        y += 10
        for fact in facts:
            para(fact, gap=14)

    # v34: запоминаем, где закончился текст — печать и подпись не должны
    # на него наезжать.
    try:
        page._text_bottom = y            # type: ignore[attr-defined]
        doc._text_bottom = y             # type: ignore[attr-defined]
    except Exception:
        pass
    pages.append(page)

    total = len(pages)
    for index, page_item in enumerate(pages, 1):
        draw_item = ImageDraw.Draw(page_item)
        _draw_footer(page_item, draw_item, doc, assets, index, total, index == total)
    return pages


def save_pages(pages: list, target_dir: Path, stem: str,
               numbered: bool = False) -> list:
    """Сохраняет страницы в PNG. Одна страница — `<stem>.png`, много — с номерами.

    v66 (согласованная схема хранения): у постановлений нового образца КАЖДАЯ
    страница получает номер — «0001-1 — Этап 1 — стр. 1.png». Постоянные имена
    нужны по двум причинам:

    * комплект версии переносится в `Архив/Версия-NNN` целиком, и по имени
      видно, какая это страница;
    * если в новой версии страниц стало меньше, старые страницы не остаются
      лежать рядом с новыми (критерий 12 приёмки).

    `numbered=False` сохраняет прежнее поведение: одна страница — без номера.
    Режим IAD и старые дела не затрагиваются.
    """
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    written = []
    if len(pages) == 1 and not numbered:
        path = target_dir / f"{stem}.png"
        pages[0].convert("RGB").save(path, "PNG", dpi=(DPI, DPI))
        return [path]
    for index, page in enumerate(pages, 1):
        suffix = f" — стр. {index}.png" if numbered else f" — стр.{index}.png"
        path = target_dir / f"{stem}{suffix}"
        page.convert("RGB").save(path, "PNG", dpi=(DPI, DPI))
        written.append(path)
    return written


def render_to_png(doc, target_dir: Path, stem: str, assets: DocAssets | None = None,
                  numbered: bool = False) -> list:
    return save_pages(render_document(doc, assets), target_dir, stem,
                      numbered=numbered)
