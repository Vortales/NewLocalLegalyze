"""ogp_stages.py — три этапа обращения и цепочки-варианты этапа 2 (v62).

Замечание пользователя (v62): «Цепочка этапов» должна полностью заменить
старую вкладку «Постановление»; этапов три, кнопка одна и она называет
текущий узел («Этап 1» → «Этап 2.1» → «Этап 2.2» → «Этап 3»), а .png
печатает отдельная кнопка «Сформировать» у каждого этапа/подэтапа.

Согласованное ТЗ (ответы пользователя на вопросы перед разработкой):

1. **Этап 1** — ровно три состояния: принятие / отказ / переадресация.
   Отказ и переадресация для нас равны: обращение ушло, дело закрыто,
   этапы 2 и 3 не наступают.
2. **Этап 2** наступает только при принятии. ИИ присылает **минимум три
   цепочки** (варианта порядка постановлений), у каждой свой прогресс; активна
   одна, переключаться можно в любой момент, прогресс других не стирается.
   Внутри цепочки подэтапы 2.1, 2.2, … идут строго по порядку.
3. **Этап 3** — без подэтапов: ИИ по смыслу обращения, ответа этапа 1 и всех
   пройденных подэтапов САМ выбирает вид завершения (суд, завершение
   производства, санкция, прекращение). Прокурор может поправить и
   пересобрать.
4. У каждого узла два поля: **поле 1** — что ввёл прокурор (правится), **поле
   2** — ответ ИИ (только чтение). Если поле 1 изменилось после ответа, поле 2
   помечается «устарело» и дальше по цепочке идти нельзя.
5. У каждого узла своя кнопка «Сформировать» и индикатор «PNG сформирован».

Модуль не зависит от Qt: раскладку, подписи кнопок и состояние цепочки
проверяют обычные тесты.
"""

from __future__ import annotations

import re

import ogp_chain

# --------------------------------------------------------------------------- #
#  Этапы
# --------------------------------------------------------------------------- #

ETAP_1 = 1
ETAP_2 = 2
ETAP_3 = 3

#: Сколько вариантов цепочки ИИ обязан прислать на этапе 1.
MIN_CHAINS = 3
MAX_CHAINS = 5
MAX_STEPS = 12

#: Состояния этапа 1 (v62: три, а не пять).
STATE_ACCEPT = ogp_chain.OUTCOME_ACCEPT       # «accept»
STATE_REFUSE = ogp_chain.OUTCOME_REFUSE       # «refuse»
STATE_REDIRECT = ogp_chain.OUTCOME_REDIRECT   # «redirect»

#: Порядок кнопок этапа 1 в форме.
STAGE1_STATES: tuple = (STATE_ACCEPT, STATE_REFUSE, STATE_REDIRECT)

STAGE1_LABELS: dict = {
    STATE_ACCEPT: "принятие",
    STATE_REFUSE: "отказ",
    STATE_REDIRECT: "переадресация",
}

#: Короткое пояснение к каждому состоянию этапа 1.
STAGE1_HINTS: dict = {
    STATE_ACCEPT: "обращение по форме и нашего ведомства — открывается этап 2",
    STATE_REFUSE: "основание статьи 16 Регламента с номером пункта — дело закрыто",
    STATE_REDIRECT: "вопрос чужого органа — обращение ушло, дело закрыто",
}

#: Состояния, которые закрывают дело для нас.
CLOSING_STATES: tuple = (STATE_REFUSE, STATE_REDIRECT)


def stage1_label(state: str) -> str:
    "Подпись состояния этапа 1."
    return STAGE1_LABELS.get(state, "решение")


def stage1_closes(state: str) -> bool:
    "Закрывает ли это состояние дело для нас (этапы 2 и 3 не наступают)."
    return str(state or "") in CLOSING_STATES


def stage1_from_outcome(outcome: str) -> str:
    """Старый пятиисходный ответ ИИ (v60–v61) → три состояния v62.

    «Недостатки» и «прекращение» из прежней блок-схемы для нас означают, что
    обращение не пошло в работу: недостатки — заявитель не устранил форму,
    прекращение — обращение отозвано. Оба сводятся к отказу, чтобы старые
    дела открывались в новой форме без потери решения.
    """
    outcome = str(outcome or "")
    if outcome == ogp_chain.OUTCOME_ACCEPT:
        return STATE_ACCEPT
    if outcome == ogp_chain.OUTCOME_REDIRECT:
        return STATE_REDIRECT
    if outcome in (ogp_chain.OUTCOME_REFUSE, ogp_chain.OUTCOME_DEFECTS,
                   ogp_chain.OUTCOME_STOP):
        return STATE_REFUSE
    return ""


# --------------------------------------------------------------------------- #
#  Цепочки этапа 2
# --------------------------------------------------------------------------- #

CHAIN_START = "===ЦЕПОЧКИ ЭТАПА 2==="
CHAIN_END = "===КОНЕЦ ЦЕПОЧЕК==="
#: Маркер одной цепочки внутри блока: «===ЦЕПОЧКА A: название===».
_CHAIN_LINE = re.compile(
    r"^\s*=*\s*ЦЕПОЧКА\s*([A-ZА-Я0-9]{1,2})\s*(?:[:.\-—]\s*(.*?))?\s*=*\s*$",
    re.I)
_STEP_LINE = re.compile(r"^\s*(?:шаг\s*)?(\d{1,2})\s*(?:[.)]|[-—–])\s*(.+)$", re.I)
_TITLE_LINE = re.compile(r"^\s*(?:НАЗВАНИЕ|ПОДХОД|СУТЬ)\s*:\s*(.+)$", re.I)
_WHY_LINE = re.compile(r"^\s*(?:ЗАЧЕМ|ПОЧЕМУ|ВЫГОДА)\s*:\s*(.+)$", re.I)


def chain_letters(count: int = MIN_CHAINS) -> list:
    """Буквы вариантов цепочки: A, B, C …"""
    base = "ABCDE"
    return list(base[:max(1, min(int(count or MIN_CHAINS), MAX_CHAINS))])


def split_chains(text: str) -> list:
    """Разбирает блок «===ЦЕПОЧКИ ЭТАПА 2===» на варианты.

    Возвращает список ``{letter, title, why, steps:[{n, kind, title, why}]}``.
    Если блока нет, но есть старый одиночный план (``===ПЛАН ЭТАПА 2===``),
    он превращается в единственный вариант «A» — старые ответы ИИ и старые
    дела продолжают работать.
    """
    raw = str(text or "")
    start = raw.find(CHAIN_START)
    chains: list = []
    if start >= 0:
        body = raw[start + len(CHAIN_START):]
        end = body.find(CHAIN_END)
        if end >= 0:
            body = body[:end]
        current = None
        for line in body.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            marker = _CHAIN_LINE.match(stripped)
            if marker:
                current = {
                    "letter": marker.group(1).upper()[:1],
                    "title": (marker.group(2) or "").strip(" .:—-"),
                    "why": "",
                    "steps": [],
                }
                chains.append(current)
                continue
            if current is None:
                continue
            title_line = _TITLE_LINE.match(stripped)
            if title_line:
                current["title"] = title_line.group(1).strip(" .")
                continue
            why_line = _WHY_LINE.match(stripped)
            if why_line:
                current["why"] = why_line.group(1).strip(" .")
                continue
            step = _STEP_LINE.match(stripped)
            if step:
                title, why = _split_step_body(step.group(2))
                current["steps"].append({
                    "n": int(step.group(1)),
                    "kind": ogp_chain.step_kind_from_text(title) or
                            ogp_chain.step_kind_from_text(why),
                    "title": title,
                    "why": why,
                })
    for chain in chains:
        chain["steps"].sort(key=lambda item: item["n"])
    chains = [chain for chain in chains if chain["steps"]]
    if chains:
        return _renumber(chains)
    single = ogp_chain.parse_plan(raw)
    if single:
        return [{
            "letter": "A",
            "title": "порядок от ИИ",
            "why": "",
            "steps": [{"n": step.get("n"), "kind": step.get("kind", ""),
                       "title": step.get("title", ""),
                       "why": step.get("why", "")} for step in single],
        }]
    return []


def _split_step_body(body: str) -> tuple:
    body = str(body or "").strip()
    for sep in (" — ", " – ", " - ", ": ", ";"):
        if sep in body:
            title, why = body.split(sep, 1)
            return title.strip(" .»«\"'"), why.strip(" .")
    return body.strip(" .»«\"'"), ""


def _renumber(chains: list) -> list:
    "Подэтапы нумеруются подряд 1..N внутри каждой цепочки."
    for chain in chains:
        chain["steps"] = [
            dict(step, n=index)
            for index, step in enumerate(chain["steps"], start=1)
        ][:MAX_STEPS]
    return chains[:MAX_CHAINS]


def chains_present(text: str) -> bool:
    return CHAIN_START in str(text or "") or ogp_chain.plan_present(text)


# --------------------------------------------------------------------------- #
#  Подписи узлов и кнопок
# --------------------------------------------------------------------------- #

def step_label(letter: str, number: int) -> str:
    """Метка подэтапа этапа 2: «2.1».

    Буква варианта цепочки в подпись кнопки не входит: пользователь просил
    ровно «Этап 2.1», «Этап 2.2», а вариант виден в «Цепочке этапов».
    """
    return f"2.{int(number)}"


def node_caption(case) -> str:
    """Подпись главной кнопки: «Этап 1» / «Этап 2.1» / «Этап 3».

    Кнопка одна и называется по активному узлу цепочки — так просил
    пользователь: «кнопка Этап 1, Этап 2.1, 2.2 …, Этап 3 — это по сути одна
    и та же, меняется её название и привязка к этапу».
    """
    if not str(getattr(case, "appeal_raw", "") or "").strip():
        return "Этап 1"
    state = str(getattr(case, "outcome", "") or "")
    if not state:
        return "Этап 1"
    if stage1_closes(state):
        return "Этап 1"
    if not (getattr(case, "work_plan", None) or []):
        return "Этап 2"
    number = int(case.next_work_step() or 0)
    if number:
        return "Этап " + step_label(str(getattr(case, "work_letter", "") or ""), number)
    # Все подэтапы цепочки пройдены — итог.
    return "Этап 3"


def save_caption(stage) -> str:
    """Подпись кнопки «Сформировать»: «Сформировать постановление для Этапа 2.1»."""
    kind = str(stage or "")
    if kind == "1":
        return "Сформировать постановление для Этапа 1"
    if kind == "3":
        return "Сформировать постановление для Этапа 3"
    return f"Сформировать постановление для Этапа {kind}"


def save_ready(stage) -> bool:
    """Можно ли печатать постановление по этому узлу (нужен ответ ИИ)."""
    return bool(str(stage or "").strip(" ."))


# --------------------------------------------------------------------------- #
#  Узел цепочки: индексы вида «1», «2.1», «3»
# --------------------------------------------------------------------------- #

def parse_node(kind) -> dict:
    """«2.3» → {etap: 2, letter: '', step: 3}; «3» → {etap: 3, step: 0}."""
    text = str(kind or "").strip().strip("эт. ").replace(",", ".")
    match = re.match(r"^([123])(?:\.(\d{1,2}))?$", text)
    if not match:
        return {"etap": 0, "letter": "", "step": 0}
    etap = int(match.group(1))
    step = int(match.group(2) or 0)
    if etap != ETAP_2:
        step = 0
    return {"etap": etap, "letter": "", "step": step}


def node_folder(base: str, kind) -> str:
    """Имя подпапки узла: «эт.1», «эт.2.3», «эт.3» (v62).

    ``base`` — папка обращения; к ней добавляется подпапка узла и, если у
    прокурора несколько вариантов цепочки, буква варианта («эт.A-2.1»).
    """
    parsed = parse_node(kind)
    if not parsed["etap"]:
        return str(base)
    if parsed["etap"] == ETAP_2 and parsed["step"]:
        name = f"эт.{parsed['step'] and '2.' + str(parsed['step'])}"
    elif parsed["etap"] == ETAP_1:
        name = "эт.1"
    else:
        name = "эт.3"
    suffix = f" ({base})" if base else ""
    return name + suffix


# --------------------------------------------------------------------------- #
#  Состояние узлов для интерфейса
# --------------------------------------------------------------------------- #

def node_state(case, kind) -> dict:
    """Что известно про узел: ответ ИИ, поле прокурора, PNG, устаревание.

    Возвращает ``{etap, step, answer, input, stale, png, done, caption,
    save_caption, can_run, can_save, hint}``. Интерфейс только рисует это.
    """
    parsed = parse_node(kind)
    etap, step = parsed["etap"], parsed["step"]
    node = case.work_node(kind) if etap else {}
    answer = str(node.get("answer", "") or "")
    user_input = str(node.get("input", "") or "")
    stale = bool(node.get("stale"))
    stage1 = stage1_from_outcome(getattr(case, "outcome", ""))
    state = {
        "kind": _norm_kind(etap, step),
        "etap": etap,
        "step": step,
        "answer": answer,
        "input": user_input,
        "stale": stale,
        "png": str(node.get("png", "") or ""),
        "done": bool(node.get("done")),
        "caption": node_caption(case) if etap else "",
        "save_caption": save_caption(_norm_kind(etap, step)) if etap else "",
        "can_run": False,
        "can_save": False,
        "hint": "",
    }
    if not etap:
        state["hint"] = "Неизвестный этап."
        return state
    if etap == ETAP_1:
        state["can_run"] = bool(str(case.appeal_raw or "").strip())
        state["can_save"] = bool(answer and stage1)
        state["hint"] = ("Вставьте текст обращения и нажмите «Этап 1»: ИИ решит "
                         "судьбу обращения и предложит цепочки этапа 2.")
        return state
    if not stage1:
        state["hint"] = "Сначала выполните этап 1."
        return state
    if stage1_closes(stage1):
        state["hint"] = (f"Этап 1 завершён: {stage1_label(stage1)} — этапы 2 и "
                         "3 не наступают.")
        return state
    if etap == ETAP_2:
        if not (case.work_plan or []):
            state["hint"] = "План этапа 2 ещё не получен: выполните этап 1."
            return state
        step_info = _step_by_number(case, step)
        if not step_info:
            state["hint"] = f"Подэтап 2.{step} не найден в выбранной цепочке."
            return state
        blocked = _blocked_reason(case, step)
        state["can_run"] = not blocked
        state["can_save"] = bool(answer) and not step_info.get("needs_rerun", False)
        state["hint"] = blocked or (
            f"Подэтап 2.{step}: введите, что стало известно, и нажмите "
            f"«Этап 2.{step}».")
        return state
    # Этап 3
    state["can_run"] = _stage3_allowed(case)
    state["can_save"] = bool(answer)
    state["hint"] = ("ИИ сам выберет вид завершения по смыслу всех этапов — "
                     "нажмите «Этап 3», чтобы получить решение.")
    return state


def _norm_kind(etap: int, step: int) -> str:
    if not etap:
        return ""
    if etap == ETAP_2 and step:
        return f"2.{step}"
    return str(etap)


def _step_by_number(case, number: int) -> dict:
    for step in case.work_plan or []:
        if int(step.get("n") or 0) == int(number or 0):
            return step
    return {}


def _previous_numbers(case, number: int) -> list:
    return [int(step.get("n") or 0) for step in case.work_plan or []
            if int(step.get("n") or 0) < int(number or 0)]


def _blocked_reason(case, number: int) -> str:
    """Почему нельзя отправить подэтап в ИИ (пусто — можно)."""
    for prev in _previous_numbers(case, number):
        node = case.work_node(f"2.{prev}")
        if not str(node.get("answer", "") or "").strip():
            return (f"Сначала пройдите подэтап 2.{prev}: подэтапы идут строго "
                    "по порядку.")
        if node.get("stale"):
            return (f"Поле 1 подэтапа 2.{prev} изменено после ответа ИИ — "
                    f"прогоните подэтап 2.{prev} снова.")
        if not node.get("done"):
            return f"Подэтап 2.{prev} ещё не отмечен выполненным."
    return ""


def _stage3_allowed(case) -> bool:
    """Этап 3 доступен, когда пройдены все подэтапы активной цепочки."""
    if not (case.work_plan or []):
        return False
    if not case.work_done():
        return False
    return not any(case.work_node(f"2.{int(step.get('n') or 0)}").get("stale")
                   for step in case.work_plan)


def chain_progress(case) -> list:
    """Прогресс всех вариантов цепочки: [{letter, title, done, total, active}]."""
    rows = []
    active = str(getattr(case, "work_letter", "") or "")
    for chain in case.work_chains or []:
        steps = chain.get("steps") or []
        done = 0
        for index, _step in enumerate(steps, start=1):
            node = (chain.get("nodes") or {}).get(f"2.{index}") or {}
            if str(node.get("answer", "") or "").strip():
                done += 1
        rows.append({
            "letter": str(chain.get("letter", "")),
            "title": str(chain.get("title", "")),
            "why": str(chain.get("why", "")),
            "done": done,
            "total": len(steps),
            "active": str(chain.get("letter", "")) == active,
        })
    return rows


def overall_badge(case) -> str:
    """Короткая строка состояния для заголовка окна."""
    state = stage1_from_outcome(getattr(case, "outcome", ""))
    if not state:
        return "этап 1: не решён"
    if stage1_closes(state):
        return f"этап 1: {stage1_label(state)} · дело закрыто"
    if not (case.work_plan or []):
        return "этап 1: принятие · план этапа 2 не получен"
    number = int(case.next_work_step() or 0)
    total = len(case.work_plan or [])
    if number:
        return (f"этап 2 — шаг {number} из {total} "
                f"(цепочка {getattr(case, 'work_letter', 'A')})")
    return "этап 3: итог по обращению"


# --------------------------------------------------------------------------- #
#  Итог шага по ответу ИИ
# --------------------------------------------------------------------------- #

NEGATIVE_MARKS = ("отказать", "отказано", "не подлежит", "не подтвержд",
                  "недостаточно", "прекратить", "прекращено", "не установлен",
                  "не выявлено")
POSITIVE_MARKS = ("признать виновн", "привлечь к ответственности",
                  "назначить штраф", "возбудить уголовное дело",
                  "удовлетворить", "истребовать", "вызвать на допрос",
                  "приостановить", "внести изменения", "установлено",
                  "подтверждено", "предать суду", "передать в суд")


def verdict_of(text: str) -> str:
    """Итог узла по резолютивной части ответа ИИ: positive / negative / пусто."""
    low = str(text or "").lower()
    if not low.strip():
        return ""
    marker = low.rfind("постановил")
    body = low[marker:] if marker >= 0 else low
    negative = any(mark in body for mark in NEGATIVE_MARKS)
    positive = any(mark in body for mark in POSITIVE_MARKS)
    if negative and not positive:
        return "negative"
    if positive:
        return "positive"
    if negative:
        return "negative"
    return ""


# --------------------------------------------------------------------------- #
#  Контекст для ИИ: память всей цепочки
# --------------------------------------------------------------------------- #

INPUT_PREVIEW = 600


def _short(text: str, limit: int = INPUT_PREVIEW) -> str:
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + " …"


def context_block(case, current: str = "") -> str:
    """Связный контекст дела для заявки в ИИ (v62).

    Пользователь: «работая на этапе 2.3, в ИИ внутри документа (.txt)
    отсылается информация из поля „Обращение“, ответ по „Этап 1“, ответ по
    „Этап 2.1“, „Этап 2.2"» — то есть ИИ должен помнить всю цепочку.
    Поле 1 текущего узла идёт целиком, пройденных — сокращённо, поле 2
    (ответ ИИ) — целиком: это вывод, на который он опирается.
    """
    lines = ["СВЕДЕНИЯ ПО ДЕЛУ (память предыдущих этапов):", ""]
    raw = str(case.appeal_raw or "").strip()
    if raw:
        lines.append("ОБРАЩЕНИЕ (текст целиком — во вложении «Обращение.txt»):")
        lines.append(_short(raw, 1200))
        lines.append("")
    state = stage1_from_outcome(getattr(case, "outcome", ""))
    node1 = case.work_node("1")
    if state:
        lines.append(f"ЭТАП 1 — решение: {stage1_label(state)}")
        if case.intake_basis:
            lines.append(f"Основание: {case.intake_basis}")
        if node1.get("input"):
            lines.append(f"Что ввёл прокурор: {_short(node1['input'])}")
        if node1.get("answer"):
            lines.append("Ответ ИИ по этапу 1:")
            lines.append(str(node1["answer"]).strip())
        lines.append("")
    if state != STATE_ACCEPT:
        return "\n".join(lines).rstrip() + "\n"
    chains = chain_progress(case)
    if chains:
        lines.append("ВАРИАНТЫ ЦЕПОЧКИ ЭТАПА 2:")
        for row in chains:
            mark = "◀ активная" if row["active"] else ""
            lines.append(f"  {row['letter']}) {row['title']} — пройдено "
                         f"{row['done']} из {row['total']} {mark}".rstrip())
        lines.append("")
    for step in case.work_plan or []:
        number = int(step.get("n") or 0)
        node = case.work_node(f"2.{number}")
        answer = str(node.get("answer", "") or "").strip()
        user_input = str(node.get("input", "") or "").strip()
        if not answer and not user_input:
            continue
        lines.append(f"ПОДЭТАП 2.{number} — {step.get('title', '')}"
                     f"{' (выполнен)' if node.get('done') else ''}")
        # Поле 1 текущего узла отдельно и целиком печатает заявка (node_request),
        # поэтому в контексте его не дублируем.
        if user_input and f"2.{number}" != str(current):
            lines.append("  Что стало известно: " + _short(user_input))
        if node.get("stale"):
            lines.append("  ВНИМАНИЕ: ввод прокурора изменён после ответа ИИ.")
        if answer:
            lines.append("  Ответ ИИ:")
            for line in answer.splitlines():
                lines.append("    " + line)
        lines.append("")
    node3 = case.work_node("3")
    if node3.get("input") or node3.get("answer"):
        lines.append("ЭТАП 3 — итог")
        if node3.get("input"):
            lines.append("  Что ввёл прокурор: " + str(node3["input"]).strip())
        if node3.get("answer"):
            lines.append("  Ответ ИИ:")
            for line in str(node3["answer"]).splitlines():
                lines.append("    " + line)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------------------- #
#  Человеческий вид поля 2 (ответ ИИ) — как в документе
# --------------------------------------------------------------------------- #

#: Поля документа в порядке постановления.
DOC_FIELDS: tuple = (
    ("doc_number", "Номер постановления"),
    ("doc_date", "Дата"),
    ("doc_type", "Вид документа"),
    ("author_name", "Постановление вынес"),
    ("author_rank", "Должность"),
    ("author_id", "Удостоверение прокурора"),
    ("city", "Город"),
    ("applicant", "Заявитель"),
    ("applicant_id", "ID заявителя"),
    ("appeal_date", "Дата обращения"),
    ("subject", "Предмет обращения"),
    ("offender", "В отношении"),
    ("offender_id", "Удостоверение"),
    ("offender_department", "Отдел сотрудника"),
    ("event_datetime", "Дата и время события"),
    ("event_place", "Место события"),
    ("evidence", "Доказательства"),
    ("violation_act", "Нарушенный акт"),
    ("violation_clause", "Пункт нарушения"),
    ("legal_basis", "Нормативное основание"),
    ("penalty", "Взыскание"),
)


def pretty_answer(doc, raw: str = "") -> str:
    """Ответ ИИ «по-человечески»: шапка документа, УСТАНОВИЛ, ПОСТАНОВИЛ.

    Пользователь: «сделай, чтобы пользователь не мог менять информацию в поле
    2, а только смотреть, располагай информацию в поле 2 в понятном виде без
    `"author_name": "Marcus Nomad"`, а как она должна стоять в документе».
    """
    lines: list = []
    values = {}
    if doc is not None:
        try:
            values = doc.to_dict()
        except Exception:
            values = {}
    for key, label in DOC_FIELDS:
        value = str(values.get(key, "") or "").strip()
        if value:
            lines.append(f"{label}: {value}")
    established = str(values.get("established", "") or "").strip()
    resolved = str(values.get("resolved", "") or "").strip()
    if established:
        lines.append("")
        lines.append("УСТАНОВИЛ:")
        lines.append(established)
    if resolved:
        lines.append("")
        lines.append("ПОСТАНОВИЛ:")
        lines.append(resolved)
    if lines:
        return "\n".join(lines)
    # Документ не разобран — показываем сам ответ ИИ без технических пометок.
    text = str(raw or "")
    for junk in ("===ПОСТАНОВЛЕНИЕ===", "===КОНЕЦ ПОСТАНОВЛЕНИЯ===",
                 "===КОНЕЦ ОТВЕТА==="):
        text = text.replace(junk, "")
    return text.strip()
