"""postanovlenie_data.py — данные постановления и разбор ответа ИИ (v27).

Задача новой версии: пользователь заполняет форму, программа формирует
структурированный .txt, прикрепляет его в чат, ИИ отвечает строго в рамках
постановления и помечает ответ триггером. Этот модуль отвечает за три вещи:

1. Модель данных постановления (`Postanovlenie`) — единый словарь полей,
   который понимают и форма, и .txt, и рендер в PNG.
2. Сериализация в .txt «как документ» и обратно (для истории и правок).
3. Разбор ответа ИИ по триггерам: `===ПОСТАНОВЛЕНИЕ===`, `===КОНЕЦ ОТВЕТА===`,
   поля `Ключ: значение`, нумерованные пункты постановляющей части.

Модуль намеренно не зависит от PyQt: его можно проверять автотестами и
переиспользовать в консольных инструментах.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path

# --------------------------------------------------------------------------- #
#  Триггеры. Их имена — часть контракта с промтом (см. ПРОМТ_ПОСТАНОВЛЕНИЯ.md)
# --------------------------------------------------------------------------- #

TRIGGER_DOC_START = "===ПОСТАНОВЛЕНИЕ==="
TRIGGER_DOC_END = "===КОНЕЦ ПОСТАНОВЛЕНИЯ==="
TRIGGER_ANSWER_END = "===КОНЕЦ ОТВЕТА==="
#: Имя вложенного файла, по которому промт понимает «это заявка на постановление».
DOC_MARKER_NAME = "ПОСТАНОВЛЕНИЕ"
DOC_MARKER_SUFFIX = ".txt"

#: Поля шаблона (Шаблон.txt) — используются для автозаполнения формы.
TEMPLATE_FIELDS = ("Name", "ID", "Fraction", "Rang", "Department", "JobTitle")

#: Единый список полей постановления: (ключ, подпись в форме, подпись в .txt).
DOC_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("vedomstvo", "Ведомство", "Ведомство"),
    ("podrazdelenie", "Подразделение", "Подразделение"),
    ("doc_type", "Вид документа", "Вид документа"),
    ("doc_title", "Заголовок документа", "Заголовок"),
    ("doc_number", "Номер документа", "Номер"),
    ("doc_date", "Дата документа", "Дата"),
    ("author_name", "Кто выносит (И. Фамилия)", "Постановление вынес"),
    ("author_rank", "Звание / должность", "Должность"),
    ("subject_name", "Кому (И. Фамилия)", "В отношении"),
    ("subject_id", "Удостоверение №", "Удостоверение"),
    ("subject_rank", "Звание сотрудника", "Звание сотрудника"),
    ("subject_department", "Отдел сотрудника", "Отдел сотрудника"),
    ("violation_act", "Нарушенный акт", "Нарушенный акт"),
    ("violation_clause", "Пункт / статья / часть", "Пункт нарушения"),
    # v33: нормативное основание шире одного устава — процессуальные нормы,
    # УК, иные акты «в части касающейся».
    ("legal_basis", "Нормативное основание", "Нормативное основание"),
    ("penalty", "Взыскание", "Взыскание"),
    ("situation", "Обстоятельства", "Обстоятельства"),
    # v33 (замечание): строки «Доказательства» в бланке больше нет.
    ("extra", "Дополнительно", "Дополнительно"),
)

FIELD_LABELS = {key: label for key, _form, label in DOC_FIELDS}
FORM_LABELS = {key: form for key, form, _label in DOC_FIELDS}

#: Виды постановлений, которые форма умеет собирать уже сейчас.
#: Универсальность на будущее: сюда добавляются жалобы/иски/обращения.
DOC_TYPES = (
    "Постановление о выдаче дисциплинарного взыскания",
    "Постановление о снятии дисциплинарного взыскания",
    "Постановление о проведении внутренней проверки",
    "Постановление об отстранении от службы",
)

#: Взыскания по Уставу LSCSD, гл. IV, п. 4.1 (см. data/reestr).
PENALTIES = (
    "предупреждение",
    "устный выговор",
    "строгий выговор",
    "переаттестация",
    "понижение",
    "увольнение",
)

#: Родительный падеж для формулировки «в виде …» (документ пишется по-русски,
#: поэтому «в виде строгого выговора», а не «в виде строгий выговор»).
PENALTY_GENITIVE = {
    "предупреждение": "предупреждения",
    "устный выговор": "устного выговора",
    "строгий выговор": "строгого выговора",
    "переаттестация": "переаттестации",
    "понижение": "понижения",
    "увольнение": "увольнения",
}

#: Начала формулировок нарушения, которые в постановлении идут в родительном
#: падеже: «в связи с нарушением пункта 19) части 1 статьи «а»».
_CLAUSE_GENITIVE = (
    ("пункт ", "пункта "),
    ("часть ", "части "),
    ("статья ", "статьи "),
    ("статьи ", "статьи "),
    ("подпункт ", "подпункта "),
    ("раздел ", "раздела "),
)


def penalty_instrumental(penalty: str) -> str:
    """«строгий выговор» -> «строгого выговора» (для фразы «в виде …»).

    Возвращает "" для пустых значений и для «заглушек» вида «дисциплинарное
    взыскание»: в документе нельзя писать «взыскание в виде дисциплинарного
    взыскания» — нужен конкретный вид из перечня п. 4.1 Устава LSCSD.
    """
    text = _clean(penalty).lower().strip(" .")
    if not text:
        return ""
    if text in PENALTY_GENITIVE:
        return PENALTY_GENITIVE[text]
    for nominative, genitive in PENALTY_GENITIVE.items():
        if text.startswith(nominative):
            return genitive + text[len(nominative):]
    # «дисциплинарное взыскание», «взыскание», «наказание» — не вид взыскания
    if "взыскани" in text or "наказани" in text:
        return ""
    return _clean(penalty)


#: v33: ориентиры тяжести (Устав LSCSD, гл. III–IV). Нужны, чтобы взыскание
#: не сводилось к «строгому выговору» там, где проступок легче или тяжелее.
#: Ключ — вид взыскания, значение — слова-признаки проступка.
PENALTY_HINTS = (
    ("предупреждение",
     ("опоздал", "опоздание", "незначительн", "впервые", "мелк", "небрежн",
      "незначительный", "устное замечание", "формальн")),
    ("устный выговор",
     ("нарушил форму одежды", "дресс-код", "дресс код", "не выполнил приказ",
      "пререкани", "грубость", "халатн")),
    ("строгий выговор",
     ("систематическ", "повторн", "грубое нарушение", "оскорбл", "неуважени",
      "субординац", "отказ выполнять приказ")),
    ("переаттестация",
     ("несоответств", "квалификац", "профпригодн", "не сдал", "аттестац")),
    ("понижение",
     ("превышение полномочий", "халатность", "сокрытие", "фальсификац",
      "злоупотребл")),
    ("увольнение",
     ("тяжк", "особо тяжк", "преступлен", "коррупц", "взятк", "применение силы",
      "незаконное задержание", "угроз")),
)


def suggest_penalty(situation: str) -> str:
    """Подсказывает вид взыскания по описанию проступка (v33).

    Пользователь просил не ограничиваться выговорами: если обстоятельства
    описывают тяжёлый проступок — ориентир на увольнение/понижение, если
    незначительный — предупреждение. Это лишь ОРИЕНТИР для ИИ и для формы:
    окончательное решение всё равно за ИИ (он видит устав), а пользователь
    может выбрать любое взыскание из перечня п. 4.1.
    """
    text = _clean(situation).lower()
    if not text:
        return ""
    best, best_score = "", 0
    for penalty, markers in PENALTY_HINTS:
        score = sum(1 for marker in markers if marker in text)
        if score > best_score:
            best, best_score = penalty, score
    return best


#: Слова, которыми в уставе обозначены виды взысканий (для разбора перечня
#: «[ Строгий выговор / Понижение / Увольнение ]»).
_PENALTY_CANON = (
    ("строгий выговор", "строгий выговор"),
    ("устный выговор", "устный выговор"),
    ("переаттестац", "переаттестация"),
    ("предупрежд", "предупреждение"),
    ("понижен", "понижение"),
    ("увольнен", "увольнение"),
)


def parse_allowed_penalties(text: str) -> list:
    """Перечень допустимых взысканий из пункта устава: «… [ A / B / C ]».

    Устав LSCSD у каждого пункта главы VIII прямо указывает вилку наказаний,
    например «8.1 … [ Строгий выговор / Понижение / Увольнение ]». Именно по
    этой вилке и нужно проверять применимость взыскания (замечание анализа).
    """
    raw = str(text or "")
    if "[" not in raw or "]" not in raw:
        return []
    inside = raw[raw.index("[") + 1: raw.rindex("]")].lower()
    allowed = []
    for marker, canon in _PENALTY_CANON:
        if marker in inside and canon not in allowed:
            allowed.append(canon)
    return allowed


def _clause_numbers(clause: str) -> list:
    """Номера пунктов устава из строки: «пункты 8.1 и 8.24» -> [8.1, 8.24]."""
    return re.findall(r"\b(\d{1,2}\.\d{1,2})\b", str(clause or ""))


def allowed_penalties_for(clause: str, regulation) -> list:
    """Какие взыскания допускает устав за указанные пункты (пересечение)."""
    if regulation is None:
        return []
    numbers = _clause_numbers(clause)
    if not numbers:
        return []
    allowed_per_clause = []
    for number in numbers:
        for text in getattr(regulation, "articles", {}).values():
            if str(text).strip().startswith(number):
                allowed = parse_allowed_penalties(text)
                if allowed:
                    allowed_per_clause.append(set(allowed))
                break
    if not allowed_per_clause:
        return []
    common = set.intersection(*allowed_per_clause)
    # Порядок — как в уставе (п. 4.1): от мягкого к строгому.
    return [name for name in ("предупреждение", "устный выговор", "строгий выговор",
                              "переаттестация", "понижение", "увольнение")
            if name in common]


def normalize_penalty(penalty: str) -> str:
    """Приводит взыскание к виду из перечня п. 4.1 Устава LSCSD.

    Понимает падежи и вольные формулировки: «строгого выговора» -> «строгий
    выговор», «устный» -> «устный выговор». Неизвестное значение не
    выдумывает — возвращает пустую строку, и тогда пункт строится без него.
    """
    text = _clean(penalty).lower().strip(" .")
    if not text or "взыскани" in text or "наказани" in text:
        return ""
    for nominative, genitive in PENALTY_GENITIVE.items():
        if text in (nominative, genitive) or text.startswith(nominative):
            return nominative
    if text.startswith("строг"):
        return "строгий выговор"
    if text.startswith("устн"):
        return "устный выговор"
    if text.startswith("выговор"):
        return "строгий выговор"
    if text.startswith("уволь"):
        return "увольнение"
    if text.startswith("пониж"):
        return "понижение"
    if text.startswith("переаттест"):
        return "переаттестация"
    if text.startswith("предупрежд"):
        return "предупреждение"
    return ""


def clean_position(rank: str) -> str:
    """Оставляет только должность внутри фракции.

    Замечание: «Куратор отдела IAD (Assistent sheriff), Сенатор» — в документе
    должна быть только должность во фракции, без внешних статусов (Сенатор,
    Губернатор, Адвокат и т. п.).
    """
    text = _clean(rank)
    if not text:
        return ""
    # Отрезаем «, Сенатор», «; Губернатор» и т. п. после запятой, если это
    # не часть названия должности в скобках.
    depth = 0
    cut = len(text)
    for index, char in enumerate(text):
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char in ",;" and depth == 0:
            cut = index
            break
    head = text[:cut].strip()
    if not head:
        return text
    return head


def normalize_clause(clause: str, act: str = "") -> str:
    """Убирает дублирование слова «устав», когда акт назван отдельно.

    Пользователь может написать в пункте «пункт 3.1 устава», а в акте —
    «Внутреннего устава Los Santos County Sheriff Department». В документе
    это давало «…нарушением пункта 3.1 устава Внутреннего устава …».
    """
    text = _clean(clause)
    if not text:
        return ""
    if "устав" in _clean(act).lower():
        text = re.sub(r"\s*,?\s*устава?\s*$", "", text, flags=re.IGNORECASE)
    return text.strip(" ,")


_VAGUE_CLAUSE = re.compile(
    r"^(по обстоятельствам|согласно обстоятельствам|по уставу|по заявке|"
    r"по указанным обстоятельствам|не указан|не установлен)",
    re.IGNORECASE,
)


#: Прилагательные перед названием акта: «Уголовный кодекс» -> «Уголовного кодекса».
_ADJ_GENITIVE = (
    ("ый ", "ого "), ("ий ", "его "), ("ой ", "ого "), ("ый", "ого"),
)


def act_genitive(act: str) -> str:
    """«Устав Los Santos» -> «Устава Los Santos» (после «нарушением …»)."""
    text = _clean(act)
    if not text:
        return ""
    low = text.lower()
    for nominative, genitive in (("устав ", "Устава "), ("кодекс ", "Кодекса "),
                                 ("закон ", "Закона "), ("регламент ", "Регламента ")):
        if low.startswith(nominative):
            return genitive + text[len(nominative):]
    # v38: ИИ присылает «Уголовный кодекс штата Сан-Андреас» — после
    # «образуют нарушение …» нужен родительный падеж («Уголовного кодекса»).
    words = text.split()
    if len(words) > 1:
        for index, word in enumerate(words[:-1]):
            if word.lower() in ("кодекс", "устав", "закон", "регламент"):
                head = words[index].lower()
                genitive = {"кодекс": "кодекса", "устав": "устава",
                            "закон": "закона", "регламент": "регламента"}[head]
                if words[index][:1].isupper():
                    genitive = genitive[:1].upper() + genitive[1:]
                before = words[:index]
                if before:
                    adjective = before[-1]
                    for nominative, ending in _ADJ_GENITIVE:
                        if adjective.lower().endswith(nominative):
                            before[-1] = adjective[:-len(nominative)] + ending
                            break
                words[index] = genitive
                return " ".join(before + words[index:])
    return text


def shorten_clause(clause: str) -> str:
    """«Пункт 8.5 Устава LSCSD — нарушение дресс-кода» -> «пункт 8.5».

    В выгрузке v37 ИИ прислал пункт вместе с расшифровкой и сокращением
    устава («Устава LSCSD»), а рядом печаталось полное название акта —
    получалось «… своего отдела Устава Los Santos County Sheriff Department».
    Пояснение сохраняем в скобках, название акта оставляем отдельному полю.
    """
    text = _clean(clause)
    if not text:
        return ""
    # «пункт 19) части 1 статьи а» — это не «пункт 19.», а ссылка на статью:
    # такие формулировки оставляем как есть.
    if re.search(r"\d\s*\)", text) or "части" in text.lower() or "стать" in text.lower():
        return text
    found = re.match(r"(?:пункт[аы]?\s*|ст\.?\s*|стать[яи]\s*)?(\d+(?:\.\d+)+)",
                     text, re.IGNORECASE)
    if not found:
        return text
    tail = text[found.end():].lstrip(" )—-,")
    # Расшифровку оставляем, только если она поясняет суть пункта, а не
    # повторяет название устава или акта.
    tail = re.sub(r"^(устава?|закона?|кодекса?)\b[^-—]*", "", tail,
                  flags=re.IGNORECASE).strip(" —-,")
    if len(tail) < 4 or re.fullmatch(r"(устава?|закона?|кодекса?)", tail, re.IGNORECASE):
        return f"пункт {found.group(1)}"
    return f"пункт {found.group(1)} ({tail.strip(' .')})"


def is_vague_clause(clause: str) -> bool:
    """Пункт/статья — заглушка вида «по обстоятельствам, указанным в заявке»."""
    return bool(_VAGUE_CLAUSE.match(_clean(clause)))


#: Разделители перечисления норм в поле «Пункт нарушения».
_CLAUSE_SPLIT = re.compile(r"\s*;\s*|,\s*(?=(?:часть|част[аи]|статья|статьи|"
                           r"пункт|пункта|ст\.|ч\.|п\.)\s)", re.IGNORECASE)


def clause_genitive(clause: str) -> str:
    """«пункт 19) части 1 статьи а» -> «пункта 19) части 1 статьи а»."""
    text = _clean(clause)
    # v38: ИИ перечисляет нормы через запятую — «части 2 статьи 12.8, часть 2
    # статьи 15.1». Падеж нужен КАЖДОЙ части, иначе в документе остаётся
    # «образуют нарушение: части 2 статьи 12.8, часть 2 статьи 15.1».
    pieces = [piece for piece in _CLAUSE_SPLIT.split(text) if piece.strip()]
    if len(pieces) > 1:
        return ", ".join(clause_genitive(piece) for piece in pieces)
    low = text.lower()
    for nominative, genitive in _CLAUSE_GENITIVE:
        if low.startswith(nominative):
            return genitive + text[len(nominative):]
    # v37: ИИ прислал «нарушением 8.5 Устав …» — без слова «пункт» фраза
    # читается как ошибка. Голый номер устава получает нужный падеж.
    if re.match(r"^\d+(\.\d+)+", text):
        return f"пункта {text}"
    return text

#: Поля, которые разрешено оставлять пустыми: их подбирает ИИ по обстоятельствам
#: (замечание v27: «вся информация будет браться из Обстоятельств»).
OPTIONAL_GUESSED_FIELDS = (
    "violation_act", "violation_clause", "legal_basis", "penalty",
    "subject_rank", "subject_department", "extra",
)

#: Ведомство по умолчанию — то, для которого в папке лежат герб и печать.
DEFAULT_VEDOMSTVO = "LOS SANTOS COUNTY SHERIFF'S DEPARTMENT"
DEFAULT_PODRAZDELENIE = "INTERNAL AFFAIRS DIVISION"
DEFAULT_STATE = "STATE OF SAN ANDREAS"


#: Номер удостоверения внутри ФИО: «#131485», «№ 131485», а также отдельное
#: число из 4+ цифр в конце («Marcus Nomad 85028»). Двузначные ранги и т. п.
#: не трогаем, чтобы не испортить имя.
_ID_IN_NAME = re.compile(r"(?:#|№)?\s*\b\d{4,}\b")


def split_subject(name: str, ident: str) -> tuple:
    """Разделяет «ФИО» и «удостоверение», если ИИ перепутал их местами.

    Реальный случай: в поле «В отношении» пришло «Aliya», а в «Удостоверение»
    — «Nomad». В документе это давало «сотруднику Aliya с номером удостоверения
    #Nomad». Правило: номер удостоверения — только цифры; всё буквенное,
    попавшее в это поле, — часть фамилии.
    """
    name = _clean(name)
    ident = _clean(ident).lstrip("#").strip()
    letters_in_id = re.findall(r"[^\d#\s][^#]*", ident)
    if letters_in_id and not re.search(r"\d{3,}", ident):
        tail = " ".join(part.strip() for part in letters_in_id if part.strip())
        if tail and tail.lower() not in name.lower():
            name = f"{name} {tail}".strip()
        ident = ""
    elif ident and not re.fullmatch(r"\d+", ident):
        # «№123456», «уд. 123456» и прочее — оставляем только цифры
        digits = re.sub(r"\D", "", ident)
        ident = digits
    found = _ID_IN_NAME.search(name)
    if found and not ident:
        ident = re.sub(r"\D", "", found.group(0))
    name = _ID_IN_NAME.sub("", name).strip(" ,;-")
    return name, ident


def initials_signature(full_name: str) -> str:
    """«Kirill Goldens» -> «K. Goldens» (подпись как в примерах IAD)."""
    parts = [p for p in str(full_name or "").replace(".", " ").split() if p]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0][:1].upper() + parts[0][1:]
    surname = parts[-1]
    return f"{parts[0][0].upper()}. {surname[:1].upper() + surname[1:]}"


def today_ru() -> str:
    return datetime.now().strftime("%d.%m.%Y")


#: Значения-заглушки, которые ИИ пишет вместо данных.
_PLACEHOLDERS = {
    "не указано", "не указан", "не указана", "не указаны", "нет данных",
    "неизвестно", "n/a", "нет", "-", "—", "отсутствует", "не установлено",
}


def _clean(value) -> str:
    text = str(value if value is not None else "").strip()
    if text.lower().strip(" .") in _PLACEHOLDERS:
        return ""
    return text


@dataclass
class Postanovlenie:
    """Одно постановление: поля формы + служебная история."""

    vedomstvo: str = DEFAULT_VEDOMSTVO
    podrazdelenie: str = DEFAULT_PODRAZDELENIE
    doc_type: str = DOC_TYPES[0]
    doc_title: str = ""
    doc_number: str = ""
    doc_date: str = field(default_factory=today_ru)
    author_name: str = ""
    author_rank: str = ""
    subject_name: str = ""
    subject_id: str = ""
    subject_rank: str = ""
    subject_department: str = ""
    violation_act: str = ""
    violation_clause: str = ""
    legal_basis: str = ""       # v33: УК / процессуальный кодекс / иные акты
    penalty: str = ""
    situation: str = ""
    #: Оставлено для чтения старых файлов; в бланк не печатается (v33).
    evidence: str = ""
    extra: str = ""
    #: Пункты постановляющей части. Если пусто — собираются автоматически.
    points: list = field(default_factory=list)
    #: Служебное: откуда взято (форма / ответ ИИ), когда создано.
    created_at: float = field(default_factory=time.time)
    source: str = "form"

    def __post_init__(self) -> None:
        """Единые правила применяются сразу при создании, а не «где-то потом».

        Иначе объект, собранный из формы/истории/ответа ИИ напрямую через
        конструктор, обходил бы чистку ФИО, должности и взыскания — именно так
        в v28 в документ попадали «Aliya» / «#Nomad», «Сенатор» и «взыскание в
        виде дисциплинарного взыскания».
        """
        self.subject_name, self.subject_id = split_subject(self.subject_name, self.subject_id)
        self.author_rank = clean_position(self.author_rank)
        cleaned_penalty = normalize_penalty(self.penalty)
        if cleaned_penalty:
            self.penalty = cleaned_penalty
        elif _clean(self.penalty).lower().strip(" .") in ("взыскание", "дисциплинарное взыскание",
                                                          "наказание"):
            self.penalty = ""
        self.doc_type = _clean(self.doc_type) or DOC_TYPES[0]

    # ------------------------------------------------------------------ утиль

    def get(self, key: str, default: str = "") -> str:
        value = _clean(getattr(self, key, default))
        if key == "author_rank":
            return clean_position(value)
        if key == "penalty":
            return normalize_penalty(value)
        return value

    def set(self, key: str, value) -> None:
        if not hasattr(self, key):
            return
        if key in ("subject_name", "subject_id"):
            # Правило одно на весь документ: ФИО — буквы, удостоверение — цифры.
            other = self.subject_id if key == "subject_name" else self.subject_name
            name, ident = split_subject(
                value if key == "subject_name" else self.subject_name,
                value if key == "subject_id" else other,
            )
            self.subject_name, self.subject_id = name, ident
            return
        if key == "author_rank":
            value = clean_position(value)
        elif key == "penalty":
            value = normalize_penalty(value)
        setattr(self, key, value)

    def clean_copy(self) -> "Postanovlenie":
        """Копия с уже применёнными правилами ФИО/должности/взыскания."""
        clone = Postanovlenie.from_dict(self.to_dict())
        clone.subject_name, clone.subject_id = split_subject(self.subject_name, self.subject_id)
        clone.author_rank = clean_position(self.author_rank)
        clone.penalty = normalize_penalty(self.penalty) or self.penalty
        return clone

    def signature(self) -> str:
        """Подпись «И. Фамилия» из поля «кто выносит»."""
        return initials_signature(self.author_name)

    def title(self) -> str:
        return self.doc_title or self.doc_type

    def document_title(self) -> str:
        """Полный заголовок для бланка.

        ИИ часто присылает «Заголовок: о выдаче дисциплинарного взыскания»
        (без слова «Постановление»), и на бланке получалось просто
        «о выдаче дисциплинарного взыскания». Если вида документа нет в
        заголовке — подставляем его.
        """
        title = self.title().strip()
        doc_type = self.get("doc_type").strip()
        if not title:
            return doc_type
        low_title = title.lower()
        prefix = doc_type.split()[0] if doc_type else "Постановление"
        if prefix.lower() not in low_title:
            # «Снятие дисциплинарного взыскания» -> «Постановление о снятии…»
            head = title[:1].lower() + title[1:]
            if head.startswith(("о ", "об ")):
                return f"{prefix} {head}"
            # Падеж меняем у ПЕРВОГО слова: «Снятие …» -> «о снятии …»,
            # «Проведение …» -> «о проведении …», «Отстранение …» -> «об отстранении …».
            first, _, rest = head.partition(" ")
            for ending, replacement in (("ание", "ании"), ("ение", "ении"),
                                        ("тие", "тии"), ("ие", "ии")):
                if first.endswith(ending):
                    first = first[: -len(ending)] + replacement
                    break
            joiner = "об " if first[:1] in "аоэиуыеёюя" else "о "
            return f"{prefix} {joiner}{first}{(' ' + rest) if rest else ''}"
        if title[:1].islower():
            return title[:1].upper() + title[1:]
        return title

    def missing_fields(self) -> list:
        """Обязательные поля, без которых документ не имеет смысла."""
        # v28: нарушение/взыскание определяет ИИ («по желанию для уточнения»),
        # поэтому обязательными остались только реквизиты и предмет документа.
        required = (
            "doc_number", "doc_date", "author_name", "author_rank",
            "subject_name", "subject_id",
        )
        return [FORM_LABELS[k] for k in required if not self.get(k)]

    def auto_number(self, existing: list | None = None, prefix: str = "LSCSD-IAD") -> str:
        """Номер вида LSCSD-IAD-140: следующий после максимального в истории."""
        top = 0
        for item in existing or []:
            m = re.search(r"(\d+)\s*$", _clean(item))
            if m:
                top = max(top, int(m.group(1)))
        return f"{prefix}-{top + 1}"

    def kind(self) -> str:
        """Класс документа по заголовку: выдача / снятие / проверка / отстранение."""
        text = f"{self.get('doc_type')} {self.get('doc_title')}".lower()
        if "снят" in text:
            return "removal"
        if "проверк" in text:
            return "inspection"
        if "отстран" in text:
            return "suspension"
        return "penalty"

    def _violation_phrase(self) -> str:
        """«нарушением пункта 8.5 Устава …» без заглушек «по обстоятельствам»."""
        clause = self.get("violation_clause")
        act = self.get("violation_act")
        clean_clause = "" if is_vague_clause(clause) else clause
        if clean_clause and act:
            return f"{clause_genitive(clean_clause)} {act_genitive(act)}"
        if clean_clause:
            return clause_genitive(clean_clause)
        if act:
            return f"требований {act_genitive(act)}"
        return ""

    def decision_points(self) -> list:
        """Постановляющая часть. Если ИИ не дал пунктов — собираем по Уставу."""
        points = [p for p in (self.points or []) if _clean(p)]
        if points:
            fixed = [_clean(p) for p in points]
            return self._align_points(fixed)
        subject = self.get("subject_name") or "сотруднику"
        subject_id = self.get("subject_id")
        who = f"{subject} с номером удостоверения #{subject_id}" if subject_id else subject
        if self.kind() == "removal":
            # Снятие: и первый пункт, и весь документ говорят одно и то же.
            penalty_gen = penalty_instrumental(self.get("penalty"))
            done = (f"Снять с сотрудника {who} ранее наложенное дисциплинарное "
                    f"взыскание в виде {penalty_gen}."
                    if penalty_gen else
                    f"Снять с сотрудника {who} ранее наложенное дисциплинарное взыскание.")
            return [
                done,
                "Настоящее постановление вступает в силу с момента его подписания.",
                "Ответственность за доведение настоящего постановления до сотрудника, а также "
                "контроль за исполнением настоящего документа оставляю за собой.",
            ]
        reference = self._violation_phrase()
        because = f", в связи с нарушением {reference}" if reference else ""
        penalty = penalty_instrumental(self.get("penalty"))
        # Конкретный вид взыскания обязателен: «в виде дисциплинарного
        # взыскания» — это не наказание, а его отсутствие (замечание v28).
        measure = f"дисциплинарное взыскание в виде {penalty}" if penalty \
            else "дисциплинарное взыскание"
        result = [
            f"Назначить сотруднику {who} {measure}{because}.",
            "Настоящее постановление вступает в силу с момента его подписания и подлежит "
            "исполнению в течение 24 часов с момента назначения.",
            "Ответственность за доведение настоящего постановления до сотрудника, а также "
            "контроль за исполнением настоящего документа оставляю за собой.",
        ]
        return result

    def _align_points(self, points: list) -> list:
        """Согласует пункты с видом документа.

        Реальный случай из замечаний: документ называется «о снятии
        дисциплинарного взыскания», а первый пункт — «Назначить … взыскание».
        Правим только противоречие первого пункта, остальные не трогаем.
        """
        kind = self.kind()
        penalty = self.get("penalty")
        penalty_gen = penalty_instrumental(penalty)
        first = points[0]
        low = first.lower()
        if kind == "removal" and ("назначить" in low or "наложить" in low):
            subject = self.get("subject_name")
            who = f"{subject} с номером удостоверения #{self.get('subject_id')}" \
                if self.get("subject_id") else subject
            points[0] = (f"Снять с сотрудника {who} ранее наложенное дисциплинарное "
                         f"взыскание в виде {penalty_gen}." if penalty
                         else f"Снять с сотрудника {who} ранее наложенное "
                              "дисциплинарное взыскание.")
            return points
        if kind == "penalty" and "снять" in low and penalty_gen:
            points[0] = re.sub(r"в виде [^.]+", f"в виде {penalty_gen}", first)
        if kind == "inspection" and ("назначить" in low or "снять" in low):
            points[0] = ("Назначить проведение внутренней проверки по изложенным "
                         "обстоятельствам.")
        if kind == "suspension" and ("назначить" in low or "снять" in low):
            points[0] = "Отстранить сотрудника от исполнения служебных обязанностей до окончания проверки."
        return points

    def preamble(self) -> str:
        """Вводная часть — ровно как в примерах IAD."""
        dept = self.get("vedomstvo") or DEFAULT_VEDOMSTVO
        name = self.get("author_name")
        rank = self.get("author_rank")
        podr = self.get("podrazdelenie") or DEFAULT_PODRAZDELENIE
        who = f"{dept} {name}".strip()
        # Если должность уже содержит подразделение («Начальник отдела
        # внутренних расследований»), не повторяем «отдела INTERNAL AFFAIRS
        # DIVISION» — фраза становится бессмысленной.
        low_rank = rank.lower()
        if not rank:
            position = f"отдела {podr}"
        elif "отдел" in low_rank or "division" in low_rank or "дивизион" in low_rank:
            position = ""
        else:
            position = f"отдела {podr}"
        position = f", занимающий должность {rank} {position}".rstrip() if position \
            else f", занимающий должность {rank}"
        return (
            f"Я, сотрудник {who}{position}, "
            "в рамках своих полномочий, предоставленных мне внутренним уставом и иными "
            "нормативно-правовыми актами, настоящим кодексом постановил:"
        )

    # ------------------------------------------------------- сериализация

    def to_dict(self) -> dict:
        data = asdict(self)
        data["points"] = list(self.points or [])
        return data

    @classmethod
    def from_dict(cls, data: dict | None) -> "Postanovlenie":
        data = data or {}
        allowed = {key for key, _f, _l in DOC_FIELDS} | {
            "points", "created_at", "source",
        }
        kwargs = {k: v for k, v in data.items() if k in allowed}
        doc = cls(**kwargs)
        doc.points = [_clean(p) for p in (data.get("points") or []) if _clean(p)]
        return doc

    #: .txt, который прикрепляется в чат: машиночитаемый блок + читаемый вид.
    def to_chat_txt(self) -> str:
        """Текст заявки, который уходит в чат.

        v30.3 (причина замечания «в PNG данные конструктора»): раньше заявка
        начиналась словом «ПОСТАНОВЛЕНИЕ» и заканчивалась «===КОНЕЦ ОТВЕТА===».
        Для клиента это выглядело как готовый ответ ИИ: он читал из чата СВОЁ
        же сообщение и печатал из него PNG. Теперь заявка — обычная просьба,
        без триггеров и без служебных пометок («сырые слова пользователя»,
        «определить по обстоятельствам»).
        """
        lines = ["ЗАЯВКА НА ПОСТАНОВЛЕНИЕ"]
        lines.append("Данные для подготовки документа:")
        for key, _form, label in DOC_FIELDS:
            value = self.get(key)
            if key == "situation" and value:
                lines.append(f"{label}: {value}")
            elif value:
                lines.append(f"{label}: {value}")
            elif key in OPTIONAL_GUESSED_FIELDS:
                # v28: если пользователь не заполнил нарушение/взыскание/звание,
                # ИИ должен определить их сам по обстоятельствам (промт 1.12).
                lines.append(f"{label}: нет данных — определи сам")
        if self.get("penalty") or self.get("violation_clause"):
            # Подсказка для ИИ: он всё равно перепишет пункты официально.
            lines.append("")
            lines.append("Предлагаемые пункты (перепиши официально):")
            for i, point in enumerate(self.decision_points(), 1):
                lines.append(f"{i}. {point}")
        lines.append("")
        # v30.3: буквальных триггеров в заявке НЕТ намеренно — иначе заявку
        # можно принять за готовый ответ ИИ (именно это и происходило).
        lines.append("Подготовь постановление строго по этим данным, в формате "
                     "промта: один структурированный блок от начала документа до "
                     "конца ответа, без описания вложений.")
        return "\n".join(lines) + "\n"

    def basis_text(self) -> str:
        """«Основание: …» — все применимые нормы, а не только устав (v33).

        Замечание: в бланке был только Устав LSCSD. Теперь сюда попадают и
        нормы Уголовного кодекса, процессуального кодекса и других актов
        («в части касающейся»), если ИИ их указал в «Нормативном основании».
        """
        parts = []
        raw_clause = self.get("violation_clause")
        clause = ("" if is_vague_clause(raw_clause)
                  else normalize_clause(raw_clause, self.get("violation_act")))
        clause = shorten_clause(clause)
        if clause and self.get("violation_act"):
            parts.append(f"{clause} {act_genitive(self.get('violation_act'))}".strip())
        elif clause:
            parts.append(clause)
        elif self.get("violation_act"):
            parts.append(self.get("violation_act"))
        basis = _clean(self.get("legal_basis"))
        if basis and basis.lower() not in " ".join(parts).lower():
            parts.append(basis)
        # Убираем дубли, сохраняя порядок: устав мог быть назван дважды.
        unique = []
        for part in parts:
            part = part.strip(" ;,")
            if part and part.lower() not in [u.lower() for u in unique]:
                unique.append(part)
        return "; ".join(unique)

    def to_document_txt(self) -> str:
        """Готовый бланк словами — ровно то, что печатается в PNG.

        Порядок блоков повторяет `doc_render.render_document()`: шапка,
        заголовок, вводная часть, ПОСТАНОВИЛ, основания. Нужен, чтобы можно
        было проверить документ до печати: `ИИ-документ.txt` == PNG.
        """
        lines = [
            (self.get("vedomstvo") or "LOS SANTOS COUNTY SHERIFF DEPARTMENT").upper(),
            (self.get("podrazdelenie") or "").upper(),
            "",
            self.document_title(),
            "",
        ]
        if self.get("doc_date"):
            lines.append(f"Дата: {self.get('doc_date')}")
        if self.get("doc_number"):
            lines.append(f"№ {self.get('doc_number')}")
        lines.append("")
        lines.append(self.preamble())
        lines.append("")
        lines.append("ПОСТАНОВИЛ:")
        lines.append("")
        for i, point in enumerate(self.decision_points(), 1):
            lines.append(f"{i}. {point}")
        lines.append("")
        facts = []
        basis = self.basis_text()
        if basis:
            facts.append("Основание: " + basis)
        if self.get("situation"):
            facts.append("Обстоятельства: " + self.get("situation"))
        if self.get("extra"):
            facts.append("Дополнительно: " + self.get("extra"))
        lines.extend(facts)
        lines.append("")
        if self.get("author_name"):
            lines.append(f"Подпись: {self.get('author_name')}")
            if self.get("author_rank"):
                lines.append(f"Должность: {self.get('author_rank')}")
        return "\n".join(lines).strip() + "\n"

    def chat_txt_name(self) -> str:
        num = self.get("doc_number") or "б-н"
        return f"{DOC_MARKER_NAME} {num}{DOC_MARKER_SUFFIX}"

    #: Снимок для истории: полный, чтобы можно было открыть и поправить.
    def history_payload(self) -> dict:
        data = self.to_dict()
        data["created_at"] = time.time()
        data["human_created"] = datetime.fromtimestamp(self.created_at).strftime(
            "%d.%m.%Y %H:%M"
        )
        return data


# --------------------------------------------------------------------------- #
#  Разбор ответа ИИ
# --------------------------------------------------------------------------- #

_ALIASES = {
    "номер": "doc_number",
    "№": "doc_number",
    "номер документа": "doc_number",
    "дата": "doc_date",
    "дата документа": "doc_date",
    "постановление вынес": "author_name",
    "вынес": "author_name",
    "должность": "author_rank",
    "звание": "author_rank",
    "в отношении": "subject_name",
    "сотрудник": "subject_name",
    "кому": "subject_name",
    "удостоверение": "subject_id",
    "удостоверение №": "subject_id",
    "id": "subject_id",
    "звание сотрудника": "subject_rank",
    "отдел сотрудника": "subject_department",
    "нарушенный акт": "violation_act",
    "акт": "violation_act",
    "пункт нарушения": "violation_clause",
    "пункт": "violation_clause",
    "статья": "violation_clause",
    "взыскание": "penalty",
    # v30: одно и то же поле в чате и в документе называется «Обстоятельства»,
    # но старые ответы ИИ («Ситуация») и вольные формулировки тоже понимаем.
    "обстоятельства": "situation",
    "обстоятельства дела": "situation",
    "ситуация": "situation",
    "ситуация / фабула": "situation",
    "фабула": "situation",
    "примечание": "extra",
    "примечания": "extra",
    # v33: нормативное основание — УК, процессуальный кодекс, иные акты.
    "нормативное основание": "legal_basis",
    "нормативное обоснование": "legal_basis",
    "правовое основание": "legal_basis",
    "правовое обоснование": "legal_basis",
    "основание": "legal_basis",
    "нормы": "legal_basis",
    "применимые нормы": "legal_basis",
    "статьи ук": "legal_basis",
    "нормы ук": "legal_basis",
    "нормы упк": "legal_basis",
    # «Доказательства» больше не печатаем, но старые ответы читаем без ошибок.
    "доказательства": "evidence",
    "дополнительно": "extra",
    "ведомство": "vedomstvo",
    "подразделение": "podrazdelenie",
    "вид документа": "doc_type",
    "заголовок": "doc_title",
}

_NUMBERED = re.compile(r"^\s*(?:п(?:ункт)?\s*)?(\d{1,2})[.)]\s+(.*)$")
_BULLET = re.compile(r"^\s*[-•*—]\s+(.*)$")

#: Строки интерфейса сервиса, которые попадают в текст вместе с ответом
#: (чипы вложений, кнопки «перейти», статусы «Режим ИИ обрабатывает…»).
#: Без этой чистки они уезжали в «Дополнительно» готового постановления —
#: ровно то, что видно в замечаниях к v27.
_UI_NOISE = re.compile(
    r"(перейти к (основному контенту|предыдущему запросу)"
    r"|справка по использованию специальных возможностей"
    r"|all items removed from input context"
    r"|режим ии обрабатывает"
    r"|^(pdf|file|файл|txt|png|docx)$"
    r"|^[^:]{1,80}\.(pdf|txt|png|jpe?g|docx|doc|json)$"
    r"|^шаблон\.txt$"
    r"|^заявка \(отправлено в$"
    r"|^ответ от режима ии"
    r"|^ответ в режиме ии"
    r"|^(mem|phis\.pdf|ша|блон\.txt)$"
    r"|^показать больше$|^копировать$|^поделиться$|^нравится$)",
    re.IGNORECASE,
)


#: Служебные фразы нашего же сообщения в чате (v37).
#:
#: Реальная ситуация: веб-клиент ИИ отдал в ответе ВЕСЬ текст чата, включая
#: наше сообщение «Сформировано постановление… НИ В КАКОМ РАЗДЕЛЕ не
#: используй внеигровые термины: «IC», «OOC»…». Этот текст ушёл в
#: «Обстоятельства» документа и в PNG, а проверка справедливо нашла там «IC».
#: Теперь строки-инструкции не считаются содержанием документа.
_CHAT_INSTRUCTION = re.compile(
    r"(сформировано постановление, файл заявки приложен"
    r"|работай только с текстом заявки"
    r"|не описывай изображения и вложения"
    r"|ответь строго по формату промта"
    r"|ни в каком разделе .{0,40}не используй внеигровые"
    r"|вместо «?без весомых ic причин»?"
    r"|вы отправили \d+ файл"
    r"|ответ в режиме ии, исходные файлы"
    r"|исходный запрос:"
    r"|прикрепил|приложен файл заявки)",
    re.IGNORECASE,
)

#: Строка целиком — служебная инструкция (начинается с повелительного глагола).
_CHAT_INSTRUCTION_START = re.compile(
    r"^(работай|ответь|не описывай|не разбирай|не пиши|используй|укажи|"
    r"перепиши|сформируй|подготовь|выбери|составь|выведи)\b",
    re.IGNORECASE,
)


def is_chat_instruction(line: str) -> bool:
    """Строка — инструкция из нашего сообщения в чате, а не текст документа."""
    text = _clean(line)
    if not text:
        return False
    if _CHAT_INSTRUCTION.search(text):
        return True
    return bool(_CHAT_INSTRUCTION_START.match(text))


def scrub_chat_text(text: str) -> str:
    """Убирает из ответа ИИ служебные строки чата (v37)."""
    kept = []
    for line in str(text or "").splitlines():
        if is_chat_instruction(line):
            continue
        kept.append(line)
    return "\n".join(kept)


def is_ui_noise(line: str) -> bool:
    """Строка — мусор интерфейса, а не часть постановления."""
    text = _clean(line)
    if not text:
        return True
    if len(text) <= 2:
        return True
    if is_chat_instruction(text):
        return True
    # v37: сначала явные признаки интерфейса («PDF», «File», «Заявка.txt») —
    # раньше проверка обрывков имени вложения срабатывала раньше и отвечала
    # «не мусор», из-за чего эти строки попадали в «Обстоятельства».
    if _UI_NOISE.search(text):
        return True
    # «Ша / блон.txt» — перенос имени вложения, «Мем / фис.pdf» и т. п.
    if re.fullmatch(r"[А-Яа-яЁёA-Za-z]{1,6}", text) and len(text) <= 6:
        return bool(re.search(r"[а-яё]{2,}", text)) and _looks_like_fragment(text)
    return False


def _looks_like_fragment(text: str) -> bool:
    """Короткий обрывок без пробелов рядом с именем файла — мусор чата."""
    return text.lower() in {"ша", "мем", "блон", "фи", "фис", "чат", "заяв", "заявк"}


_MD_WRAP = re.compile(r"^[\s>*_`#>\-•·\d.)\]]+")


def diag_event(name: str, **fields) -> None:
    """Событие в лог, если диагностика доступна (рендер работает и без неё)."""
    try:
        import diagnostics as _diag
        _diag.event(name, **fields)
    except Exception:
        pass


def strip_markdown(value: str) -> str:
    """Убирает markdown-разметку из значения, сохраняя текст.

    ИИ любит отвечать «- **Обстоятельства:** текст», «`#131485`», «*важно*».
    В документе звёздочки и обратные кавычки не нужны.
    """
    text = str(value or "")
    text = text.replace("**", "").replace("__", "").replace("`", "")
    text = re.sub(r"(?<![\w])[*_](?=\S)|(?<=\S)[*_](?![\w])", "", text)
    # Срезаем только «маркеры списка» и кавычки, НЕ трогая цифры и «#»:
    # «#131485» — это номер удостоверения, «3.1» — пункт устава.
    text = re.sub(r"^[\s>*_`•·\-—]+", "", text)
    text = text.replace("\u00a0", " ")
    return re.sub(r"[ \t]+", " ", text).strip()


def _normalize_key(raw: str) -> str:
    """Ключ поля из строки ответа, устойчивый к markdown.

    Реальный случай: ИИ прислал «- **Обстоятельства:** текст» — звёздочки не
    давали распознать поле, строка уходила в «свободный текст», и в документе
    появлялась подпись рендера вместо значения поля. Теперь разметка снимается
    до сравнения с алиасами.
    """
    key = _clean(raw).lower()
    key = key.replace("**", "").replace("__", "").replace("`", "")
    key = key.replace("«", "").replace("»", "").replace('"', "")
    key = re.sub(r"^[\s>*_`#>\-•·\d.)\]]+", "", key)
    key = key.replace("\u00a0", " ").rstrip(":").strip(" .")
    key = re.sub(r"\s+", " ", key)
    if key in _ALIASES:
        return _ALIASES[key]
    for label_key, form_label in FORM_LABELS.items():
        if key == form_label.lower():
            return label_key
    for field_key, _f, label in DOC_FIELDS:
        if key == label.lower():
            return field_key
    return ""


#: Признаки того, что ИИ вместо документа описывает изображение.
#: Реальный случай: клиент приложил .png постановления, ИИ ответил «На
#: изображении представлен официальный бланк…», и этот разбор уезжал в
#: «Обстоятельства» готового документа. Такой текст документом не считается.
_DESCRIPTION_MARKERS = (
    "на изображении", "на картинке", "на скриншоте", "на предоставленном изображении",
    "изображении представлен", "представлен официальный бланк", "вот подробный разбор",
    "разбор всего, что находится", "шапка документа", "основной текст постановления",
    "подпись и водяные знаки", "нижний левый угол", "нижний правый угол",
    "вероятно, для ролевого", "gta v rp", "samp", "водяной знак (герб",
)
#: Строки-«рубрики» описания: «📑 Шапка документа», «**✍️ Основной текст**».
_SECTION_HEADING = re.compile(
    r"^[\s>*_`#•·📑✍️🖋️📄🔹▪✅❌\-]*(шапка|основной текст|постановляющая часть|"
    r"подпись|водяные знаки|фотер|подвал|реквизиты|логотип|примечание|"
    r"исходящие данные|вводная часть)[^\n]{0,40}$",
    re.IGNORECASE,
)


def is_section_heading(line: str) -> bool:
    """Строка-рубрика описания картинки («📑 Шапка документа»), а не поле.

    Проверяем до ключей: если слева от двоеточия стоит известное имя поля
    («Примечание», «Обстоятельства»), это поле, а не заголовок раздела.
    """
    text = _clean(line)
    if not text:
        return False
    if ":" in text:
        head = _normalize_key(text.split(":", 1)[0])
        if head:
            return False
    return bool(_SECTION_HEADING.match(text)) and len(text) <= 60


def looks_like_image_description(text: str) -> bool:
    """Ответ ИИ — описание бланка на картинке, а не сам документ."""
    raw = str(text or "").lower()
    if not raw.strip():
        return False
    hits = sum(1 for marker in _DESCRIPTION_MARKERS if marker in raw)
    if hits >= 2:
        return True
    # одиночный сильный признак + отсутствие полей документа
    if hits >= 1 and not re.search(r"^\s*[*_`>-]*\s*(номер|дата|в отношении)\s*[:：]",
                                   raw, re.MULTILINE):
        return True
    return False


def count_document_fields(text: str) -> int:
    """Сколько НАСТОЯЩИХ полей постановления распознаёт парсер.

    v32: главный признак документа. Наше собственное сообщение в чате
    («…ответь одним блоком от ===ПОСТАНОВЛЕНИЕ=== до ===КОНЕЦ
    ПОСТАНОВЛЕНИЯ===…») содержит маркеры, но НИ ОДНОГО поля — именно на этом
    клиент ошибался: находил «блок», решал, что ответ готов, и выдавал
    «Бланк не сформирован», так и не дождавшись настоящего ответа ИИ.

    Считаем теми же ключами, что и разбор, — лишней логики не добавляем.
    """
    found = {key for key, _form, _label in DOC_FIELDS}
    count = 0
    for raw_line in str(text or "").splitlines():
        line = strip_markdown(raw_line.strip())
        if ":" not in line:
            continue
        key = _normalize_key(line.split(":", 1)[0])
        if key in found and strip_markdown(line.split(":", 1)[1]).strip():
            count += 1
    return count


def looks_like_document(text: str) -> bool:
    """Текст — постановление ИИ, а не служебная фраза про маркеры."""
    return count_document_fields(text) >= 2


def has_document_trigger(text: str) -> bool:
    """Есть ли в тексте НАСТОЯЩИЙ маркер постановления.

    v30.3: раньше проверялось вхождение слова «ПОСТАНОВЛЕНИЕ» в любом месте
    текста — под это подходила и сама заявка («ЗАЯВКА НА ПОСТАНОВЛЕНИЕ»,
    «Вид документа: Постановление …»), и обычная правовая формулировка.
    Поэтому заявка принималась за ответ ИИ, и PNG собирался из конструктора.

    Теперь нужен явный маркер: `===ПОСТАНОВЛЕНИЕ===` / `===КОНЕЦ
    ПОСТАНОВЛЕНИЯ===` — либо строка, начинающаяся с `ПОСТАНОВЛЕНИЕ` И
    содержащая двоеточие-поля. Просто слово в тексте не считается.
    """
    raw = str(text or "")
    # Пробелы, `*` и обратные кавычки вокруг маркера не важны:
    # «=== ПОСТАНОВЛЕНИЕ ===», «**===ПОСТАНОВЛЕНИЕ===**».
    compact = re.sub(r"[\s`*]+", "", raw.upper())
    if "===ПОСТАНОВЛЕНИЕ===" in compact or "КОНЕЦПОСТАНОВЛЕНИЯ" in compact:
        return True
    # Строка-маркер: «ПОСТАНОВЛЕНИЕ» как ЗАГОЛОВОК, а не «Постановление вынес: …».
    # Поэтому смотрим только на первое слово строки и не берём «вынес/вступает».
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Только когда «ПОСТАНОВЛЕНИЕ» стоит отдельным заголовком:
        # «ПОСТАНОВЛЕНИЕ», «ПОСТАНОВЛЕНИЕ: …», но НЕ «Постановление вынес: …».
        if re.match(r"^ПОСТАНОВЛЕНИЕ(\s*[:.]|\s*$)", line.upper()):
            return True
    return False


def has_answer_end(text: str) -> bool:
    compact = re.sub(r"[\s=*/\-_]+", "", str(text or "").upper())
    return "КОНЕЦОТВЕТА" in compact


def strip_triggers(text: str) -> str:
    out = str(text or "")
    for trigger in (TRIGGER_ANSWER_END, TRIGGER_DOC_START, TRIGGER_DOC_END):
        out = out.replace(trigger, "")
    return out.strip()


#: Служебные фразы, которые есть ТОЛЬКО в нашей заявке (ИИ их не пишет).
_REQUEST_ONLY_MARKS = (
    "заявка на постановление",
    "нет данных — определи сам",
    "предлагаемые пункты (перепиши официально)",
)


def _flat(text: str) -> str:
    return " ".join(str(text or "").split())


def is_own_request(text: str, request_text: str = "") -> bool:
    """Текст — это НАША ЖЕ заявка в чате, а не ответ ИИ.

    v30.3 (главная причина замечания): заявка начинается словом
    «ПОСТАНОВЛЕНИЕ» и заканчивается «===КОНЕЦ ОТВЕТА===», поэтому она
    выглядит для клиента как готовый ответ ИИ. Клиент опрашивал страницу и на
    первых опросах читал СВОЁ сообщение — из него собирался PNG (данные
    конструктора), а настоящий ответ ИИ приходил позже и только перезаписывал
    `ИИ-ответ.txt`. Отсюда и расхождение: файл верный, PNG — нет.
    """
    flat = _flat(text)
    if not flat:
        return False
    request = _flat(request_text)
    if request and flat == request:
        return True
    if request and request in flat and len(flat) <= len(request) + 240:
        # наше сообщение плюс, возможно, имя вложения/подпись
        return True
    low = flat.lower()
    # Страховка, если текст заявки недоступен: служебные пометки клиента.
    return sum(1 for mark in _REQUEST_ONLY_MARKS if mark in low) >= 1


def extract_document_block(text: str) -> str:
    """Вырезает из ответа ИИ только блок постановления.

    Реальная ситуация: ИИ пишет «На изображении представлен официальный
    документ…» и уже ПОСЛЕ этого выдаёт `===ПОСТАНОВЛЕНИЕ=== …`. Раньше
    детектор описания срабатывал на весь текст и документ отбрасывался —
    в PNG уходили данные формы, а не то, что ответил ИИ. Теперь описание до
    блока и после него просто не участвует в разборе.
    """
    raw = str(text or "")
    # v32: маркер маркером, но если настоящих полей в тексте нет — это не
    # документ, а служебная фраза («…ответь одним блоком от ===ПОСТАНОВЛЕНИЕ===
    # до ===КОНЕЦ ПОСТАНОВЛЕНИЯ===…»). Такой текст блоком не считается.
    if not looks_like_document(raw):
        return ""
    # v37: в ответ может попасть весь чат вместе с нашими инструкциями
    raw = scrub_chat_text(raw)
    upper = raw.upper()
    start = -1
    for marker in (TRIGGER_DOC_START, "===ПОСТАНОВЛЕНИЕ=="):
        pos = upper.find(marker.upper())
        if pos >= 0:
            start = pos
            break
    if start < 0:
        if not has_document_trigger(raw):
            return ""
        start = 0
    block = raw[start:]
    for end_marker in (TRIGGER_DOC_END, "===КОНЕЦ ПОСТАНОВЛЕНИЯ==="):
        end = block.find(end_marker)
        if end >= 0:
            return block[:end + len(end_marker)]
    # конец блока мог не прийти (обрезанный ответ) — режем по «КОНЕЦ ОТВЕТА»
    end = block.find(TRIGGER_ANSWER_END)
    return block[:end] if end >= 0 else block


def parse_ai_answer(answer_text: str, base: Postanovlenie | None = None) -> Postanovlenie | None:
    """Достаёт поля постановления из ответа ИИ (триггерная логика).

    Понимает оба вида ответа, которые допускает промт:
    * структурированный — строки «Ключ: значение» и нумерованные пункты;
    * свободный — тогда просто сохраняется как обстоятельства.

    Возвращает `None`, если ответ не похож на постановление.
    """
    text = str(answer_text or "")
    if not text.strip():
        return None

    # v30.3: наша собственная заявка — не документ. Её служебные пометки
    # однозначно указывают на конструктор, а не на ответ ИИ.
    if is_own_request(text):
        diag_event("postanovlenie.answer_is_own_request")
        return None

    # v30.1: работаем только с блоком постановления, если он есть в ответе.
    block = extract_document_block(text)
    if block.strip("= \n\tПОСТАНОВЛЕНИЕКОНЕЦ"):
        text = block
    elif looks_like_image_description(text):
        # Блока нет, а пришло описание картинки — это не документ: базовый
        # вариант (данные формы) остаётся как есть, чтобы в PNG не попал пересказ.
        diag_event("postanovlenie.answer_is_image_description")
        return None

    text = scrub_chat_text(text)

    doc = Postanovlenie.from_dict((base or Postanovlenie()).to_dict())
    doc.source = "ai"
    doc.created_at = time.time()

    inside = False
    saw_marker = False
    saw_content = False          # ИИ дал что-то кроме номера и даты
    points: list = []
    free_lines: list = []
    # v36 (замечание): ИИ присылал строку «Постановляющая часть: 1. … 2. …»
    # ОДНОЙ строкой. Ключ не совпадал ни с одним полем, и вся резолюция уезжала
    # в «Обстоятельства» — поэтому в бланке не было приказа, а обстоятельства
    # дублировали сами себя. Теперь такая строка разбирается на пункты.
    in_points = False

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # v37: «Постановляющая часть:» — это рубрика резолюции, но
        # is_section_heading() принимала её за заголовок описания картинки и
        # выбрасывала строку. Из-за этого in_points не включался, и все пункты
        # приказа уезжали в «Обстоятельства» (выгрузка LSCSD-IAD-1).
        heading_plain = strip_markdown(line)
        heading_key = heading_plain.split(":", 1)[0].strip(" .").lower()
        if heading_key.startswith(("постановляющая", "резолютивная", "постановил",
                                   "решил")):
            in_points = True
            rest = heading_plain.split(":", 1)[1].strip() if ":" in heading_plain else ""
            for chunk in re.split(r"(?:^|\s)(?=\d{1,2}[.)]\s)", rest):
                body = strip_markdown(re.sub(r"^\d{1,2}[.)]\s*", "", chunk.strip()))
                if body:
                    points.append(body)
                    saw_content = True
            continue
        if is_ui_noise(line) or is_section_heading(line):
            continue
        compact = re.sub(r"[\s=*/\-_]+", "", line.upper())
        # v37: маркер — это строка «ПОСТАНОВЛЕНИЕ» целиком. Раньше под условие
        # попадало любое начало с этого слова, и поле «Постановление вынес:
        # Marcus Nomad» съедалось как маркер — в документе не было подписанта.
        if (compact.startswith("ПОСТАНОВЛЕНИЕ") and ":" not in line
                and len(compact) <= len("ПОСТАНОВЛЕНИЕ") + 6):
            inside = True
            saw_marker = True
            continue
        if "КОНЕЦПОСТАНОВЛЕНИЯ" in compact:
            inside = False
            continue
        if "КОНЕЦОТВЕТА" in compact:
            continue

        if ":" in line:
            raw_key, raw_value = line.split(":", 1)
            key = _normalize_key(raw_key)
            value = strip_markdown(raw_value)
            heading = _clean(raw_key).lower().replace("**", "").strip(" .")
            # «Постановляющая часть: …» / «Резолютивная часть: …»
            if not key and heading.startswith(("постановляющая", "резолютивная",
                                               "постановил", "решил")):
                in_points = True
                value = value.strip()
                if value:
                    # внутри строки может быть сразу несколько пунктов «1. … 2. …»
                    for chunk in re.split(r"(?:^|\s)(?=\d{1,2}[.)]\s)", value):
                        body = strip_markdown(re.sub(r"^\d{1,2}[.)]\s*", "", chunk.strip()))
                        if body:
                            points.append(body)
                            saw_content = True
                continue
            if key and value:
                # v37: в значение мог попасть весь чат вместе с инструкцией.
                # Чистим ТОЛЬКО инструкции: значение поля («Примечание: Mem»)
                # трогать нельзя, даже если оно похоже на обрывок имени файла.
                value = "\n".join(
                    chunk for chunk in scrub_chat_text(value).splitlines()
                    if not is_chat_instruction(chunk)).strip()
                if not value:
                    continue
                doc.set(key, value)
                if key not in ("doc_number", "doc_date"):
                    saw_content = True
                    in_points = False
                continue

        numbered = _NUMBERED.match(line)
        if numbered:
            body = strip_markdown(numbered.group(2))
            if body:
                points.append(body)
                saw_content = True
                continue
        bullet = _BULLET.match(line)
        if bullet and strip_markdown(bullet.group(1)):
            points.append(strip_markdown(bullet.group(1)))
            saw_content = True
            continue
        plain = strip_markdown(line)
        if plain:
            low_plain = plain.lower().strip(" .:")
            if low_plain.startswith(("постановляющая часть", "резолютивная часть")):
                in_points = True
                rest = re.sub(r"^[^:]*:\s*", "", plain).strip()
                if rest:
                    for chunk in re.split(r"(?:^|\s)(?=\d{1,2}[.)]\s)", rest):
                        body = strip_markdown(re.sub(r"^\d{1,2}[.)]\s*", "", chunk.strip()))
                        if body:
                            points.append(body)
                            saw_content = True
                continue
            if in_points:
                for chunk in re.split(r"(?:^|\s)(?=\d{1,2}[.)]\s)", plain):
                    body = strip_markdown(re.sub(r"^\d{1,2}[.)]\s*", "", chunk.strip()))
                    if body:
                        points.append(body)
                        saw_content = True
                continue
            free_lines.append(plain)
            saw_content = True

    if points:
        doc.points = points
    # Неразобранный текст — это содержание документа (обстоятельства), а не
    # «дополнительно»: в v27 мусор интерфейса попадал именно в «Дополнительно».
    if free_lines:
        joined = "\n".join(free_lines).strip()
        if not doc.get("situation"):
            doc.situation = joined
        elif joined and joined not in doc.get("situation"):
            doc.situation = (doc.get("situation") + "\n" + joined).strip()

    if not saw_marker and not (doc.get("subject_name") or doc.get("penalty") or doc.points):
        return None
    # v31: документ считается разобранным, только если ИИ дал СОДЕРЖАНИЕ.
    # Одного «Номер:» (или «Номер» + «Дата») мало: раньше этого хватало, и
    # печатался бланк из конструктора, хотя ИИ фактически ничего не прислал.
    if not saw_content:
        diag_event("postanovlenie.answer_without_content", chars=len(text))
        return None
    if not doc.get("doc_date"):
        doc.doc_date = today_ru()
    if not doc.get("doc_type"):
        doc.doc_type = DOC_TYPES[0]
    return doc


# --------------------------------------------------------------------------- #
#  Сохранение результатов в папку postanovleniya
# --------------------------------------------------------------------------- #

#: Имена файлов одной выдачи (по номеру документа).
def doc_folder(root, number: str = "") -> Path:
    """Папка одной выдачи: `postanovleniya/<номер>/` (или `_без-номера`)."""
    safe = re.sub(r"[^\w\-. №]", "_", _clean(number) or "без-номера")[:60]
    folder = Path(root) / safe.strip()
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def copy_pngs_into(folder, paths) -> list:
    """Кладёт копии готовых PNG в папку выдачи (рядом с заявкой и ответом ИИ)."""
    import shutil
    folder = Path(folder)
    copied = []
    for path in paths or []:
        try:
            target = folder / Path(path).name
            if Path(path).resolve() != target.resolve():
                shutil.copy2(path, target)
            copied.append(target)
        except Exception:
            continue
    return copied


def save_ai_answer(root, doc: Postanovlenie, answer_text: str) -> list:
    """Складывает в папку выдачи то, что ответил ИИ.

    * `ИИ-ответ.txt` — ровно тот текст, который пришёл в чат (as is, с
      markdown) — чтобы всегда можно было понять, откуда взялся документ;
    * `ИИ-поля.json` — разобранные поля (что именно клиент принял к сведению);
    * `ИИ-документ.txt` — РОВНО тот бланк, который печатается в PNG.
    """
    folder = doc_folder(root, doc.get("doc_number"))
    written = []
    raw = str(answer_text or "")
    if raw.strip():
        path = folder / "ИИ-ответ.txt"
        path.write_text(raw.replace("\r\n", "\n"), encoding="utf-8")
        written.append(path)
    payload = {
        "когда": datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
        "источник": doc.source,
        "поля": doc.to_dict(),
    }
    path = folder / "ИИ-поля.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    written.append(path)
    path = folder / "ИИ-документ.txt"
    path.write_text(doc.to_document_txt(), encoding="utf-8")
    written.append(path)
    return written



# --------------------------------------------------------------------------- #
#  Юридическая проверка постановления (v34)
# --------------------------------------------------------------------------- #

#: Процессуальные кодексы — их нормы НЕ являются материальным основанием
#: дисциплинарного взыскания (замечание анализа: «часть 1 статьи 16
#: Процессуального кодекса» регламентирует задержание, а не проступок).
_PROCEDURAL_MARKS = ("процессуальн", "упк", "проц. кодекс")

#: Материальные кодексы: ссылаться можно, обосновывать ими дисциплинарное
#: взыскание — нет (ст. 8.24 устава нарушение НПА штата — самостоятельный
#: дисциплинарный проступок сотрудника).
_MATERIAL_CODE_MARKS = ("уголовн", "ук ", "ук\u00a0", "коап", "административн")

#: Ограничение полномочий из п. 4.4 устава: понижение — только Шериф,
#: его заместители и Ассистент Шерифа.
_RANK_LIMITS = (
    ("понижение", ("шериф", "заместител", "ассистент шерифа", "ундершериф")),
)


def split_basis(basis: str) -> tuple:
    """Делит «Нормативное основание» на устав и прочие акты.

    Возвращает (`уставная часть`, `прочие акты`). Нужно, чтобы процессуальные
    нормы не попадали в обоснование взыскания.
    """
    charter, others = [], []
    for part in re.split(r"[;\n]+", str(basis or "")):
        part = part.strip(" .,")
        if not part:
            continue
        low = part.lower()
        if "устав" in low or "lscsd" in low:
            charter.append(part)
        else:
            others.append(part)
    return "; ".join(charter), "; ".join(others)


def procedural_parts(basis: str) -> list:
    """Куски основания со ссылкой на процессуальный кодекс."""
    result = []
    for part in re.split(r"[;\n]+", str(basis or "")):
        low = part.lower()
        if any(mark in low for mark in _PROCEDURAL_MARKS):
            result.append(part.strip(" .,"))
    return [p for p in result if p]


#: Признаки внеигровой (OOC) речи в официальном документе (v36, промт 1.19 п. 19).
#:
#: Замечание: в постановление попало «при отсутствии на то весомых IC причин».
#: «IC», «OOC», «РП», «отыгран», «администрация сервера» — это язык игроков, а
#: не штата Сан-Андреас. В бланке их быть не может: там официальный язык права.
OOC_MARKERS: tuple[tuple[str, str], ...] = (
    ("IC", "в отсутствие законных и фактических оснований"),
    ("OOC", ""),
    ("RP", ""),
    ("РП", ""),
    ("нон-РП", ""),
    ("non-RP", ""),
    ("отыгр", ""),
    ("не отыграл", ""),
    ("функционал", ""),
    ("по правилам сервера", ""),
    ("правила сервера", ""),
    ("администрация сервера", ""),
    ("админ", ""),
    ("бан ", ""),
    ("варн", ""),
    ("персонаж игрока", ""),
    ("игровой персонаж", ""),
)

#: Готовые формулировки для типовых внеигровых оборотов.
_OOC_REWRITES: tuple[tuple[str, str], ...] = (
    # «в соответствии с IC информацией» → «на основании материалов проверки»
    (r"в\s+соответствии\s+с\s+IC[-\s]*информаци\w*", "на основании материалов проверки"),
    (r"согласно\s+IC[-\s]*информаци\w*", "согласно материалам проверки"),
    (r"по\s+IC[-\s]*информаци\w*", "по материалам проверки"),
    (r"IC[-\s]*информаци\w*", "материалам проверки"),
    # «при отсутствии на то весомых IC причин» → официальная формула целиком
    (r"(?:при\s+отсутствии|без|не\s+имея)\s+(?:на\s+то\s+)?(?:весомых\s+|"
     r"достаточных\s+|законных\s+)?IC[-\s]*причин\w*",
     "в отсутствие законных и фактических оснований"),
    (r"(?:при\s+отсутствии|без)\s+(?:на\s+то\s+)?(?:весомых|достаточных)\s+причин\w*",
     "в отсутствие законных и фактических оснований"),
    (r"IC[-\s]*причин\w*", "законных и фактических оснований"),
    (r"IC[-\s]*основани\w*", "законных оснований"),
    # одиночные термины
    (r"(?<![A-Za-zА-Яа-яЁё])IC(?![A-Za-zА-Яа-яЁё])", "в рамках служебной деятельности"),
    (r"(?<![A-Za-zА-Яа-яЁё])OOC(?![A-Za-zА-Яа-яЁё])", ""),
    (r"(?<![A-Za-zА-Яа-яЁё])(?:RP|РП)(?![A-Za-zА-Яа-яЁё])", ""),
)

#: Русские слова, внутри которых «IC»/«RP» — не термин (латиница в кириллице
#: не встречается, поэтому проверяем только границы слова).
def ooc_terms(text: str) -> list:
    """Находит внеигровые термины в тексте. Возвращает список найденных."""
    found: list = []
    low = " " + str(text or "").lower() + " "
    for marker, _replacement in OOC_MARKERS:
        needle = marker.lower().strip()
        if not needle:
            continue
        if re.search(r"(?<![a-zа-яё0-9])" + re.escape(needle) + r"(?![a-zа-яё0-9])", low):
            label = marker.strip()
            if label.lower() not in [f.lower() for f in found]:
                found.append(label)
    return found


def sanitize_ooc(text: str) -> str:
    """Переписывает внеигровые обороты официальным языком (правка документа).

    Ничего не выдумывает: заменяет только типовые конструкции на юридические
    формулы («без весомых IC причин» → «в отсутствие законных и фактических
    оснований»). Если оборот не распознан, его надо править вручную — об этом
    сообщит `check_document`.
    """
    result = str(text or "")
    for pattern, replacement in _OOC_REWRITES:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
    result = re.sub(r"\s{2,}", " ", result)
    result = re.sub(r"\s+([,.;:])", r"\1", result)
    return result.strip()


def leaked_chat_text(doc) -> str:
    """Возвращает служебную строку чата, попавшую в текст документа (v37)."""
    for label, value in (("Обстоятельства", doc.get("situation")),
                         ("Постановляющая часть", " ".join(getattr(doc, "points", []) or [])),
                         ("Нормативное основание", doc.get("legal_basis")),
                         ("Дополнительно", doc.get("extra"))):
        for line in str(value or "").splitlines():
            if is_chat_instruction(line):
                return f"{label}: «{line.strip()[:120]}…»"
    return ""


def check_ooc(doc: "Postanovlenie") -> list:
    """Замечания «в документе внеигровая лексика» (v36)."""
    issues: list = []
    for label, value in (("Обстоятельства", doc.get("situation")),
                         ("Постановляющая часть", " ".join(doc.points or [])),
                         ("Нормативное основание", doc.get("legal_basis")),
                         ("Дополнительно", doc.get("extra"))):
        found = ooc_terms(value)
        if found:
            issues.append(
                f"{label}: внеигровые термины ({', '.join(found)}) — в официальном "
                "документе их быть не должно. Замените на правовые формулировки: "
                "«в отсутствие законных и фактических оснований», «при отсутствии "
                "надлежащих правовых оснований».")
    return issues


def check_document(doc: "Postanovlenie", regulation=None) -> list:
    """Проверяет постановление по уставу. Возвращает список замечаний.

    Каждое замечание — готовый текст для человека. Пустой список = замечаний
    нет. Именно эти замечания сохраняются в `Проверка.txt` и показываются
    в клиенте, чтобы документ не уходил с юридической ошибкой.

    Проверяется то, что реально нашлось в присланном анализе:
    * вилка взысканий из устава (п. 4.1 и перечни «[ … ]» у пунктов);
    * смешение процессуальных и материальных норм;
    * ограничения по должности (п. 4.4 — понижение только Шериф/заместители/
      Ассистент Шерифа);
    * «чёрный список» — только по п. 4.5 (3 строгих выговора или
      несдача переаттестации);
    * пункты устава, которые не удалось найти в базе.
    """
    issues: list = []
    basis = doc.get("legal_basis")
    clause = doc.get("violation_clause")

    # 1. Вилка наказаний по уставу.
    allowed = allowed_penalties_for(clause, regulation)
    penalty = normalize_penalty(doc.get("penalty")) or doc.get("penalty")
    if allowed and penalty and penalty not in allowed:
        issues.append(
            f"Взыскание «{penalty}» не предусмотрено за указанные пункты. "
            f"Устав допускает: {', '.join(allowed)}.")

    # 2. Процессуальные нормы не обосновывают дисциплинарное взыскание.
    procedural = procedural_parts(basis)
    if procedural:
        issues.append(
            "Процессуальный кодекс не может быть основанием дисциплинарного "
            "взыскания — он регулирует порядок, а не составы проступков. "
            "Уберите из основания: " + "; ".join(procedural) + ".")

    # 3. Ограничения по должности (п. 4.4 устава).
    rank = (doc.get("author_rank") or "").lower()
    if penalty in ("понижение", "увольнение") and rank:
        for limited, words in _RANK_LIMITS:
            if penalty == limited and not any(word in rank for word in words):
                issues.append(
                    f"«{penalty.capitalize()}» по п. 4.4 устава назначают только "
                    "Шериф, его заместители и Ассистент Шерифа. Укажите это "
                    "в документе или выберите взыскание в рамках полномочий.")

    # 4. Чёрный список — только по п. 4.5.
    black_list = "черн" in (str(doc.get("penalty")) + str(doc.get("extra"))).lower()
    if black_list and not re.search(r"\b4\.5\b", str(basis) + str(clause)):
        issues.append(
            "Занесение в чёрный список устав предусматривает только по п. 4.5 "
            "(3 строгих выговора или несдача переаттестации). Укажите это "
            "основание или уберите чёрный список.")

    # 5. Взыскание с «дополнительными» словами, которых нет в уставе (v37).
    #    В выгрузке было «устный выговор с занесением в личное дело» — устав
    #    такого вида взыскания не знает (п. 4.1), есть только устный выговор.
    extra_penalty = re.search(r"с\s+занесением\s+в\s+личное\s+дело",
                              " ".join([str(doc.get("penalty"))]
                                       + list(doc.decision_points())).lower())
    if extra_penalty:
        issues.append(
            "Взыскание указано с добавлением «с занесением в личное дело», "
            "а устав (п. 4.1) такого вида не предусматривает: есть только "
            "устный выговор и строгий выговор. Оставьте формулировку устава.")

    # 6. Пункт устава — заглушка вместо номера.
    if clause and is_vague_clause(clause):
        issues.append(
            "Нарушенный пункт устава не указан («по обстоятельствам, указанным "
            "в заявке»). Постановление должно ссылаться на конкретный пункт "
            "главы VIII — иначе взыскание назначено без состава проступка.")

    # 7. Кодекс этики — это п. 8.6 устава, а не отдельный акт.
    if "этическ" in basis.lower() and not re.search(r"\b8\.6\b", str(clause)):
        issues.append(
            "Кодекс этики и служебного поведения — это пункт 8.6 Устава LSCSD. "
            "Ссылку «Этический кодекс штата» замените на «пункт 8.6 "
            "Устава Los Santos County Sheriff Department».")

    # 8. Служебный текст чата попал в документ (v37).
    leaked = leaked_chat_text(doc)
    if leaked:
        issues.append(
            "В текст документа попала служебная инструкция из чата: " + leaked +
            " Её нужно удалить — в постановлении должен быть только текст решения.")

    # 9. Внеигровая лексика (IC/OOC) — промт 1.19 п. 19.
    issues.extend(check_ooc(doc))

    # 10. Пункты устава, которые не найдены в базе.
    if regulation is not None and clause:
        for number in _clause_numbers(clause):
            found = any(str(text).strip().startswith(number)
                        for text in getattr(regulation, "articles", {}).values())
            if not found:
                issues.append(f"Пункт {number} в уставе не найден — проверьте номер.")

    return issues


# --------------------------------------------------------------------------- #
#  История сформированных постановлений
# --------------------------------------------------------------------------- #

#: Виды постановлений в истории (v41). Раньше и IAD, и Прокурор писали в одну
#: папку `postanovleniya/`, из-за чего:
#: * список истории перемешивался — в форме IAD показывались постановления
#:   Прокурора, а «Загрузить в форму» падал (документ собирался моделью IAD);
#: * нумерация была общей: создав IAD-постановление, прокурор получал номер
#:   «вторым», хотя нумерация у ведомств своя (AG-1, LSCSD-IAD-1).
HISTORY_KIND_IAD = "iad"
HISTORY_KIND_GOV = "gov"


class DocumentHistory:
    """История постановлений: JSON-файлы в папке `postanovleniya`.

    Требование задачи: «вызвать старое заполнение шаблона и внести правки».
    Поэтому каждая запись — полный снимок полей, а не только имя файла.

    v41: у каждого вида постановлений СВОЯ папка и СВОЙ загрузчик:
    `postanovleniya/history/iad/*.json` и `postanovleniya/history/gov/*.json`.
    Старые записи из корня папки подхватываются только теми формами, чей
    загрузчик может их разобрать.
    """

    #: Вид истории по умолчанию. Подкласс перекрывает через `KIND` (v41).
    KIND = HISTORY_KIND_IAD

    def __init__(self, folder: Path, kind: str = ""):
        # `kind` экземпляра не должен перебивать KIND подкласса: иначе
        # GovHistory писал бы в папку IAD (ошибка ловилась тестами v41).
        self.kind = kind or type(self).KIND
        root = Path(folder)
        # Новые записи — в подпапке вида; корень читаем только как «старые».
        self.legacy_folder = root
        self.folder = root / "history" / self.kind
        self.folder.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------ загрузчик

    def _parse(self, data: dict):
        """Собирает документ из снимка истории. Переопределяется в v41."""
        return Postanovlenie.from_dict(data)

    #: Поля, которые пишет только модель Прокурора (v41). Запись с ними —
    #: постановление Прокурора, форме IAD она не показывается.
    GOV_ONLY_KEYS = ("doc_stage", "prosecutor_facts", "appeal_raw",
                     "prosecutor_rank", "appeal_subject")

    def _belongs(self, data: dict) -> bool:
        """Запись относится к этому виду истории (v41).

        История IAD принимает всё, кроме постановлений Прокурора: они лежат в
        своей папке, но старые записи могли остаться в корне. Без этой
        проверки форма IAD собирала бы из них документ IAD.
        """
        if not isinstance(data, dict):
            return False
        # Признак только по полям: номер «AG-1» может стоять и в бланке IAD
        # (так бывало в тестах и старых выгрузках), поля «вида решения» —
        # только у Прокурора.
        return not any(key in data for key in self.GOV_ONLY_KEYS)

    def _path(self, stamp: float) -> Path:
        name = datetime.fromtimestamp(stamp).strftime("%Y%m%d-%H%M%S")
        path = self.folder / f"{name}.json"
        n = 1
        while path.exists():
            path = self.folder / f"{name}-{n}.json"
            n += 1
        return path

    def save(self, doc: Postanovlenie) -> Path:
        path = self._path(doc.created_at or time.time())
        path.write_text(
            json.dumps(doc.history_payload(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    def items(self) -> list:
        """Свежие записи первыми: [(path, dump, документ)].

        v41: читаются своя папка вида и (для совместимости) старый корень.
        Одна и та же запись не показывается дважды: если снимок лежит и в
        старом корне, и в подпапке вида (или сохранён дважды без изменений),
        остаётся один — самый свежий файл.
        """
        seen: dict = {}
        order: list = []
        candidates = []
        for path in sorted(self.folder.glob("*.json")):
            candidates.append(path)
        for path in sorted(self.legacy_folder.glob("*.json")):
            # Корень остаётся «старым» хранилищем: подхватываем только то,
            # что этот вид истории умеет разобрать.
            candidates.append(path)
        for path in candidates:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(data, dict) or not self._belongs(data):
                continue
            try:
                doc = self._parse(data)
            except Exception:
                continue
            if doc is None:
                continue
            # Ключ — имя файла (без счётчика `-N`) плюс содержимое снимка.
            # Так схлопываются только настоящие дубли: копия файла при
            # переносе из старого корня в `history/<вид>/` и повторное
            # сохранение того же снимка. Разные редакции одного документа
            # остаются отдельными записями (v41).
            key = (re.sub(r"-\d+$", "", path.stem),
                   json.dumps(data, ensure_ascii=False, sort_keys=True))
            old_path = seen.get(key)
            if old_path is not None:
                # Дубль: оставляем более свежий файл (по времени изменения).
                try:
                    if old_path.stat().st_mtime >= path.stat().st_mtime:
                        continue
                except Exception:
                    continue
            seen[key] = path
            order.append((key, path, data, doc))
        return [(path, data, doc) for _key, path, data, doc in order]

    def labels(self) -> list:
        labels = []
        for _path, data, doc in self.items():
            created = _clean(data.get("human_created")) or "—"
            num = doc.get("doc_number") or "без номера"
            who = doc.get("subject_name") or "—"
            labels.append(f"{created} · {num} · {who}")
        return labels

    def used_numbers(self) -> list:
        """Номера этого вида постановлений (v41).

        Замечание: «создаю постановление для IAD, и если делаю постановление
        Прокурора следующим, то номер уже второй». Проверка идёт по своим
        номерам: у Прокурора AG-1…, у IAD LSCSD-IAD-1…, они не пересекаются.
        """
        # Повторы допустимы (две редакции одного документа): `auto_number`
        # берёт максимум, поэтому важен только состав своих номеров.
        numbers = []
        for _p, _d, doc in self.items():
            number = doc.get("doc_number")
            if number:
                numbers.append(number)
        return numbers

    def load_index(self, index: int):
        items = self.items()
        if 0 <= index < len(items):
            return items[index][2]
        return None

    # ------------------------------------------------------------ удаление (v38)
    #
    # Замечание: «в «История» добавь возможность удалять «Постановления»».
    # Удаляем ИМЕННО запись истории (json-снимок полей). Папку документа
    # (<номер>/ с PNG и текстами) не трогаем — там готовый бланк, который
    # пользователь мог уже отправить; в диалоге это прямо сказано.

    def path_at(self, index: int) -> Path | None:
        items = self.items()
        if 0 <= index < len(items):
            return items[index][0]
        return None

    def label_at(self, index: int) -> str:
        labels = self.labels()
        if 0 <= index < len(labels):
            return labels[index]
        return ""

    def delete_index(self, index: int) -> bool:
        """Удаляет запись истории по индексу в списке (свежие первыми)."""
        path = self.path_at(index)
        if path is None:
            return False
        try:
            path.unlink()
        except Exception:
            diag_event("history.delete_failed", path=str(path))
            return False
        diag_event("history.deleted", path=str(path))
        return True

    def delete_all(self) -> int:
        """Очищает историю целиком. Возвращает число удалённых записей."""
        removed = 0
        for path in sorted(self.folder.glob("*.json")):
            try:
                path.unlink()
                removed += 1
            except Exception:
                diag_event("history.delete_failed", path=str(path))
        diag_event("history.cleared", removed=removed)
        return removed


# --------------------------------------------------------------------------- #
#  Правила ведомства: подстановка реквизитов и проверка по Уставу
# --------------------------------------------------------------------------- #

class Regulation:
    """Правила конкретного ведомства (пока IAD/LSCSD, дальше — универсально).

    Читает выгруженный JSON законодательной базы (data/reestr/*.json) и умеет
    подставить реквизиты вроде «пункт 19) части 1 статьи а» и проверить,
    что такой пункт в уставе действительно есть.
    """

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else None
        self.title = ""
        self.sections: list = []
        self.articles: dict = {}
        if self.path and self.path.exists():
            self._load(self.path)

    def _load(self, path: Path) -> None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return
        self.title = _clean(data.get("title"))
        self.sections = data.get("sections") or []
        for section in self.sections:
            text = _clean(section.get("text"))
            n = section.get("n")
            if text:
                self.articles[str(n)] = text

    def clause_text(self, clause: str, act_words: str = "") -> str:
        """Ищет пункт устава по строке вида «19) части 1 статьи а».

        Возвращает точный фрагмент текста устава, если он найден, иначе "".
        Нужно для того, чтобы в постановлении была дословная норма, а не
        пересказ ИИ.
        """
        needle = _clean(clause)
        if not needle:
            return ""

        # Формат LSCSD: пункт устава — «3.1», «4.1», «10.6». Ищем дословно по
        # началу текста раздела: в уставе нет деления на статьи и части,
        # поэтому прежний шаблон «пункт 19) части 1 статьи а» тут не работает.
        dotted = re.search(r"\b(\d{1,2}\.\d{1,2})\b", needle)
        if dotted:
            number = dotted.group(1)
            for text in self.articles.values():
                if text.strip().startswith(number):
                    return text[:600]
            return ""

        item = re.search(r"(\d+)\s*\)", needle)
        article = re.search(r"статьи?\s*([0-9A-Za-zА-Яа-я]+)", needle, re.IGNORECASE)
        if not item and not article:
            return ""
        want_item = item.group(1) if item else ""
        want_article = article.group(1).lower() if article else ""
        for text in self.articles.values():
            low = text.lower()
            if want_item and f"{want_item})" not in low:
                continue
            if want_article and f"статья {want_article}" not in low and f"статьи {want_article}" not in low:
                continue
            return text[:600]
        return ""

    def penalties(self) -> list:
        """Перечень взысканий из гл. IV (п. 4.1) устава, если он есть."""
        for text in self.articles.values():
            low = text.lower()
            if "4.1" in text and "взыскан" in low:
                inside = text.split(":", 1)[-1]
                inside = re.sub(r"нажмите.*", " ", inside, flags=re.IGNORECASE)
                inside = inside.replace("\n", " ")
                parts = re.split(r"[/,;]|\sи\s", inside)
                clean = []
                for part in parts:
                    item = _clean(part).strip(". \"'")
                    item = re.sub(r"^(цитата|перечень)[:\s]*", "", item, flags=re.IGNORECASE)
                    if item and len(item) < 40 and item.lower() not in {c.lower() for c in clean}:
                        clean.append(item)
                if clean:
                    return clean
        return list(PENALTIES)


def default_regulation_path(base_dir: Path) -> Path | None:
    folder = Path(base_dir) / "reestr"
    if not folder.is_dir():
        return None
    for path in sorted(folder.glob("*.json")):
        return path
    return None
