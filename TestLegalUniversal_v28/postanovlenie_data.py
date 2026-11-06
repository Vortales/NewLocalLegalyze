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
    ("penalty", "Взыскание", "Взыскание"),
    ("situation", "Ситуация / фабула", "Ситуация"),
    ("evidence", "Доказательства", "Доказательства"),
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
    """«строгий выговор» -> «строгого выговора» (для фразы «в виде …»)."""
    text = _clean(penalty).lower()
    if not text:
        return ""
    if text in PENALTY_GENITIVE:
        return PENALTY_GENITIVE[text]
    for nominative, genitive in PENALTY_GENITIVE.items():
        if text.startswith(nominative):
            return genitive + text[len(nominative):]
    return _clean(penalty)


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


def clause_genitive(clause: str) -> str:
    """«пункт 19) части 1 статьи а» -> «пункта 19) части 1 статьи а»."""
    text = _clean(clause)
    low = text.lower()
    for nominative, genitive in _CLAUSE_GENITIVE:
        if low.startswith(nominative):
            return genitive + text[len(nominative):]
    return text

#: Поля, которые разрешено оставлять пустыми: их подбирает ИИ по обстоятельствам
#: (замечание v27: «вся информация будет браться из Обстоятельств»).
OPTIONAL_GUESSED_FIELDS = (
    "violation_act", "violation_clause", "penalty",
    "subject_rank", "subject_department", "evidence", "extra",
)

#: Ведомство по умолчанию — то, для которого в папке лежат герб и печать.
DEFAULT_VEDOMSTVO = "LOS SANTOS COUNTY SHERIFF'S DEPARTMENT"
DEFAULT_PODRAZDELENIE = "INTERNAL AFFAIRS DIVISION"
DEFAULT_STATE = "STATE OF SAN ANDREAS"


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
    penalty: str = ""
    situation: str = ""
    evidence: str = ""
    extra: str = ""
    #: Пункты постановляющей части. Если пусто — собираются автоматически.
    points: list = field(default_factory=list)
    #: Служебное: откуда взято (форма / ответ ИИ), когда создано.
    created_at: float = field(default_factory=time.time)
    source: str = "form"

    # ------------------------------------------------------------------ утиль

    def get(self, key: str, default: str = "") -> str:
        return _clean(getattr(self, key, default))

    def set(self, key: str, value) -> None:
        if hasattr(self, key):
            setattr(self, key, value)

    def signature(self) -> str:
        """Подпись «И. Фамилия» из поля «кто выносит»."""
        return initials_signature(self.author_name)

    def title(self) -> str:
        return self.doc_title or self.doc_type

    def document_title(self) -> str:
        """Полный заголовок для бланка: «Постановление …» на русском."""
        return self.title().strip()

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
        act = self.get("violation_act") or "внутреннего устава"
        clause = clause_genitive(normalize_clause(self.get("violation_clause"), act))
        because = f", в связи с нарушением {clause} {act}" if clause else f", в связи с нарушением {act}"
        penalty = penalty_instrumental(self.get("penalty")) or "дисциплинарного взыскания"
        result = [
            f"Назначить сотруднику {who} дисциплинарное взыскание в виде {penalty}{because}.",
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
        lines = [DOC_MARKER_NAME]
        for key, _form, label in DOC_FIELDS:
            value = self.get(key)
            if value:
                lines.append(f"{label}: {value}")
            elif key in OPTIONAL_GUESSED_FIELDS:
                # v28: если пользователь не заполнил нарушение/взыскание/звание,
                # ИИ должен определить их сам по обстоятельствам (промт 1.12).
                lines.append(f"{label}: определить по обстоятельствам")
        if self.get("penalty") or self.get("violation_clause"):
            # Подсказка для ИИ: он всё равно перепишет пункты официально.
            lines.append("")
            lines.append("Предлагаемая постановляющая часть (перепиши официально):")
            for i, point in enumerate(self.decision_points(), 1):
                lines.append(f"{i}. {point}")
        lines.append("")
        lines.append(TRIGGER_ANSWER_END)
        return "\n".join(lines) + "\n"

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
    "ситуация": "situation",
    "фабула": "situation",
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
    r"|^[\w\- ]{1,40}\.(pdf|txt|png|docx|json)$"
    r"|^шаблон\.txt$"
    r"|^показать больше$|^копировать$|^поделиться$|^нравится$)",
    re.IGNORECASE,
)


def is_ui_noise(line: str) -> bool:
    """Строка — мусор интерфейса, а не часть постановления."""
    text = _clean(line)
    if not text:
        return True
    if len(text) <= 2:
        return True
    return bool(_UI_NOISE.search(text))


def _normalize_key(raw: str) -> str:
    key = _clean(raw).lower().rstrip(":").strip()
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


def has_document_trigger(text: str) -> bool:
    """Есть ли в тексте признак постановления (по триггеру).

    Триггеров два: начальный `===ПОСТАНОВЛЕНИЕ===` (или строка-маркер
    «ПОСТАНОВЛЕНИЕ», с которой начинается файл-заявка) и конечный
    `===КОНЕЦ ПОСТАНОВЛЕНИЯ===`. Достаточно любого из них — окончательное
    решение всё равно принимает разбор полей (`parse_ai_answer`), поэтому
    упоминание слова «постановление» в обычном правовом ответе ничего не
    сломает: без полей и пунктов документ не собирается.
    """
    compact = re.sub(r"[\s=*/\-_]+", "", str(text or "").upper())
    return "ПОСТАНОВЛЕНИЕ" in compact or "КОНЕЦПОСТАНОВЛЕНИЯ" in compact


def has_answer_end(text: str) -> bool:
    compact = re.sub(r"[\s=*/\-_]+", "", str(text or "").upper())
    return "КОНЕЦОТВЕТА" in compact


def strip_triggers(text: str) -> str:
    out = str(text or "")
    for trigger in (TRIGGER_ANSWER_END, TRIGGER_DOC_START, TRIGGER_DOC_END):
        out = out.replace(trigger, "")
    return out.strip()


def parse_ai_answer(answer_text: str, base: Postanovlenie | None = None) -> Postanovlenie | None:
    """Достаёт поля постановления из ответа ИИ (v27, триггерная логика).

    Понимает оба вида ответа, которые допускает промт:
    * структурированный — строки «Ключ: значение» и нумерованные пункты;
    * свободный — тогда просто сохраняется как ситуация/дополнительно.

    Возвращает `None`, если ответ не похож на постановление.
    """
    text = str(answer_text or "")
    if not text.strip():
        return None

    doc = Postanovlenie.from_dict((base or Postanovlenie()).to_dict())
    doc.source = "ai"
    doc.created_at = time.time()

    inside = False
    saw_marker = False
    points: list = []
    free_lines: list = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or is_ui_noise(line):
            continue
        compact = re.sub(r"[\s=*/\-_]+", "", line.upper())
        if "ПОСТАНОВЛЕНИЕ" in compact and compact.startswith("ПОСТАНОВЛЕНИЕ"):
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
            if key and _clean(raw_value):
                doc.set(key, _clean(raw_value))
                continue

        numbered = _NUMBERED.match(line)
        if numbered:
            body = _clean(numbered.group(2))
            if body:
                points.append(body)
                continue
        bullet = _BULLET.match(line)
        if bullet and _clean(bullet.group(1)):
            points.append(_clean(bullet.group(1)))
            continue
        free_lines.append(line)

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
    if not doc.get("doc_date"):
        doc.doc_date = today_ru()
    if not doc.get("doc_type"):
        doc.doc_type = DOC_TYPES[0]
    return doc


# --------------------------------------------------------------------------- #
#  История сформированных постановлений
# --------------------------------------------------------------------------- #

class DocumentHistory:
    """История постановлений: JSON-файлы в папке `postanovleniya`.

    Требование задачи: «вызвать старое заполнение шаблона и внести правки».
    Поэтому каждая запись — полный снимок полей, а не только имя файла.
    """

    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)

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
        """Свежие записи первыми: [(path, dump, Postanovlenie)]."""
        result = []
        for path in sorted(self.folder.glob("*.json"), reverse=True):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            result.append((path, data, Postanovlenie.from_dict(data)))
        return result

    def labels(self) -> list:
        labels = []
        for _path, data, doc in self.items():
            created = _clean(data.get("human_created")) or "—"
            num = doc.get("doc_number") or "без номера"
            who = doc.get("subject_name") or "—"
            labels.append(f"{created} · {num} · {who}")
        return labels

    def used_numbers(self) -> list:
        return [doc.get("doc_number") for _p, _d, doc in self.items() if doc.get("doc_number")]

    def load_index(self, index: int) -> Postanovlenie | None:
        items = self.items()
        if 0 <= index < len(items):
            return items[index][2]
        return None


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
