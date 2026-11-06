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


def _wrap(draw, text: str, font, max_width: int) -> list:
    """Перенос по словам с учётом реальной ширины строки."""
    lines: list = []
    for paragraph in str(text or "").split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            probe = f"{current} {word}"
            if draw.textlength(probe, font=font) <= max_width:
                current = probe
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def draw_justified(draw, text: str, font, x: int, y: int, max_width: int,
                   line_h: int, fill=None, justify_last: bool = False) -> int:
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
    lines = _wrap(draw, text, font, max_width)
    for index, line in enumerate(lines):
        words = line.split()
        last_line = index == len(lines) - 1
        if not words:
            y += line_h
            continue
        if last_line and not justify_last:
            # последняя строка абзаца остаётся как есть — иначе «рваный» край
            draw.text((x, y), line, font=font, fill=fill, anchor="la")
            y += line_h
            continue
        if len(words) == 1:
            draw.text((x, y), words[0], font=font, fill=fill, anchor="la")
            y += line_h
            continue
        words_width = sum(draw.textlength(word, font=font) for word in words)
        gap_total = max_width - words_width
        space = gap_total / (len(words) - 1)
        # Не раздуваем пробелы больше, чем в 2.6 раза: иначе строка «рвётся».
        natural = draw.textlength(" ", font=font) or 1
        space = min(space, natural * 2.6)
        cursor = float(x)
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
        self.fonts = Fonts(base, data_dirs=data_dirs)

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

    # Дата и номер справа.
    f_small = assets.fonts.get(26, "body")
    y += 6
    right = PAGE_W - MARGIN_X
    if doc.get("doc_date"):
        draw.text((right, y), f"Дата: {doc.get('doc_date')}", font=f_small, fill=MUTED, anchor="ra")
        y += 34
    if doc.get("doc_number"):
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

    if last:
        # Печать слева.
        stamp_size = 330
        if assets.stamp and Path(assets.stamp).is_file():
            _paste_logo(page, assets.stamp, stamp_size, MARGIN_X + stamp_size // 2,
                        bottom - stamp_size - 40, opacity=0.92)

        # Подпись справа: рукописный шрифт + должность под ней.
        f_script = assets.fonts.get(104, "script")
        f_role = assets.fonts.get(26, "body")
        right = PAGE_W - MARGIN_X
        sign = doc.signature() or "—"
        _draw_signature(page, sign, f_script, right, bottom - 190,
                        assets.fonts.script_is_handwritten)
        draw.line((right - 460, bottom - 80, right, bottom - 80), fill=LINE, width=2)
        role = doc.get("author_rank")
        draw.text((right, bottom - 66), role, font=f_role, fill=MUTED, anchor="ra")

    f_foot = assets.fonts.get(22, "sans")
    draw.text((PAGE_W // 2, PAGE_H - 84), f"стр. {page_no} из {total}",
              font=f_foot, fill=MUTED, anchor="ma")
    draw.text((MARGIN_X, PAGE_H - 84), doc.get("vedomstvo", "")[:60],
              font=f_foot, fill=MUTED, anchor="la")


def render_document(doc, assets: DocAssets | None = None) -> list:
    """Рендер постановления в список страниц PNG (PIL.Image), A4 200 dpi."""
    assets = assets or DocAssets()
    pages: list = []
    page = _new_page(assets)
    draw = ImageDraw.Draw(page)
    y = _draw_header(page, draw, doc, assets)

    max_width = PAGE_W - 2 * MARGIN_X
    limit = PAGE_H - MARGIN_BOTTOM - 260  # оставляем место под подписи

    f_body = assets.fonts.get(28, "body")
    f_head = assets.fonts.get(30, "body")
    line_h = 44

    def ensure(height: int) -> None:
        """Перенос на новую страницу, если блок не помещается."""
        nonlocal page, draw, y
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
             justify: bool = True) -> None:
        """Абзац документа. v33: по умолчанию выравнивание по ширине."""
        nonlocal y
        font = font or f_body
        width = max_width - indent
        if not justify:
            for line in _wrap(draw, text, font, width):
                ensure(line_h)
                if line:
                    draw.text((MARGIN_X + indent, y), line, font=font, fill=INK,
                              anchor="la")
                y += line_h
            y += gap
            return
        # Постранично: строки рисуем группами, чтобы уважать перенос страницы.
        pending: list = []

        def flush() -> None:
            nonlocal y, pending
            if pending:
                block_text = "\n".join(pending)
                y = draw_justified(draw, block_text, font, MARGIN_X + indent, y,
                                   width, line_h, INK)
                pending = []

        for line in _wrap(draw, text, font, width):
            ensure(line_h)
            pending.append(line)
        flush()
        y += gap

    # Вводная часть.
    para(doc.preamble(), gap=22)

    # Постановляющая часть.
    draw.text((MARGIN_X, y), "ПОСТАНОВИЛ:", font=f_head, fill=ACCENT, anchor="la")
    y += 58
    for i, point in enumerate(doc.decision_points(), 1):
        para(f"{i}. {point}", indent=0, gap=14)

    # Основания: нарушенный акт и доказательства (если заданы).
    facts = []
    # v33: основание собирает сам документ — устав + УК/процессуальные нормы
    # и иные акты «в части касающейся» (раньше печатался только устав).
    basis = doc.basis_text() if hasattr(doc, "basis_text") else ""
    if basis:
        facts.append("Основание: " + basis)
    if doc.get("situation"):
        facts.append("Обстоятельства: " + doc.get("situation"))
    # v33 (замечание): строку «Доказательства:» в бланке не печатаем вовсе.
    if doc.get("extra"):
        facts.append("Дополнительно: " + doc.get("extra"))
    if facts:
        y += 10
        for fact in facts:
            para(fact, gap=14)

    pages.append(page)

    total = len(pages)
    for index, page_item in enumerate(pages, 1):
        draw_item = ImageDraw.Draw(page_item)
        _draw_footer(page_item, draw_item, doc, assets, index, total, index == total)
    return pages


def save_pages(pages: list, target_dir: Path, stem: str) -> list:
    """Сохраняет страницы в PNG. Одна страница — `<stem>.png`, много — с номерами."""
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    written = []
    if len(pages) == 1:
        path = target_dir / f"{stem}.png"
        pages[0].convert("RGB").save(path, "PNG", dpi=(DPI, DPI))
        return [path]
    for index, page in enumerate(pages, 1):
        path = target_dir / f"{stem} — стр.{index}.png"
        page.convert("RGB").save(path, "PNG", dpi=(DPI, DPI))
        written.append(path)
    return written


def render_to_png(doc, target_dir: Path, stem: str, assets: DocAssets | None = None) -> list:
    return save_pages(render_document(doc, assets), target_dir, stem)
