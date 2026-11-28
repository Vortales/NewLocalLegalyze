"""doc_layout.py — нумерация постановлений и раскладка папок (v66).

Основание: передача `Legalyze_v66_handoff` (согласованные требования) и ответы
пользователя на шесть вопросов:

* `suffix_everywhere` — номер этапа печатается и ВНУТРИ PNG (строка «Номер:»),
  и в именах файлов и папок;
* `chain_subfolder` — варианты цепочки хранятся раздельно: подпапка цепочки
  внутри папки общего номера;
* `v66_only` — новое правило действует только для обращений, созданных в v66;
  старые дела живут в прежних папках (`AG-1`, `001`) и не переименовываются;
* `forbid` — занять общий номер другого обращения нельзя: клиент показывает
  занятые номера и предлагает следующий свободный;
* `freeze` — индекс этапа фиксируется при первом создании документа и не
  пересчитывается, если план потом изменили; новые шаги получают следующие
  свободные индексы;
* по цепочкам — объективный минимум: вариант может быть один.

Согласованный вид (передача, раздел 3)::

    Обращение №1/
    └── 0001/                                  общий номер
        ├── Этап 1 — принятие/
        │   ├── 0001-1 — Этап 1 — стр. 1.png
        │   ├── 0001-1 — Этап 1 — стр. 2.png
        │   └── Архив/
        │       └── Версия-001/
        │           ├── 0001-1 — Этап 1 — стр. 1.png
        │           ├── ответ ИИ, поля и заявка этой версии
        └── Цепочка A/
            └── Этап 2.1 — истребование/
                ├── 0001-2 — Этап 2.1 — стр. 1.png
                └── Архив/

Модуль не зависит от Qt и браузера: всё проверяется тестами.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

#: Признак дела, созданного в v66: для него действует новая нумерация.
NEW_NUMBERING = "v66"

#: Общий номер обращения — минимум четыре цифры.
BASE_MIN_WIDTH = 4

#: Что переносится в папку версии при повторном «Сформировать».
VERSION_FILES = (
    "*.png",
    "ИИ-документ.txt",
    "ИИ-ответ.txt",
    "ИИ-поля.json",
    "Заявка (Сформировать).txt",
    "Заявка-поля.json",
    "Обращение.txt",
    "Проверка.txt",
)


# --------------------------------------------------------------------------- #
#  Общий (базовый) номер обращения
# --------------------------------------------------------------------------- #

def uses_new(case) -> bool:
    """Действует ли для дела новая нумерация (решение `v66_only`)."""
    return str(getattr(case, "numbering", "") or "") == NEW_NUMBERING


def normalize_base(text) -> str:
    """Общий номер к согласованному виду: «1» → «0001», «0012» → «0012».

    Всё, что не является числом (старые `AG-1`, пустое), даёт пустую строку:
    такие номера живут по прежним правилам.
    """
    raw = str(text or "").strip()
    match = re.fullmatch(r"0*(\d{1,6})", raw)
    if not match:
        return ""
    return str(int(match.group(1))).zfill(BASE_MIN_WIDTH)


def is_base(number: str) -> bool:
    """Похоже ли значение на общий номер нового образца."""
    return bool(normalize_base(number))


def suggest_base(used) -> str:
    """Следующий свободный общий номер: 0001, 0002, … (решение `forbid`)."""
    taken = {normalize_base(item) for item in (used or ())}
    taken.discard("")
    candidate = 1
    while str(candidate).zfill(BASE_MIN_WIDTH) in taken:
        candidate += 1
    return str(candidate).zfill(BASE_MIN_WIDTH)


def base_is_free(number, used) -> bool:
    """Не занят ли общий номер другим обращением."""
    base = normalize_base(number)
    if not base:
        return False
    return base not in {normalize_base(item) for item in (used or ())}


def occupied_bases(used) -> list:
    """Занятые общие номера по возрастанию — показать прокурору."""
    return sorted({normalize_base(item) for item in (used or ())} - {""})


# --------------------------------------------------------------------------- #
#  Индекс этапа и номер постановления
# --------------------------------------------------------------------------- #

def node_step(kind) -> int:
    """Номер подэтапа для «2.N» (иначе 0)."""
    text = str(kind or "").strip()
    if text.startswith("2."):
        try:
            return int(text.split(".", 1)[1])
        except (ValueError, IndexError):
            return 0
    return 0


def canonical_index(kind, plan_size: int) -> int:
    """Индекс этапа по каноническому порядку (до фиксации).

    Этап 1 — 1; подэтап 2.N — 1 + N; этап 3 — за последним подэтапом. Для
    плана из двух подэтапов получается согласованный набор 1, 2, 3, 4.
    """
    text = str(kind or "").strip()
    step = node_step(text)
    if step:
        return 1 + step
    if text == "3":
        return max(2, int(plan_size or 0) + 2)
    return 1


def node_index(index_map, kind, plan_size: int = 0) -> int:
    """Индекс этапа: зафиксированный или новый свободный (решение `freeze`).

    `index_map` — сохранённое в деле соответствие {узел: индекс}. Уже
    выданный индекс не пересчитывается: если план изменили (добавили, убрали
    или переставили подэтапы), старые документы остаются под своими номерами,
    а новый узел получает первый свободный.
    """
    table = {str(key): int(value)
             for key, value in dict(index_map or {}).items()
             if str(key) and int(value or 0) > 0}
    kind = str(kind or "").strip()
    if kind in table:
        return table[kind]
    wanted = canonical_index(kind, plan_size)
    taken = set(table.values())
    if wanted not in taken:
        return wanted
    free = 1
    while free in taken:
        free += 1
    return free


def document_number(base, index) -> str:
    """Номер постановления этапа: «0001-2»."""
    base = normalize_base(base)
    if not base or not int(index or 0):
        return ""
    return f"{base}-{int(index)}"


# --------------------------------------------------------------------------- #
#  Папки и имена файлов
# --------------------------------------------------------------------------- #

def _safe(text, fallback: str = "") -> str:
    """Имя для файловой системы Windows: без «\\/:*?"<>|» и управляющих."""
    raw = str(text or "").strip()
    raw = re.sub(r"[\\/:*?\"<>|\r\n\t]", " ", raw)
    raw = re.sub(r"\s+", " ", raw).strip(" .")
    return raw or fallback


def chain_dir_name(letter) -> str:
    """Подпапка варианта цепочки: «Цепочка A» (решение `chain_subfolder`)."""
    text = _safe(letter).upper()
    return f"Цепочка {text}" if text else ""


def stage_dir_name(kind, step_label: str = "") -> str:
    """Имя папки этапа: «Этап 1», «Этап 2.1 — истребование», «Этап 3»."""
    text = str(kind or "").strip()
    step = node_step(text)
    if step:
        label = _safe(step_label)
        name = f"Этап 2.{step}"
        return f"{name} — {label}" if label else name
    if text == "3":
        label = _safe(step_label)
        return f"Этап 3 — {label}" if label else "Этап 3"
    label = _safe(step_label)
    return f"Этап 1 — {label}" if label else "Этап 1"


def case_dir(root, case_number, base) -> Path:
    """Папка общего номера: `<корень>/Обращение №1/0001`."""
    number = _safe(case_number, "1")
    return Path(root) / f"Обращение №{number}" / _safe(base, "б-н")


def stage_dir(root, case_number, base, kind, letter: str = "",
              step_label: str = "", create: bool = True) -> Path:
    """Папка этапа (с подпапкой цепочки для этапа 2)."""
    target = case_dir(root, case_number, base)
    chain = chain_dir_name(letter) if node_step(kind) else ""
    if chain:
        target = target / chain
    target = target / stage_dir_name(kind, step_label)
    if create:
        target.mkdir(parents=True, exist_ok=True)
    return target


def stage_caption(kind) -> str:
    """Короткая подпись этапа для имени файла: «Этап 1», «Этап 2.1», «Этап 3».

    Метка по существу («принятие», «истребование») входит в ИМЯ ПАПКИ; в имени
    файла остаётся только номер и этап — ровно как в согласованной схеме:
    `0001-1 — Этап 1 — стр. 1.png`.
    """
    text = str(kind or "").strip()
    step = node_step(text)
    if step:
        return f"Этап 2.{step}"
    if text == "3":
        return "Этап 3"
    return "Этап 1"


def file_stem(number, kind, step_label: str = "") -> str:
    """Имя документа без страниц: «0001-2 — Этап 2.1».

    `step_label` принят для совместимости вызовов, но в имя файла не попадает:
    согласованный вид — `0001-2 — Этап 2.1 — стр. 1.png`, а метка подэтапа
    («истребование») живёт в имени папки.
    """
    return _safe(f"{number} — {stage_caption(kind)}", "постановление")


def page_name(stem, page: int) -> str:
    """Имя страницы PNG: «0001-1 — Этап 1 — стр. 1.png»."""
    return f"{_safe(stem, 'постановление')} — стр. {int(page)}.png"


# --------------------------------------------------------------------------- #
#  Архив версий
# --------------------------------------------------------------------------- #

def archive_dir(stage: Path) -> Path:
    return Path(stage) / "Архив"


def existing_versions(stage: Path) -> list:
    """Список папок версий по возрастанию номера."""
    root = archive_dir(stage)
    if not root.is_dir():
        return []
    found = []
    for item in root.iterdir():
        match = re.fullmatch(r"Версия-(\d+)", item.name)
        if match and item.is_dir():
            found.append((int(match.group(1)), item))
    return [path for _n, path in sorted(found)]


def next_version_dir(stage: Path) -> Path:
    """Следующая свободная папка версии: «Архив/Версия-001»."""
    numbers = [int(re.fullmatch(r"Версия-(\d+)", path.name).group(1))
               for path in existing_versions(stage)]
    index = (max(numbers) + 1) if numbers else 1
    return archive_dir(stage) / f"Версия-{index:03d}"


def _current_files(stage: Path) -> list:
    """Файлы действующей версии (то, что переносится в архив)."""
    stage = Path(stage)
    if not stage.is_dir():
        return []
    found: list = []
    for pattern in VERSION_FILES:
        found.extend(sorted(stage.glob(pattern)))
    return [path for path in found if path.is_file()]


def snapshot_current(stage: Path, version_dir: Path | None = None) -> Path | None:
    """Безопасный снимок действующей версии в `Архив/Версия-NNN`.

    Требование пользователя: «пока новая версия не создана успешно,
    действующий PNG не удалять». Поэтому сначала КОПИРУЕМ, сверяем размер и
    только потом убираем оригинал; ошибка на любом шаге оставляет действующий
    комплект на месте.
    """
    stage = Path(stage)
    files = _current_files(stage)
    if not files:
        return None
    target = Path(version_dir) if version_dir else next_version_dir(stage)
    target.mkdir(parents=True, exist_ok=True)
    copied = []
    for path in files:
        try:
            destination = target / path.name
            shutil.copy2(path, destination)
            if destination.stat().st_size != path.stat().st_size:
                raise OSError(f"размер не совпал: {destination}")
            copied.append((path, destination))
        except Exception:
            # откатываем то, что успели скопировать, и оставляем оригиналы
            for _src, done in copied:
                try:
                    done.unlink()
                except Exception:
                    pass
            return None
    for path, _destination in copied:
        try:
            path.unlink()
        except Exception:
            return None
    return target


def archive_matches(stage: Path, version: Path) -> bool:
    """В папке версии лежат все файлы снятой версии (проверка для тестов)."""
    version = Path(version)
    if not version.is_dir():
        return False
    return bool(list(version.glob("*.png")))


def pages_leftover(stage: Path, expected: int) -> list:
    """Лишние страницы старой версии, если их число изменилось.

    Требование: «не оставляйте страницу старой многостраничной версии среди
    страниц новой». Возвращает страницы с номером больше `expected`.
    """
    stage = Path(stage)
    extra = []
    for path in sorted(stage.glob("*.png")):
        match = re.search(r"— стр\. (\d+)\.png$", path.name)
        if match and int(match.group(1)) > int(expected or 0):
            extra.append(path)
    return extra
