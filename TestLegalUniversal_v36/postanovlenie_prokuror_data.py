"""postanovlenie_prokuror_data.py — постановления Прокурора (v36).

Прокурор выносит постановления по обращениям, поступившим в Офис
Генерального прокурора. Состав и формулировки взяты из примеров
(`Постановление Прокурора/1–6.txt`) и Регламента подачи обращений:

* шапка «STATE OF SAN-ANDREAS / OFFICE OF THE ATTORNEY GENERAL»;
* «ПОСТАНОВЛЕНИЕ № …» + заголовок вида решения;
* «Я, Прокурор штата Сан-Андреас ФИО, удостоверение № …, руководствуясь
  частью 2 статьи 36 Конституции штата, частями 1, 2 и 6 статьи 8 Закона
  «О деятельности офиса Генерального прокурора», статьями 11–14 Регламента
  подачи обращений, рассмотрев обращение гражданина … от …»;
* «УСТАНОВИЛ:» — фактические обстоятельства и правовая оценка;
* «ПОСТАНОВИЛ:» — резолюция (принять / отказать / привлечь / передать в суд).

Порядок работы такой же, как у IAD: форма → «Сформировать» → .txt в чат →
ответ ИИ → PNG собирается ТОЛЬКО из ответа ИИ.
"""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import postanovlenie_data as base
from postanovlenie_data import (
    _clean, diag_event, extract_document_block, has_answer_end, initials_signature,
    is_own_request, is_section_heading, is_ui_noise, normalize_clause,
    split_subject, strip_markdown, today_ru, _BULLET, _NUMBERED,
)

#: Шапка бланка прокуратуры.
GOV_VEDOMSTVO = "STATE OF SAN-ANDREAS"
GOV_PODRAZDELENIE = "OFFICE OF THE ATTORNEY GENERAL"

#: Виды решений прокурора по обращению (Регламент, ст. 13):
#: принятие к рассмотрению, отказ, приостановление и т. д.
GOV_DOC_TYPES: tuple[str, ...] = (
    "О принятии обращения к собственному производству",
    "Об отказе в принятии обращения к рассмотрению",
    "О привлечении к административной ответственности",
    "О возбуждении уголовного дела по обращению",
    "О завершении проверки по обращению и передаче материалов в уголовное производство",
    "О переадресации обращения по подведомственности",
    "О прекращении производства по обращению",
)

#: Предметы обращения — ровно как в форме, которую подают граждане
#: (Регламент подачи обращений, ст. 5).
GOV_SUBJECTS: tuple[str, ...] = (
    "заявление о преступлении, совершённом должностным лицом при исполнении "
    "либо в связи с исполнением служебных обязанностей",
    "заявление о нарушении законодательства (не содержащем признаков "
    "преступления), совершённом должностным лицом",
    "заявление о преступлении, совершённом гражданским лицом",
    "заявление о нарушении законодательства (не содержащем признаков "
    "преступления), совершённом гражданским лицом",
    "жалоба на действия (бездействие) должностного лица",
)

#: Поля формы. Порядок = порядок в форме и в .txt заявки; третье значение —
#: подпись поля в бланке (её же понимает разбор ответа ИИ).
GOV_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("vedomstvo", "Ведомство", "Ведомство"),
    ("podrazdelenie", "Подразделение", "Подразделение"),
    ("doc_type", "Вид решения", "Вид документа"),
    ("doc_number", "Номер постановления", "Номер"),
    ("doc_date", "Дата постановления", "Дата"),
    ("author_name", "Прокурор (И. Фамилия)", "Постановление вынес"),
    ("author_rank", "Должность", "Должность"),
    ("author_id", "Удостоверение прокурора", "Удостоверение прокурора"),
    ("city", "Город", "Город"),
    # ── заявитель ─────────────────────────────────────────────────────────
    ("applicant_name", "Заявитель (ФИО)", "Заявитель"),
    ("applicant_id", "ID-карта заявителя", "ID заявителя"),
    ("applicant_representative", "Представитель заявителя",
     "Представитель заявителя"),
    ("appeal_number", "Номер обращения", "Номер обращения"),
    ("appeal_date", "Дата обращения", "Дата обращения"),
    ("appeal_subject", "Предмет обращения", "Предмет обращения"),
    # ── нарушитель и событие ──────────────────────────────────────────────
    ("subject_name", "Нарушитель (ФИО)", "В отношении"),
    ("subject_id", "Удостоверение / ID нарушителя", "Удостоверение"),
    ("subject_rank", "Должность / звание нарушителя", "Звание сотрудника"),
    ("subject_department", "Организация нарушителя", "Отдел сотрудника"),
    ("event_date", "Дата и время события", "Дата и время события"),
    ("event_place", "Место события", "Место события"),
    ("evidence", "Доказательства (видео, тайм-коды)", "Доказательства"),
    # ── правовая часть ────────────────────────────────────────────────────
    ("violation_act", "Нарушенный акт", "Нарушенный акт"),
    ("violation_clause", "Статьи / пункты", "Пункт нарушения"),
    ("legal_basis", "Правовое основание", "Нормативное основание"),
    ("penalty", "Наказание / решение", "Взыскание"),
    ("situation", "Обстоятельства", "Обстоятельства"),
    ("extra", "Дополнительно", "Дополнительно"),
)

GOV_FIELD_LABELS = {key: label for key, _form, label in GOV_FIELDS}
GOV_FORM_LABELS = {key: form for key, form, _label in GOV_FIELDS}

#: Поля, которые ИИ вправе определить сам по обстоятельствам.
GOV_OPTIONAL_GUESSED = (
    "violation_act", "violation_clause", "legal_basis", "penalty",
    "subject_rank", "subject_department", "extra", "event_place",
)

#: Ссылки на Конституцию и Закон об ОГП, которые прокурор указывает в вводной
#: части (так во всех примерах 1–6).
GOV_PREAMBLE_BASIS = (
    "частью 2 статьи 36 Конституции штата Сан-Андреас",
    "частями 1, 2 и 6 статьи 8 Закона «О деятельности офиса Генерального "
    "прокурора штата San-Andreas»",
    "статьями 11, 12, 13 и 14 Регламента подачи обращений в Офис "
    "Генерального прокурора штата San-Andreas",
)

#: Алиасы ответа ИИ (то же поле может прийти разными словами).
GOV_ALIASES: dict = {
    "номер": "doc_number",
    "№": "doc_number",
    "номер постановления": "doc_number",
    "дата": "doc_date",
    "дата постановления": "doc_date",
    "вид документа": "doc_type",
    "вид решения": "doc_type",
    "постановление вынес": "author_name",
    "прокурор": "author_name",
    "вынес": "author_name",
    "должность": "author_rank",
    "удостоверение прокурора": "author_id",
    "номер удостоверения прокурора": "author_id",
    # «Удостоверение: 32229» в ответе ИИ — это нарушитель (как у IAD).
    # Реквизит прокурора приходит только с уточнением «прокурора».
    "удостоверение": "subject_id",
    "город": "city",
    "заявитель": "applicant_name",
    "фио заявителя": "applicant_name",
    "id заявителя": "applicant_id",
    "id-карта": "applicant_id",
    "id карта": "applicant_id",
    "представитель заявителя": "applicant_representative",
    "номер обращения": "appeal_number",
    "дата обращения": "appeal_date",
    "предмет обращения": "appeal_subject",
    "в отношении": "subject_name",
    "нарушитель": "subject_name",
    "фио нарушителя": "subject_name",
    "звание сотрудника": "subject_rank",
    "должность нарушителя": "subject_rank",
    "отдел сотрудника": "subject_department",
    "организация": "subject_department",
    "дата и время события": "event_date",
    "место события": "event_place",
    "место": "event_place",
    "доказательства": "evidence",
    "видеофиксация": "evidence",
    "нарушенный акт": "violation_act",
    "акт": "violation_act",
    "пункт нарушения": "violation_clause",
    "статьи": "violation_clause",
    "статья": "violation_clause",
    "нормативное основание": "legal_basis",
    "правовое основание": "legal_basis",
    "основание": "legal_basis",
    "взыскание": "penalty",
    "наказание": "penalty",
    "решение": "penalty",
    "обстоятельства": "situation",
    "ситуация": "situation",
    "фабула": "situation",
    "дополнительно": "extra",
    "примечание": "extra",
    "ведомство": "vedomstvo",
    "подразделение": "podrazdelenie",
}


def _covered(fragment: str, text: str, threshold: float = 0.6) -> bool:
    """Похоже ли, что `fragment` уже упомянут в `text`.

    Нужно, чтобы «УСТАНОВИЛ» не повторял место события второй раз: в тексте
    может стоять «в здании EMS», а в поле — «здание EMS».
    """
    words = [w for w in re.findall(r"[\wА-Яа-яЁё-]{3,}", str(fragment or "").lower())]
    if not words:
        return False
    low = re.sub(r"[^\wА-Яа-яЁё]+", " ", str(text or "").lower())
    # сравниваем по началу слова: «здание» и «здании» — одно и то же место
    hits = sum(1 for w in words if w[:5] in low)
    return hits / len(words) >= threshold


def _normalize_gov_key(raw: str) -> str:
    key = _clean(raw).lower().replace("**", "").replace("__", "").replace("`", "")
    key = key.replace("«", "").replace("»", "").replace('"', "")
    key = re.sub(r"^[\s>*_`#>•·\d.)\]]+", "", key)
    key = key.replace("\u00a0", " ").rstrip(":").strip(" .")
    key = re.sub(r"\s+", " ", key)
    if key in GOV_ALIASES:
        return GOV_ALIASES[key]
    for field_key, _form, label in GOV_FIELDS:
        if key == label.lower():
            return field_key
    return ""


@dataclass
class PostanovlenieProkurora:
    """Одно постановление Прокурора: поля формы + резолюция."""

    vedomstvo: str = GOV_VEDOMSTVO
    podrazdelenie: str = GOV_PODRAZDELENIE
    doc_type: str = GOV_DOC_TYPES[0]
    doc_number: str = ""
    doc_date: str = field(default_factory=today_ru)
    author_name: str = ""
    author_rank: str = "Прокурор"
    author_id: str = ""
    city: str = "г. Лос-Сантос"
    applicant_name: str = ""
    applicant_id: str = ""
    applicant_representative: str = ""
    appeal_number: str = ""
    appeal_date: str = ""
    appeal_subject: str = ""
    subject_name: str = ""
    subject_id: str = ""
    subject_rank: str = ""
    subject_department: str = ""
    event_date: str = ""
    event_place: str = ""
    evidence: str = ""
    violation_act: str = ""
    violation_clause: str = ""
    legal_basis: str = ""
    penalty: str = ""
    situation: str = ""
    #: Текст раздела «УСТАНОВИЛ» из ответа ИИ (правовая оценка, выводы).
    established: str = ""
    extra: str = ""
    #: Пункты «ПОСТАНОВИЛ». Пусто — собираются по виду решения.
    points: list = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    source: str = "form"

    def __post_init__(self) -> None:
        self.applicant_name, self.applicant_id = split_subject(
            self.applicant_name, self.applicant_id)
        self.doc_type = _clean(self.doc_type) or GOV_DOC_TYPES[0]

    # ------------------------------------------------------------------ утиль

    def get(self, key: str, default: str = "") -> str:
        return _clean(getattr(self, key, default))

    def set(self, key: str, value) -> None:
        if not hasattr(self, key):
            return
        if key in ("applicant_name", "applicant_id"):
            other = self.applicant_id if key == "applicant_name" else self.applicant_name
            name, ident = split_subject(
                value if key == "applicant_name" else self.applicant_name,
                value if key == "applicant_id" else other,
            )
            self.applicant_name, self.applicant_id = name, ident
            return
        setattr(self, key, value)

    def signature(self) -> str:
        return initials_signature(self.author_name)

    def title(self) -> str:
        doc_type = self.get("doc_type")
        return doc_type or GOV_DOC_TYPES[0]

    def document_title(self) -> str:
        """«ПОСТАНОВЛЕНИЕ № 936» + подзаголовок о чём."""
        return self.title()

    def number_line(self) -> str:
        number = self.get("doc_number")
        return f"ПОСТАНОВЛЕНИЕ № {number}" if number else "ПОСТАНОВЛЕНИЕ"

    # ------------------------------------------------------------- текст

    def preamble(self) -> str:
        """Вводная часть «Я, Прокурор …, рассмотрев обращение …»."""
        rank = self.get("author_rank") or "Прокурор"
        name = self.get("author_name")
        who = f"{rank} штата Сан-Андреас" if "штата" not in rank.lower() else rank
        if name:
            who = f"{who} {name}"
        ident = self.get("author_id")
        if ident:
            who = f"{who}, удостоверение № {ident}"
        basis = ", ".join(GOV_PREAMBLE_BASIS[:2])
        tail = GOV_PREAMBLE_BASIS[2]
        applicant = self.get("applicant_name") or "гражданина"
        appeal_date = self.get("appeal_date") or self.get("doc_date")
        if applicant.lower().startswith(("граждан", "сотрудник")):
            applicant_phrase = applicant
        else:
            applicant_phrase = f"гражданина {applicant}"
        return (
            f"Я, {who}, руководствуясь {basis}, а также {tail}, "
            f"рассмотрев обращение {applicant_phrase}"
            + (f" от {appeal_date}" if appeal_date else "")
            + ","
        )

    def appeal_point(self) -> str:
        """Пункт о порядке обжалования (статьи 27–28 Регламента)."""
        return ("Настоящее постановление может быть обжаловано вышестоящему "
                "прокурору либо в суд в срок, не превышающий 72 часа (статьи 27–28 "
                "Регламента подачи обращений в Офис Генерального прокурора).")

    def facts_text(self) -> str:
        """«УСТАНОВИЛ» — фактические обстоятельства дела."""
        parts = []
        if self.get("situation"):
            parts.append(self.get("situation"))
        established = self.get("established")
        if established and established not in (self.get("situation") or ""):
            parts.append(established)
        # Дату и место добавляем, только если их НЕТ в тексте обстоятельств,
        # иначе в «УСТАНОВИЛ» одно и то же повторялось дважды.
        situation_low = (self.get("situation") or "").lower()
        when = self.get("event_date")
        place = self.get("event_place")
        date_part = ""
        m = re.match(r"\s*(\d{1,2}\.\d{1,2}\.\d{2,4})", when or "")
        if m:
            date_part = m.group(1)
        need_when = bool(when) and when.lower() not in situation_low \
            and (not date_part or date_part not in situation_low)
        need_place = bool(place) and not _covered(place, situation_low)
        if need_when and need_place:
            parts.insert(0, f"Событие имело место: {when}, место — {place}.")
        elif need_when:
            parts.insert(0, f"Событие имело место: {when}.")
        elif need_place:
            parts.insert(0, f"Место события: {place}.")
        if self.get("violation_clause") or self.get("violation_act"):
            act = " ".join(x for x in (self.get("violation_clause"),
                                       self.get("violation_act")) if x).strip()
            parts.append(f"Указанные обстоятельства образуют нарушение: {act}.")
        evidence = self.get("evidence")
        if evidence:
            # тайм-коды/номера сравниваем отдельно: «05:01» в тексте уже есть,
            # значит второй раз «представлено: …» печатать не нужно
            codes = re.findall(r"\d{1,2}:\d{2}", evidence)
            already = " ".join(parts).lower()
            if codes:
                shown = sum(1 for code in codes if code in already)
                if shown >= max(1, len(codes) // 2):
                    evidence = ""
            if evidence and not _covered(evidence, already):
                parts.append(f"В подтверждение представлено: {evidence}.")
        return " ".join(p.strip() for p in parts if p.strip())

    def decision_points(self) -> list:
        """Резолюция. Если ИИ не прислал пункты — собираем по виду решения."""
        if self.points:
            return list(self.points)
        applicant = self.get("applicant_name") or "заявителя"
        ident = self.get("applicant_id")
        applicant_ref = f"{applicant} (ID-карта № {ident})" if ident else applicant
        appeal = self.get("appeal_number")
        appeal_ref = f" № {appeal}" if appeal else ""
        subject = self.get("subject_name")
        subject_ref = subject
        if subject and self.get("subject_id"):
            subject_ref = f"{subject} (ID № {self.get('subject_id')})"
        low = self.get("doc_type").lower()
        points: list = []
        if "отказ" in low:
            points.append(f"Отказать в принятии обращения {applicant_ref} "
                          f"к рассмотрению.")
            reason = (self.get("legal_basis") or self.get("violation_clause")
                      or "обстоятельства, предусмотренные статьей 16 Регламента "
                         "подачи обращений в Офис Генерального прокурора")
            if reason:
                points.append(f"Основание отказа: {reason}.")
            points.append("Настоящее постановление вступает в законную силу с "
                          "момента подписания и публикации и может быть "
                          "обжаловано вышестоящему прокурору либо в суд в срок, "
                          "не превышающий 72 часа (статья 28 Регламента подачи "
                          "обращений в Офис Генерального прокурора).")
            return points
        if "привлеч" in low:
            if subject_ref:
                points.append(f"Признать {subject_ref} виновным в нарушении "
                              f"{self.get('violation_clause') or 'законодательства штата'}.")
            if self.get("penalty"):
                points.append(f"Назначить наказание: {self.get('penalty')}.")
            points.append("Настоящее постановление вступает в законную силу с "
                          "момента подписания и публикации и может быть "
                          "обжаловано в течение 72 часов.")
            return points
        if "уголовное дело" in low or "передаче материалов" in low:
            points.append("Возбудить уголовное производство по изложенным "
                          "обстоятельствам в отношении "
                          f"{subject_ref or 'установленных лиц'}.")
            points.append("Материалы проверки направить в Окружной суд штата "
                          "Сан-Андреас для рассмотрения по существу.")
            points.append("Настоящее постановление вступает в законную силу с "
                          "момента подписания и публикации.")
            return points
        if "переадрес" in low:
            points.append(f"Переадресовать обращение {applicant_ref} по "
                          "подведомственности.")
            points.append("Уведомить заявителя о переадресации обращения.")
            points.append(self.appeal_point())
            return points
        if "прекращ" in low:
            points.append(f"Прекратить производство по обращению {applicant_ref}.")
            points.append(self.appeal_point())
            return points
        # По умолчанию — принятие к рассмотрению (самый частый случай в примерах).
        points.append(f"Принять к рассмотрению обращение {applicant_ref}"
                      f"{(' от ' + self.get('appeal_date')) if self.get('appeal_date') else ''}, "
                      f"поступившее в Офис Генерального прокурора{appeal_ref}.")
        who = self.get("author_name")
        ident_author = self.get("author_id")
        points.append("Принять материалы обращения в производство "
                      + (f"Прокурора штата Сан-Андреас {who}" if who else "прокурора")
                      + (f", удостоверение № {ident_author}" if ident_author else "")
                      + ".")
        points.append("Осуществить рассмотрение обращения в срок, не превышающий "
                      "72 часов, в соответствии с частью 2 статьи 12 Регламента "
                      "подачи обращений в Офис Генерального прокурора штата "
                      "San-Andreas.")
        points.append("Уведомить заявителя о принятом решении.")
        points.append("Настоящее постановление вступает в законную силу с момента "
                      "его подписания и публикации на официальном портале штата "
                      "Сан-Андреас и может быть обжаловано вышестоящему прокурору "
                      "либо в суд в срок, не превышающий 72 часа (статьи 27–28 "
                      "Регламента подачи обращений).")
        return points

    def basis_text(self) -> str:
        parts = []
        act = self.get("violation_act")
        if self.get("violation_clause"):
            clause = normalize_clause(self.get("violation_clause"), act)
            parts.append(clause)
            # «статьи 8, 10, 11» без названия акта ничего не говорит читателю
            if act and act.lower() not in clause.lower():
                parts.append(act)
        elif self.get("violation_act"):
            parts.append(self.get("violation_act"))
        legal = self.get("legal_basis")
        if legal and legal.lower() not in " ".join(parts).lower():
            parts.append(legal)
        return "; ".join(p.strip(" ;,") for p in parts if p.strip(" ;,"))

    def missing_fields(self) -> list:
        required = ("doc_number", "doc_date", "author_name", "applicant_name")
        return [GOV_FORM_LABELS[k] for k in required if not self.get(k)]

    def auto_number(self, existing: list | None = None, prefix: str = "AG") -> str:
        """Номер постановления прокуратуры: AG-937 (следующий после максимума)."""
        top = 0
        for item in existing or []:
            m = re.search(r"(\d+)\s*$", _clean(item))
            if m:
                top = max(top, int(m.group(1)))
        return f"{prefix}-{top + 1}"

    # ------------------------------------------------------- сериализация

    def to_dict(self) -> dict:
        data = asdict(self)
        data["points"] = list(self.points or [])
        return data

    @classmethod
    def from_dict(cls, data: dict | None) -> "PostanovlenieProkurora":
        data = data or {}
        allowed = {key for key, _f, _l in GOV_FIELDS} | {
            "points", "created_at", "source", "established"}
        kwargs = {k: v for k, v in data.items() if k in allowed}
        doc = cls(**kwargs)
        doc.points = [_clean(p) for p in (data.get("points") or []) if _clean(p)]
        return doc

    def to_chat_txt(self) -> str:
        """Заявка в чат: обычная просьба, без маркеров ответа ИИ."""
        lines = ["ЗАЯВКА НА ПОСТАНОВЛЕНИЕ ПРОКУРОРА"]
        lines.append("Данные для подготовки документа:")
        for key, _form, label in GOV_FIELDS:
            value = self.get(key)
            if value:
                lines.append(f"{label}: {value}")
            elif key in GOV_OPTIONAL_GUESSED:
                lines.append(f"{label}: нет данных — определи сам")
        if self.points:
            lines.append("")
            lines.append("Предлагаемые пункты (перепиши официально):")
            for i, point in enumerate(self.points, 1):
                lines.append(f"{i}. {point}")
        lines.append("")
        lines.append("Подготовь постановление Прокурора строго по этим данным, "
                     "в формате промта: один структурированный блок от начала "
                     "документа до конца ответа, без описания вложений.")
        return "\n".join(lines) + "\n"

    def to_document_txt(self) -> str:
        """Бланк словами — ровно то, что печатает PNG."""
        lines = [
            self.get("vedomstvo").upper() or GOV_VEDOMSTVO,
            self.get("podrazdelenie").upper() or GOV_PODRAZDELENIE,
            "",
            self.number_line(),
            self.document_title(),
            "",
        ]
        if self.get("city") or self.get("doc_date"):
            lines.append(", ".join(x for x in (self.get("city"), self.get("doc_date")) if x))
        lines += ["", self.preamble(), "", "УСТАНОВИЛ:", "", self.facts_text(), "",
                  "ПОСТАНОВИЛ:", ""]
        for i, point in enumerate(self.decision_points(), 1):
            lines.append(f"{i}. {point}")
        basis = self.basis_text()
        if basis:
            lines += ["", "Основание: " + basis]
        if self.get("extra"):
            lines.append("Дополнительно: " + self.get("extra"))
        lines.append("")
        if self.get("author_name"):
            lines.append(f"Подпись: {self.get('author_name')}")
            if self.get("author_rank"):
                lines.append(f"Должность: {self.get('author_rank')}")
        return "\n".join(lines).strip() + "\n"

    def chat_txt_name(self) -> str:
        num = self.get("doc_number") or "б-н"
        return f"Постановление Прокурора {num}{base.DOC_MARKER_SUFFIX}"

    def history_payload(self) -> dict:
        data = self.to_dict()
        data["created_at"] = time.time()
        return data


# --------------------------------------------------------------------------- #
#  Разбор ответа ИИ
# --------------------------------------------------------------------------- #

def parse_gov_answer(answer_text: str, base_doc: PostanovlenieProkurora | None = None, base=None):
    base_doc = base_doc or base
    """Достаёт постановление Прокурора из ответа ИИ.

    Понимает тот же формат, что и IAD: строки «Ключ: значение», нумерованные
    пункты, заголовки «УСТАНОВИЛ»/«ПОСТАНОВИЛ». Возвращает None, если ответ
    не похож на документ.
    """
    text = str(answer_text or "")
    if not text.strip():
        return None
    if is_own_request(text):
        diag_event("gov.answer_is_own_request")
        return None
    block = extract_document_block(text)
    if block.strip():
        text = block

    doc = PostanovlenieProkurora.from_dict((base_doc or PostanovlenieProkurora()).to_dict())
    doc.source = "ai"
    doc.created_at = time.time()

    saw_marker = False
    saw_content = False
    points: list = []
    free_lines: list = []
    facts_lines: list = []
    in_facts = False
    in_points = False

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or is_ui_noise(line) or is_section_heading(line):
            continue
        compact = re.sub(r"[\s=*/\-_]+", "", line.upper())
        if "ПОСТАНОВЛЕНИЕ" in compact and compact.startswith("ПОСТАНОВЛЕНИЕ"):
            saw_marker = True
            continue
        if "КОНЕЦПОСТАНОВЛЕНИЯ" in compact or "КОНЕЦОТВЕТА" in compact:
            continue
        plain = strip_markdown(line)
        heading = plain.lower().strip(" .:")
        if heading.startswith(("установил", "у с т а н о в и л")):
            in_facts, in_points = True, False
            rest = re.sub(r"^[^:]*:\s*", "", plain).strip()
            if rest and rest.lower() != heading:
                facts_lines.append(rest)
                saw_content = True
            continue
        if heading.startswith(("постановил", "постановляющая", "резолютивная")):
            in_facts, in_points = False, True
            rest = re.sub(r"^[^:]*:\s*", "", plain).strip()
            if rest and rest.lower() != heading:
                for chunk in re.split(r"(?:^|\s)(?=\d{1,2}[.)]\s)", rest):
                    body = strip_markdown(re.sub(r"^\d{1,2}[.)]\s*", "", chunk.strip()))
                    if body:
                        points.append(body)
                        saw_content = True
            continue

        if ":" in line:
            raw_key, raw_value = line.split(":", 1)
            key = _normalize_gov_key(raw_key)
            value = strip_markdown(raw_value)
            if key and value:
                doc.set(key, value)
                if key not in ("doc_number", "doc_date"):
                    saw_content = True
                continue

        numbered = _NUMBERED.match(line)
        if numbered:
            body = strip_markdown(numbered.group(2))
            if body:
                if in_facts and not in_points:
                    facts_lines.append(body)
                else:
                    points.append(body)
                saw_content = True
                continue
        bullet = _BULLET.match(line)
        if bullet and strip_markdown(bullet.group(1)):
            (facts_lines if in_facts and not in_points else points).append(
                strip_markdown(bullet.group(1)))
            saw_content = True
            continue
        if plain:
            if in_points:
                for chunk in re.split(r"(?:^|\s)(?=\d{1,2}[.)]\s)", plain):
                    body = strip_markdown(re.sub(r"^\d{1,2}[.)]\s*", "", chunk.strip()))
                    if body:
                        points.append(body)
                        saw_content = True
            elif in_facts:
                facts_lines.append(plain)
                saw_content = True
            else:
                free_lines.append(plain)
                saw_content = True

    if points:
        doc.points = points
    if facts_lines:
        # Раздел «УСТАНОВИЛ» — это выводы прокурора, а не «Обстоятельства»:
        # раньше он приклеивался к обстоятельствам и печатался дважды.
        doc.established = " ".join(f.strip() for f in facts_lines if f.strip())
    if free_lines:
        joined = "\n".join(free_lines).strip()
        if not doc.get("situation"):
            doc.situation = joined
        elif joined and joined not in doc.get("situation"):
            doc.situation = (doc.get("situation") + "\n" + joined).strip()

    if not saw_marker and not (doc.get("applicant_name") or doc.points):
        return None
    if not saw_content:
        diag_event("gov.answer_without_content", chars=len(text))
        return None
    if not doc.get("doc_date"):
        doc.doc_date = today_ru()
    if not doc.get("doc_type"):
        doc.doc_type = GOV_DOC_TYPES[0]
    return doc


# --------------------------------------------------------------------------- #
#  Проверка по праву
# --------------------------------------------------------------------------- #

def _law_text(name_fragment: str, data_dir: Path | str | None = None) -> str:
    """Читает текст акта из законодательной базы (data/reestr/*.json)."""
    folder = Path(data_dir or Path(__file__).resolve().parent / "data") / "reestr"
    if not folder.is_dir():
        return ""
    for path in sorted(folder.glob("*.json")):
        if name_fragment.lower() in path.name.lower():
            try:
                import json
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return ""
            chunks = [str(p.get("text") or "") for p in data.get("posts") or []]
            chunks += [str(s.get("text") or "") for s in data.get("sections") or []]
            return "\n".join(chunks)
    return ""


#: Основания отказа по Регламенту (ст. 16, ч. 1) — для проверки «отказных».
GOV_REFUSAL_GROUNDS: tuple[tuple[str, str], ...] = (
    ("заявител", "невозможно установить личность заявителя"),
    ("предмет", "отсутствует предмет обращения"),
    ("описан", "отсутствует описание события правонарушения"),
    ("подпис", "отсутствует подпись заявителя"),
    ("форме", "обращение составлено не по установленной форме"),
    ("электронной почт", "не указаны электронная почта / телефон заявителя"),
)


def check_gov_document(doc: PostanovlenieProkurora) -> list:
    """Замечания по постановлению Прокурора. Пустой список = всё в порядке."""
    issues: list = []

    # 1. Внеигровая лексика (промт 1.19 п. 19) — та же проверка, что у IAD.
    issues.extend(base.check_ooc(doc))

    # 2. Сроки из Регламента: первичное рассмотрение 24 ч, по существу 72 ч.
    text = " ".join(doc.decision_points()).lower()
    for match in re.finditer(r"(\d+)\s*(час|ч\.|часов|сут)", text):
        hours = int(match.group(1))
        if "сут" in match.group(2):
            hours *= 24
        if hours > 72:
            issues.append(
                f"В постановлении указан срок {hours} ч, а Регламент (ст. 12) "
                "допускает не более 24 часов на принятие к рассмотрению и не "
                "более 72 часов на рассмотрение по существу.")

    # 3. Отказ без основания (ст. 16 Регламента).
    low_type = doc.get("doc_type").lower()
    if "отказ" in low_type:
        # основание отказа может быть указано и в самой резолюции («Основание
        # отказа: пункт 1 статьи 16 Регламента»), поэтому её тоже учитываем
        basis = (doc.get("legal_basis") + " " + doc.get("violation_clause") + " "
                 + doc.facts_text() + " " + " ".join(doc.decision_points())).lower()
        if not any(word in basis for word, _reason in GOV_REFUSAL_GROUNDS) \
                and "регламент" not in basis:
            issues.append(
                "Постановление об отказе должно содержать конкретное основание "
                "из статьи 16 Регламента подачи обращений (нет описания события, "
                "невозможно установить заявителя, обращение не по форме и т. п.).")

    # 4. Обжалование: 72 часа (ст. 27–28 Регламента). Статья 27 требует
    # указывать порядок обжалования для отказа, переадресации и прекращения.
    appeal_needed = any(word in low_type for word in ("отказ", "переадрес", "прекращ"))
    if appeal_needed and not re.search(r"обжалов",
                                       " ".join(doc.decision_points()).lower()):
        issues.append(
            "В резолютивной части нет порядка обжалования. По статьям 27–28 "
            "Регламента решение обжалуется вышестоящему прокурору или в суд в "
            "срок, не превышающий 72 часа.")

    # 5. Полномочия: постановление подписывает прокурор.
    if not doc.get("author_name"):
        issues.append("Не указано, кто вынес постановление (Прокурор).")

    # 6. Реквизиты обращения: без них постановление «по обращению» повисает.
    if not doc.get("applicant_name"):
        issues.append("Не указан заявитель — из постановления нельзя понять, "
                      "по чьему обращению оно вынесено.")
    if not doc.get("appeal_date") and not doc.get("doc_date"):
        issues.append("Не указана дата обращения — нарушается вводная часть.")

    return issues


# --------------------------------------------------------------------------- #
#  Ассеты бланка прокуратуры
# --------------------------------------------------------------------------- #

def gov_assets(base_dir: Path | str | None = None):
    """Герб/печать/шрифты для бланка прокуратуры.

    Отличается от IAD только картинками: `data/gerb_gov.png` (герб Офиса
    Генерального прокурора) и `data/stamp_gov.png` (печать DEPARTMENT OF
    JUSTICE). Если их нет — используются штатные, чтобы бланк не остался пустым.
    """
    import doc_render as render

    root = Path(base_dir) if base_dir else Path(__file__).resolve().parent
    assets = render.DocAssets(root)
    for folder in [root / "data", root] + list(render._resource_dirs()):
        coat = folder / "gerb_gov.png"
        stamp = folder / "stamp_gov.png"
        if coat.is_file():
            assets.coat = coat
        if stamp.is_file():
            assets.stamp = stamp
        if coat.is_file() and stamp.is_file():
            break
    return assets
