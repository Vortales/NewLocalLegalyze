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
    _clean, act_genitive, clause_genitive, diag_event, extract_document_block,
    has_answer_end, initials_signature, is_own_request, is_section_heading,
    is_ui_noise, is_vague_clause, normalize_clause, scrub_chat_text,
    split_subject, strip_markdown, today_ru, _BULLET, _NUMBERED,
)

#: Значения-заглушки: ИИ подставляет их, когда данных нет (v37).
_GOV_PLACEHOLDER = re.compile(
    r"(по\s+обстоятельствам,?\s+указанным\s+в\s+заявке|"
    r"согласно\s+обстоятельствам,?\s+указанным\s+в\s+заявке|"
    r"по\s+указанным\s+обстоятельствам|"
    r"на\s+основании\s+обстоятельств,?\s+указанных\s+в\s+заявке|"
    r"в\s+ходе\s+служебной\s+деятельности|"
    r"в\s+процессе\s+служебной\s+деятельности|"
    r"не\s+установлен[оаы]?|нет\s+данных|"
    r"информация\s+отсутствует|данные\s+отсутствуют)",
    re.IGNORECASE,
)


#: «Место события»-заглушки: это описание процесса, а не адрес.
_NOT_A_PLACE = re.compile(
    r"(в\s+ходе|в\s+процессе|в\s+результате|при\s+исполнении|"
    r"служебн\w*\s+деятельност|служебн\w*\s+обязанност)",
    re.IGNORECASE,
)


def is_placeholder(value: str) -> bool:
    """Значение — заглушка ИИ («по обстоятельствам, указанным в заявке»)."""
    text = _clean(value)
    if not text:
        return True
    return bool(_GOV_PLACEHOLDER.match(text.strip(" .,;")))


def is_place_placeholder(value: str) -> bool:
    """«Место события: в ходе служебной деятельности» — заглушка, не адрес."""
    text = _clean(value)
    if not text or is_placeholder(text):
        return True
    return bool(_NOT_A_PLACE.search(text))


def strip_placeholder_clauses(text: str) -> str:
    """Выбрасывает из абзаца фразы-заглушки (v37).

    Реальная выгрузка AG-2: «УСТАНОВИЛ» начинался со строки «Место события:
    в ходе служебной деятельности на территории штата Сан-Андреас», хотя
    место события в деле не установлено. Такие фразы — не факт, а заполнитель.
    """
    raw = str(text or "")
    if not raw.strip():
        return ""
    cleaned: list = []
    for chunk in re.split(r"(?<=[.!?])\s+|\n+", raw):
        body = chunk.strip()
        if not body:
            continue
        if re.match(r"^место события\s*:", body, re.IGNORECASE) and is_place_placeholder(
                re.sub(r"^место события\s*:\s*", "", body, flags=re.IGNORECASE)):
            continue
        if is_chat_junk(body):
            continue
        cleaned.append(body)
    return " ".join(cleaned).strip()


def drop_placeholders(doc) -> None:
    """Чистит поля постановления от заглушек, чтобы они не попали в текст."""
    for key in ("violation_act", "violation_clause", "event_place",
                "event_date", "legal_basis", "penalty", "evidence", "extra"):
        value = doc.get(key)
        if value and is_placeholder(value):
            diag_event("gov.placeholder_dropped", field=key, value=str(value)[:60])
            doc.set(key, "")


#: Шапка бланка прокуратуры.
GOV_VEDOMSTVO = "STATE OF SAN-ANDREAS"
#: Город в бланке всегда один и тот же (замечание v37: «Город всегда Лос-Сантос»).
GOV_CITY = "г. Лос-Сантос"
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

#: Поля, которые печатаются в заявке для чата. Пустые не печатаются вовсе:
#: «нет данных — определи сам» в заявке раздражало и подсказывало ИИ
#: заглушки вместо нормы (v37).
#: В чат уходят только реквизиты самого постановления: заявителя, нарушителя,
#: предмет и дату события ИИ берёт из текста обращения, которое печатается
#: ниже целиком (замечание v37: «пусть ИИ сам читает и ловит суть»).
GOV_CHAT_KEYS: tuple[str, ...] = (
    "vedomstvo", "podrazdelenie", "doc_type", "doc_number", "doc_date",
    "author_name", "author_rank", "author_id", "city",
)
GOV_CHAT_FIELDS: tuple[tuple[str, str, str], ...] = tuple(
    row for row in GOV_FIELDS if row[0] in GOV_CHAT_KEYS)

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


#: Пункты формы обращения в ОГП (Регламент подачи обращений, ст. 6–7).
APPEAL_POINTS: tuple[tuple[str, str], ...] = (
    ("данные о заявителе", "applicant_name"),
    ("данные о представителе заявителя", "applicant_representative"),
    ("предмет обращения", "appeal_subject"),
    ("сведения о нарушителе", "violator"),
    ("дата и время события", "event_date"),
    ("подробное описание ситуации", "description"),
    ("ксерокопия идентификационной карты", "id_copy"),
    ("электронную почту и номер телефона", "contacts"),
    ("доказательства правонарушения", "evidence"),
    ("копия договора о представлении интересов", "contract"),
    ("документ, подтверждающий полномочия представителя", "authority"),
    ("дата подачи заявления", "filed_date"),
    ("подпись и расшифровка заявителя", "signature"),
)


def parse_appeal_text(raw: str) -> dict:
    """Разбирает текст обращения гражданина по пунктам формы ОГП (v37).

    Реальная ситуация: пользователь вставил в «Обстоятельства» ВСЁ обращение
    целиком — с «От:», «В:», нумерацией, почтой и подписью. В постановление
    этот текст уходил дословно, а в «УСТАНОВИЛ» появлялась строка
    «Место события: в ходе служебной деятельности». Теперь обращение
    разбирается на реквизиты, а обстоятельствами становится только описание
    ситуации (п. 5).
    """
    text = str(raw or "").replace("\r\n", "\n")
    result: dict = {}
    if not text.strip():
        return result

    buffer: dict = {}
    current = ""

    def flush(key: str) -> None:
        if not key:
            return
        value = " ".join(x.strip() for x in buffer.get(key, []) if x.strip())
        value = re.sub(r"\s{2,}", " ", value).strip(" ;,")
        if value and not result.get(key):
            result[key] = value

    for line in text.splitlines():
        clean = line.strip()
        if not clean:
            if current:
                buffer.setdefault(current, []).append("")
            continue
        # «От: Kirito Gold 178393» и «1.2 …» — не пункты формы обращения, а
        # нумерация списка; «От:» вообще не реквизит и разбор не открывает.
        if re.match(r"^(от|в|кому|куда)\s*:", clean, re.IGNORECASE):
            continue
        stripped = re.sub(r"^(?:\d{1,2}\.\d{1,2}|\d{1,2})[.)]?\s*", "", clean)
        low = stripped.lower()
        matched = ""
        for prefix, key in APPEAL_POINTS:
            if low.startswith(prefix):
                matched = key
                rest = stripped[len(prefix):].lstrip(" :—-")
                # v37: «Ксерокопия идентификационной карты ИЛИ ДРУГИХ
                # документов, удостоверяющих личность…» — продолжение названия
                # пункта, а не значение поля. В поле попадал только хвост
                # «или других документов…».
                if rest.lower().startswith(("или", "либо")):
                    rest = stripped
                flush(current)
                current = key
                buffer.setdefault(key, [])
                if rest:
                    buffer[key].append(rest)
                break
        if matched:
            continue
        if current:
            buffer.setdefault(current, []).append(clean)
    flush(current)

    # «5. Подробное описание ситуации:» мог быть пуст — тогда в поле попал
    # следующий реквизит. Такой «пересказ» описанием ситуации не считается.
    description = result.get("description", "")
    if description.lower().startswith(("подпись", "дата подачи", "доказательства",
                                      "ксерокопия", "электронную почту")):
        result.pop("description", None)
    else:
        # Хвост формы («Настоящим заверяю… К заявлению прилагаю:») — это не
        # описание события, а служебные строки обращения (выгрузка v37).
        description = re.split(r"Настоящим заверяю", description)[0].strip()
        description = re.split(r"К заявлению прилагаю", description)[0].strip()
        if description:
            result["description"] = description
        else:
            result.pop("description", None)

    # «Kirito Gold 178393» -> ФИО + ID
    applicant = result.get("applicant_name", "")
    name, ident = split_subject(applicant, "")
    if name:
        result["applicant_name"] = name
    if ident:
        result["applicant_id"] = ident

    # «Todd Spencer 181956: FIB» -> ФИО, номер, организация
    violator = result.get("violator", "")
    if violator:
        head = re.split(r"[:;,]", violator, maxsplit=1)[0].strip()
        tail = violator[len(head):].lstrip(" :;,—-")
        vname, vid = split_subject(head, "")
        result["violator"] = vname or head
        if vid:
            result["violator_id"] = vid
        if tail:
            result["violator_department"] = tail.strip(" ;,:—-")

    return result


_APPEAL_FORM_MARKS = (
    "предмет обращения", "сведения о нарушителе", "данные о заявителе",
    "подробное описание ситуации", "дата подачи заявления",
    "подпись и расшифровка заявителя",
)


#: Строка-реквизит формы обращения: «5. Подробное описание ситуации: …».
_APPEAL_FORM_LINE = re.compile(
    r"^\s*(?:\d{1,2}[.)]?\s*)?(?:"
    + "|".join(re.escape(mark) for mark in _APPEAL_FORM_MARKS)
    + r")\s*:",
    re.IGNORECASE | re.MULTILINE,
)


def looks_like_appeal_form(text: str) -> bool:
    """Текст — бланк обращения гражданина в ОГП, а не описание ситуации.

    Считаем не упоминания слов, а строки-реквизиты: официальный пересказ
    («предмет обращения соответствует установленным категориям») бланком
    не считается, а «1. Данные о заявителе: …» — считается.
    """
    raw = str(text or "")
    if not raw.strip():
        return False
    if len(_APPEAL_FORM_LINE.findall(raw)) >= 2:
        return True
    low = raw.lower()
    return sum(1 for mark in _APPEAL_FORM_MARKS if f"{mark}:" in low) >= 3


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


_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _key(text: str) -> str:
    """Нормализованный ключ предложения для поиска дублей."""
    return re.sub(r"[^\wА-Яа-яЁё]+", " ", str(text or "").lower()).strip()


def sentences(text: str) -> list:
    """Делит текст на законченные предложения (для склейки без повторов)."""
    parts = [p.strip() for p in _SENT_SPLIT.split(str(text or "").strip()) if p.strip()]
    return parts


#: Строки интерфейса веб-чата, которые ИИ копирует в ответ (v37).
_CHAT_JUNK = re.compile(
    r"^(pdf|file|docx?|txt|png|jpe?g|image|изображение|вложение|"
    r"файл(ы)? приложен\w*|вы отправили \d+|ответ в режиме ии.*|"
    r"чат в режиме ии:?.*)$",
    re.IGNORECASE,
)


def is_chat_junk(line: str) -> bool:
    """Строка — элемент интерфейса чата, а не текст документа."""
    text = strip_markdown(str(line or "")).strip(" .:;«»\"'")
    if not text:
        return True
    if _CHAT_JUNK.match(text):
        return True
    # «Заявка (отправлено в чат).txt», «scan_1234.pdf»
    if re.search(r"\.(pdf|docx?|txt|png|jpe?g)$", text, re.IGNORECASE):
        return True
    return False


#: Эхо собственного документа: веб-клиент вернул отправленный текст вместе
#: с ответом, и шапка/вводная часть попадали в «Обстоятельства» (v37).
_GOV_ECHO = re.compile(
    r"^(state of san-andreas|office of the attorney general|"
    r"я,\s*прокурор|г\.\s*лос-сантос|постановление\s*№)",
    re.IGNORECASE,
)


def is_gov_echo(line: str) -> bool:
    """Строка — повтор шапки/вводной части документа, а не факт дела."""
    return bool(_GOV_ECHO.match(strip_markdown(str(line or "")).strip(" .:;")))


def append_unique(parts: list, text: str) -> None:
    """Добавляет предложения, которых ещё нет в `parts`.

    В выгрузке AG-2 «УСТАНОВИЛ» дважды повторял место события и один и тот же
    вывод: строки-поля ИИ склеивались с абзацем обстоятельств подряд.
    """
    known = _key(" ".join(parts))
    for sentence in sentences(scrub_chat_text(text)):
        body = sentence.strip(" ;,")
        if not body or is_chat_junk(body) or is_gov_echo(body):
            continue
        if is_placeholder(body) or _GOV_PLACEHOLDER.match(body):
            continue
        # «…образуют нарушение: по обстоятельствам, указанным в заявке» —
        # заглушка может быть и в середине предложения
        if re.search(r"(образуют нарушение|квалификац\w+)[:;]?\s*"
                     r"(по обстоятельствам|согласно обстоятельствам)", body, re.IGNORECASE):
            continue
        key = _key(body)
        if key and key in known:
            continue
        parts.append(body)
        known = (known + " " + key).strip()


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
    city: str = GOV_CITY
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
    #: «Обстоятельства» — собственное описание прокурора по видеофиксации (v37).
    #: Пусто — значит «Обстоятельства» взяты из обращения (`appeal_situation`),
    #: и в «УСТАНОВИЛ» они второй раз не печатаются, если ИИ дал свой текст.
    prosecutor_facts: str = ""
    #: Пересказ заявителя из пункта 5 обращения — только запасной вариант.
    appeal_situation: str = ""
    #: Текст обращения гражданина ЦЕЛИКОМ (v37). Отдельных полей заявителя и
    #: нарушителя в форме больше нет: заявитель, нарушитель, предмет, дата
    #: события и тайм-коды достаются из этого текста (`apply_appeal`).
    appeal_raw: str = ""
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

    # --------------------------------------------------- обращение целиком

    def apply_appeal(self, raw: str) -> None:
        """Раскладывает текст обращения на реквизиты (v37).

        В форме осталось одно поле «Обращение»: граждане подают обращения по
        одной форме, поэтому заявитель, его ID, нарушитель, предмет, дата
        события и доказательства достаются из самого текста. Уже заполненные
        поля не перезаписываются пустыми значениями ИИ-ответа.
        """
        self.appeal_raw = str(raw or "").strip()
        if not self.appeal_raw:
            return
        parsed = parse_appeal_text(self.appeal_raw)
        mapping = {
            "applicant_name": parsed.get("applicant_name"),
            "applicant_id": parsed.get("applicant_id"),
            "applicant_representative": parsed.get("applicant_representative"),
            "appeal_subject": parsed.get("appeal_subject"),
            "subject_name": parsed.get("violator"),
            "subject_id": parsed.get("violator_id"),
            "subject_department": parsed.get("violator_department"),
            "event_date": parsed.get("event_date"),
            "filed_date": parsed.get("filed_date"),
        }
        for key, value in mapping.items():
            if value and not self.get(key):
                self.set(key, value)
            elif value and key == "subject_department" and not self.get(key):
                self.set(key, value)
        if parsed.get("filed_date") and not self.get("appeal_date"):
            self.appeal_date = parsed["filed_date"]
        evidence = parsed.get("evidence")
        if evidence and not self.get("evidence"):
            self.evidence = evidence
        if not self.get("situation") and parsed.get("description"):
            self.situation = parsed["description"]
        description = parsed.get("description")
        if description:
            self.appeal_situation = description
        if not self.get("city"):
            self.city = GOV_CITY

    def appeal_raw_text(self) -> str:
        """Восстанавливает текст обращения для истории и повторной правки."""
        if self.get("appeal_raw"):
            return self.get("appeal_raw")
        lines = []
        if self.get("applicant_name"):
            ident = f" {self.get('applicant_id')}" if self.get("applicant_id") else ""
            lines.append(f"1. Данные о заявителе: {self.get('applicant_name')}{ident}")
        if self.get("appeal_subject"):
            lines.append(f"2. Предмет обращения: {self.get('appeal_subject')}")
        if self.get("subject_name"):
            ident = f" {self.get('subject_id')}" if self.get("subject_id") else ""
            lines.append(f"3. Сведения о нарушителе: {self.get('subject_name')}{ident}")
        if self.get("event_date"):
            lines.append(f"4. Дата и время события: {self.get('event_date')}")
        if self.get("situation"):
            lines.append(f"5. Подробное описание ситуации: {self.get('situation')}")
        if self.get("evidence"):
            lines.append(f"8. Доказательства правонарушения: {self.get('evidence')}")
        return "\n".join(lines)

    def _prosecutor_own_facts(self) -> bool:
        """«Обстоятельства» заполнены прокурором по видеофиксации, а не бланком."""
        situation = strip_placeholder_clauses(scrub_chat_text(self.get("situation")))
        if not situation:
            return False
        if self.prosecutor_facts:
            return True
        description = self.appeal_situation
        if description and _key(situation) == _key(description):
            return False
        return not looks_like_appeal_form(situation)

    def facts_text(self) -> str:
        """«УСТАНОВИЛ» — фактические обстоятельства дела."""
        parts: list = []
        # v37: если в «Обстоятельства» вставили бланк обращения — берём из него
        # только описание ситуации; остальные реквизиты уже в своих полях.
        situation = strip_placeholder_clauses(scrub_chat_text(self.get("situation")))
        if looks_like_appeal_form(situation):
            # В «Обстоятельства» вставили бланк обращения целиком — в документ
            # идёт только пункт «Подробное описание ситуации», остальные
            # реквизиты уже разложены по полям (v37).
            situation = parse_appeal_text(situation).get("description", "")
        established = strip_placeholder_clauses(scrub_chat_text(self.get("established")))
        # v37 (вторая итерация): в выгрузке AG-2 «УСТАНОВИЛ» сначала шёл текст
        # ИИ по обращению, а следом целиком повторялся пересказ заявителя из
        # пункта 5. Повторяем его только тогда, когда «УСТАНОВИЛ» пуст.
        if established and situation and not self._prosecutor_own_facts():
            situation = ""
        # Порядок: сначала факты прокурора по видеофиксации, затем правовая
        # оценка ИИ — иначе «УСТАНОВИЛ» заканчивался выводом и снова начинался
        # описанием записи (замечание второй итерации v37).
        append_unique(parts, situation)
        append_unique(parts, established)

        joined = " ".join(parts).lower()
        when = self.get("event_date")
        place = self.get("event_place")
        if is_place_placeholder(place):
            place = ""
        date_part = ""
        m = re.match(r"\s*(\d{1,2}\.\d{1,2}\.\d{2,4})", when or "")
        if m:
            date_part = m.group(1)
        need_when = bool(when) and when.lower() not in joined \
            and (not date_part or date_part not in joined)
        need_place = bool(place) and not _covered(place, joined)
        # «в ходе служебной деятельности» — не место события, а описание
        # процесса: такие заглушки выбрасываем ещё до вставки в текст.
        if need_when and need_place:
            parts.insert(0, f"Событие имело место: {when}, место — {place}.")
        elif need_when:
            parts.insert(0, f"Событие имело место: {when}.")
        elif need_place:
            parts.insert(0, f"Место события: {place}.")

        # «Указанные обстоятельства образуют нарушение: по обстоятельствам,
        # указанным в заявке» — заглушка, в документе её печатать нельзя.
        clause = "" if is_vague_clause(self.get("violation_clause")) \
            else self.get("violation_clause")
        act = self.get("violation_act")
        if clause or act:
            phrase = " ".join(x for x in (clause_genitive(clause) if clause else "",
                                          act_genitive(act) if act else "") if x)
            if phrase:
                parts.append(f"Указанные обстоятельства образуют нарушение: {phrase}.")

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
                          "момента подписания и публикации.")
            points.append(self.appeal_point())
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
                      "Сан-Андреас.")
        points.append(self.appeal_point())
        return points

    def basis_text(self) -> str:
        parts = []
        act = self.get("violation_act")
        if self.get("violation_clause"):
            clause = normalize_clause(self.get("violation_clause"), act)
            # ИИ присылает «Пункт 8.5 Устава LSCSD — нарушение дресс-кода»,
            # и к этому приклеивалось ещё «Устава Los Santos County …»
            # (выгрузка v37). Оставляем только номер пункта с пояснением:
            # название акта печатается отдельным куском ниже.
            clause = _clean(clause)
            num = re.match(r"(?:пункт[а]?\s*)?(\d+(?:\.\d+)+)", clause, re.IGNORECASE)
            if num:
                tail = clause[num.end():].lstrip(" )—-,")
                tail = re.sub(r"^устава?\s+\S+.*$", "", tail, flags=re.IGNORECASE)
                clause = f"пункт {num.group(1)}"
                if tail and len(tail) > 3:
                    clause += f" ({tail.strip(' .')})"
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
        """Обязательные реквизиты постановления (v37).

        Заявитель больше не вводится отдельно: он берётся из обращения, и если
        в тексте его нет — это видно в подсказке формы. Обязательно само
        обращение (или обстоятельства, если прокурор пишет их сам).
        """
        required = ("doc_number", "doc_date", "author_name")
        missing = [GOV_FORM_LABELS[k] for k in required if not self.get(k)]
        if not self.get("appeal_raw") and not self.get("applicant_name") \
                and not self.get("situation"):
            missing.append("Обращение")
        return missing

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
            "points", "created_at", "source", "established", "appeal_raw",
            "prosecutor_facts", "appeal_situation"}
        kwargs = {k: v for k, v in data.items() if k in allowed}
        doc = cls(**kwargs)
        doc.points = [_clean(p) for p in (data.get("points") or []) if _clean(p)]
        return doc

    def to_chat_txt(self) -> str:
        """Заявка в чат: реквизиты постановления + обращение ЦЕЛИКОМ (v37).

        Отдельные поля заявителя и нарушителя убраны из формы, поэтому в чат
        уходит текст обращения как есть — ИИ сам находит в нём заявителя,
        нарушителя, предмет, дату события и тайм-коды.
        """
        lines = ["ЗАЯВКА НА ПОСТАНОВЛЕНИЕ ПРОКУРОРА"]
        lines.append("Данные для подготовки документа:")
        self._ensure_derived_fields()
        for key, _form, label in GOV_CHAT_FIELDS:
            value = self.get(key)
            if not value:
                continue
            if key == "appeal_raw":
                continue
            lines.append(f"{label}: {value}")
        appeal = self.get("appeal_raw")
        if appeal:
            lines.append("")
            lines.append("ОБРАЩЕНИЕ ГРАЖДАНИНА (текст целиком, форма ОГП):")
            lines.append(appeal)
        elif self.get("situation"):
            lines.append("")
            lines.append("ОБСТОЯТЕЛЬСТВА, УСТАНОВЛЕННЫЕ ПРОКУРОРОМ:")
            lines.append(self.get("situation"))
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

    def _ensure_derived_fields(self) -> None:
        """Достаёт из обращения то, чего ещё нет в полях (v37)."""
        if self.get("appeal_raw"):
            self.apply_appeal(self.get("appeal_raw"))
        elif self.get("situation"):
            parsed = parse_appeal_text(self.get("situation"))
            if parsed.get("applicant_name") and not self.get("applicant_name"):
                self.set("applicant_name", parsed["applicant_name"])
                if parsed.get("applicant_id"):
                    self.set("applicant_id", parsed["applicant_id"])
            if parsed.get("violator") and not self.get("subject_name"):
                self.set("subject_name", parsed["violator"])
                if parsed.get("violator_id"):
                    self.set("subject_id", parsed["violator_id"])
            if parsed.get("event_date") and not self.get("event_date"):
                self.set("event_date", parsed["event_date"])
        if not self.get("city"):
            self.city = GOV_CITY

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

#: Строки подписи в конце резолюции. ИИ нумерует их как пункты
#: («6. Прокурор штата…», «7. J. Raine»), и они попадали в резолюцию.
_GOV_SIGNATURE = re.compile(
    r"^(прокурор(\s+штата)?|подпись|должность|удостоверение|"
    r"[А-ЯЁA-Z]\.\s?[А-ЯЁA-Z][а-яёa-z]+$|"
    r"[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+$|"
    r"(?:г\.\s*)?[А-ЯЁ][а-яё-]+(?:-[А-ЯЁ][а-яё-]+)?,\s*\d{2}\.\d{2}\.\d{4})",
)


def is_signature_line(line: str) -> bool:
    """Строка — элемент подписи (должность, ФИО, удостоверение), не пункт."""
    text = strip_markdown(str(line or "")).strip(" .;,")
    if not text:
        return False
    low = text.lower()
    if "удостоверение" in low or low.startswith(("подпись", "должность")):
        return True
    if low.startswith("прокурор") and "постановил" not in low:
        return True
    # «И. Рейн», «Jonathan Raine» — ФИО из 2–3 слов без глаголов приказа.
    words = text.split()
    if len(words) <= 3 and not re.search(r"\b(назначить|принять|возбудить|обязать|"
                                         r"отказать|направить|уведомить|прекратить)\b",
                                         low):
        return bool(re.fullmatch(r"(?:[А-ЯЁA-Z]\.?\s?)?(?:[А-ЯЁA-Z][а-яёa-z-]+\.?"
                                 r"(?:\s+[А-ЯЁA-Z][а-яёa-z-]+\.?){0,2}|"
                                 r"[А-ЯЁA-Z]\.\s?[А-ЯЁA-Z]\.(?:\s?[А-ЯЁA-Z]\.)?)", text))
    return False


def strip_signature_points(points: list) -> list:
    """Убирает пункты-подписи из резолюции (v37)."""
    kept = [p for p in points or [] if p and not is_signature_line(p)]
    return kept


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
        if is_chat_junk(line) or is_gov_echo(line):
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
        doc.points = strip_signature_points(points)
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

    # v37: заявителя и нарушителя в форме больше нет — их достаём из текста
    # ответа. «…обращение гражданина Mark Skywalker от 27.10.2026…»,
    # «…сотрудника Los Santos County Sheriff Department Kyria Suspectzx с
    # номером опознавательного знака 8332…».
    if not doc.get("applicant_name"):
        found = re.search(r"обращени\w+\s+(?:гражданина|гражданки|заявителя)\s+"
                          r"([А-ЯЁA-Z][а-яёa-z]+(?:\s+[А-ЯЁA-Z][а-яёa-z]+)?)",
                          text)
        if found:
            doc.set("applicant_name", found.group(1))
    if not doc.get("subject_name"):
        found = re.search(r"(?:сотрудник\w*|в\s+отношении)\s+(?:[^,.]{0,80}?)\s?"
                          r"([А-ЯЁA-Z][а-яёa-z]+\s+[А-ЯЁA-Z][а-яёa-z]+)\s*"
                          r"(?:с\s+номером\s+опознавательного|,|\.)",
                          text)
        if found:
            doc.set("subject_name", found.group(1))
    if not doc.get("subject_id"):
        found = re.search(r"опознавательн\w+\s+знак\w*\s*(?:№\s*)?(\d{3,10})", text)
        if found:
            doc.set("subject_id", found.group(1))

    # v37: ответ ИИ не содержит поля «Постановление вынес …» — имя прокурора
    # есть только в преамбуле «Я, Прокурор штата Сан-Андреас Jonathan Raine,
    # удостоверение № 198632». Без этого проверка считала, что автор не указан.
    if not doc.get("author_name"):
        found = re.search(
            r"Я,\s*(?:заместитель\s+)?(?:генерального\s+)?прокурор\w*[^,]{0,40}?"
            r"\s([А-ЯЁA-Z][а-яёa-z]+(?:\s+[А-ЯЁA-Z][а-яёa-z]+){1,2})\s*,",
            text, re.IGNORECASE)
        if found:
            doc.author_name = found.group(1).strip()
    if not doc.get("author_id"):
        found_id = re.search(r"удостоверен\w*\s*№\s*(\d{3,10})", text, re.IGNORECASE)
        if found_id:
            doc.author_id = found_id.group(1)

    drop_placeholders(doc)
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

    # 7. Ссылка на материалы, которых в обращении нет (v37).
    #    В выгрузке AG-2 «УСТАНОВИЛ» утверждал: «Изложенные факты и
    #    прикрепленные к кадровым и юридическим договорам сведения
    #    достаточны…», хотя таких документов в деле не было.
    facts_low = doc.facts_text().lower()
    sources_low = " ".join([doc.get("evidence"), doc.get("situation"),
                            doc.get("extra")]).lower()
    invented = [phrase for phrase in (
        "кадровым и юридическим договорам", "кадровым договорам",
        "юридическим договорам", "прикрепленные к договорам",
        "материалами дела подтверждается", "согласно материалам дела")
        if phrase in facts_low and not any(
            word in sources_low for word in ("договор", "материал", "доказательств"))]
    if invented:
        issues.append(
            "В «УСТАНОВИЛ» есть ссылка на документы, которых нет в обращении ("
            + "; ".join(invented) + "). Опишите только те факты и материалы, "
            "которые заявитель действительно приложил.")

    # 8. Обжалование без ссылки на норму (ст. 27–28 Регламента).
    resolution = " ".join(doc.decision_points()).lower()
    has_norm = ("регламент" in resolution or "регламента" in resolution
                or "статьи 27" in resolution or "статьями 27" in resolution
                or "ст. 27" in resolution or "статей 27" in resolution)
    if "обжалов" in resolution and not has_norm:
        issues.append(
            "Порядок обжалования указан без правовой нормы. По статьям 27–28 "
            "Регламента подачи обращений решение обжалуется вышестоящему "
            "прокурору или в суд в срок, не превышающий 72 часа, — сошлитесь "
            "на эти статьи.")

    # 9. Обращение без описания ситуации (v37). В выгрузке AG-2 пункт 5
    #    «Подробное описание ситуации» был пуст, и ИИ дописал факты от себя
    #    («прикрепленные к кадровым и юридическим договорам сведения»).
    situation = doc.get("situation")
    if looks_like_appeal_form(situation):
        description = parse_appeal_text(situation).get("description", "")
        if not description or description.lower().startswith("настоящим заверяю"):
            issues.append(
                "В обращении не заполнен пункт «Подробное описание ситуации» — "
                "постановление опирается на факты, которых в обращении нет. "
                "Попросите заявителя описать событие или укажите это как "
                "основание для запроса дополнительных материалов.")

    # 10. Заглушки вместо правовой оценки (v37).
    if is_placeholder(doc.get("violation_clause")) and doc.get("violation_clause"):
        issues.append(
            "Нарушенная норма указана заглушкой («по обстоятельствам, указанным "
            "в заявке»). Нужен конкретный пункт устава или статья закона — "
            "иначе правовая оценка в постановлении отсутствует.")

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
