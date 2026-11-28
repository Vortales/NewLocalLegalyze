"""gov_case.py — дело по обращению №X: состояния, этапы, артефакты (v42).

Замечания пользователя (v42), которые держит этот модуль:

* первое, что пишет прокурор по обращению, — принято оно или отказано. Для
  этого «Обращение» вставляется целиком и нажимается **«Проверка ИИ»**: ИИ
  читает текст и отвечает служебным блоком, из которого клиент берёт
  состояние;
* по результату проверки обращение получает состояние **«В процессе»**
  (принято) либо **«Отказано»**. Отказ не окончателен: прокурор может
  провести проверку заново или принять обращение вопреки ответу ИИ;
* работа идёт **этапами**. Два этапа («принятие» и «санкция») —
  минимальная цепочка, но законодательство и Регламент допускают
  дополнительные шаги: истребование материалов, повторная проверка,
  переадресация, рассмотрение жалобы на собственное постановление. Каждый
  этап — отдельный документ;
* **PNG разных этапов не должны затирать друг друга**: у файла каждого этапа
  своё имя («AG-1 — эт.1 принятие», «AG-1 — эт.2 санкция») и своя подпапка.

Модуль намеренно не зависит от Qt: всё проверяется обычными тестами.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import doc_layout as dl
import ogp_chain
import ogp_flow
import ogp_stages
import postanovlenie_data as base
import postanovlenie_prokuror_data as gdata

# --------------------------------------------------------------------------- #
#  Состояния обращения
# --------------------------------------------------------------------------- #

STATUS_NEW = "new"
STATUS_IN_PROGRESS = "in_progress"
STATUS_REFUSED = "refused"
STATUS_CLOSED = "closed"

#: «Обращение №» в программе: новое → в процессе / отказано → завершено.
CASE_STATUS_LABELS: dict = {
    STATUS_NEW: "Новое",
    STATUS_IN_PROGRESS: "В процессе",
    STATUS_REFUSED: "Отказано",
    STATUS_CLOSED: "Завершено",
}

#: Цвет подписи состояния в форме.
CASE_STATUS_COLORS: dict = {
    STATUS_NEW: "#8f96bf",
    STATUS_IN_PROGRESS: "#34d399",
    STATUS_REFUSED: "#f59e0b",
    STATUS_CLOSED: "#7c83ff",
}


# --------------------------------------------------------------------------- #
#  Этапы
# --------------------------------------------------------------------------- #

#: Этап 1 — судьба самого обращения (принять / отказать / переадресовать).
STAGE_INTAKE = "intake"
#: Этап 2 — итоговое решение по существу (санкция).
STAGE_MEASURE = "measure"
#: Дополнительные шаги цепочки (зависят от материалов дела).
STAGE_ADDITIONAL = "additional"
STAGE_RECHECK = "recheck"
STAGE_REDIRECT = "redirect"
STAGE_APPEAL = "appeal"
#: Завершение производства по обращению.
STAGE_CLOSE = "close"

#: Минимальная цепочка: принять и постановить.
DEFAULT_STAGE_PLAN: tuple = (STAGE_INTAKE, STAGE_MEASURE)

#: Дополнительные этапы, которые прокурор может добавить в цепочку.
EXTRA_STAGES: tuple = (STAGE_ADDITIONAL, STAGE_RECHECK,
                       STAGE_REDIRECT, STAGE_APPEAL, STAGE_CLOSE)

#: Описание этапа: подпись, короткое имя для файлов, нужны ли обстоятельства,
#: что именно делает ИИ на этом этапе.
STAGE_META: dict = {
    STAGE_INTAKE: {
        "title": "Этап 1. Принять или отказать в принятии обращения",
        "short": "принятие",
        "needs_facts": False,
        "needs_facts_label": "",
        "guide": (
            "Проверь ТОЛЬКО само обращение и его форму по Регламенту подачи: "
            "установима ли личность заявителя, есть ли предмет обращения и "
            "описание события, дата и время, нарушитель, доказательства, "
            "подпись с расшифровкой. Вынеси одно решение: принять к "
            "собственному производству либо отказать в принятии с НОМЕРОМ "
            "конкретного основания; если обращение не нашего ведомства — "
            "переадресуй, если отозвано или утратило предмет — прекрати. "
            "Санкцию не назначай."
        ),
    },
    STAGE_MEASURE: {
        "title": "Этап 2. Итоговое решение — санкция по существу",
        "short": "санкция",
        "needs_facts": True,
        "needs_facts_label": "Обстоятельства прокурора",
        "guide": (
            "Оцени обращение вместе с «Обстоятельствами» прокурора: что видно "
            "на видеофиксации, доводы за и против, предложенное наказание и "
            "решение прокурора. Предложение прокурора рассмотри, но прими "
            "собственное решение по существу и обоснуй его нормой, "
            "устанавливающей ответственность."
        ),
    },
    STAGE_ADDITIONAL: {
        "title": "Доп. проверка: истребование материалов и объяснений",
        "short": "доп-проверка",
        "needs_facts": True,
        "needs_facts_label": "Что уже установлено и что нужно истребовать",
        "guide": (
            "Составь постановление о проведении дополнительной проверки: "
            "перечисли, какие материалы и объяснения истребуются, у кого и в "
            "какой срок; итоговую санкцию пока не назначай."
        ),
    },
    STAGE_RECHECK: {
        "title": "Повторная проверка обстоятельств",
        "short": "перепроверка",
        "needs_facts": True,
        "needs_facts_label": "Что проверить заново и почему",
        "guide": (
            "Проверь обстоятельства заново по новым материалам: сопоставь "
            "видеофиксацию, доводы за и против и ранее сделанные выводы. "
            "Прямо укажи, что подтвердилось, что опровергнуто и что осталось "
            "неустановленным."
        ),
    },
    STAGE_REDIRECT: {
        "title": "Переадресация / передача материалов",
        "short": "переадресация",
        "needs_facts": False,
        "needs_facts_label": "",
        "guide": (
            "Вынеси решение о переадресации обращения либо передаче "
            "материалов: укажи конкретный орган или подразделение-адресата и "
            "основание."
        ),
    },
    STAGE_APPEAL: {
        "title": "Рассмотрение жалобы на постановление",
        "short": "жалоба",
        "needs_facts": True,
        "needs_facts_label": "Доводы жалобы и что по ним установлено",
        "guide": (
            "Рассмотри жалобу на ранее вынесенное постановление: проверь "
            "доводы, оцени материалы и вынеси решение — оставить "
            "постановление без изменения, изменить его или отменить."
        ),
    },
    STAGE_CLOSE: {
        "title": "Завершение производства по обращению",
        "short": "завершение",
        "needs_facts": False,
        "needs_facts_label": "",
        "guide": (
            "Вынеси постановление о завершении производства по обращению: "
            "перечисли, что сделано по делу, и укажи итог."
        ),
    },
}


#: Узел цепочки («1», «2.1», «3», «эт.2.1») — НЕ вид этапа. v64: запись
#: отложенной отправки клала узел в `stage_kind`, и дальше
#: `stage_number("2.1")` давал 3, а `gov_stage_for_kind("2.1")` — «primary»:
#: документ подэтапа 2.1 уезжал в папку «этап-3 — санкция» с этапом 1 в полях.
_NODE_KIND = re.compile(r"^(?:эт\.)?(?:1|2\.\d{1,2}|3)$")


def _normalize_stage_kind(kind) -> str:
    """Узел цепочки приводится к виду этапа; виды этапов не меняются."""
    key = str(kind or "").strip()
    if _NODE_KIND.match(key):
        return stage_kind_for_node(key)
    return key


def stage_meta(kind: str) -> dict:
    """Описание этапа (для незнакомого вида — описание санкции)."""
    return STAGE_META.get(_normalize_stage_kind(kind), STAGE_META[STAGE_MEASURE])


def stage_short(kind: str) -> str:
    """Короткое имя этапа для имён файлов."""
    return stage_meta(kind)["short"]


def stage_title(kind: str) -> str:
    return stage_meta(kind)["title"]


# --------------------------------------------------------------------------- #
#  Имена файлов этапов: PNG разных этапов не заменяют друг друга
# --------------------------------------------------------------------------- #

def doc_type_slug(doc_type: str, limit: int = 48) -> str:
    """Вид решения для имени файла: «отказе-в-принятии-обращения» (v49).

    Замечание пользователя: «этап 1 и этап 2 обязаны иметь ОДИН номер (напр.
    AG-1), но разные папки и индивидуальные метки по этапу и „Виду решения“».
    Поэтому в имя файла попадает и вид решения из ответа ИИ, но только в
    безопасном виде: без пробелов, кавычек, «№» и других спецсимволов.
    """
    text = str(doc_type or "").strip()
    if not text:
        return ""
    # «Об отказе в принятии обращения к рассмотрению» -> «отказе-в-принятии…»
    text = re.sub(r"^\s*(?:О|Об|От)\s+", "", text, flags=re.IGNORECASE)
    text = text.replace("«", "").replace("»", "").replace('"', "")
    text = text.lower()
    text = re.sub(r"[^\w\- ]", "", text)
    text = re.sub(r"\s+", "-", text).strip("-")
    text = re.sub(r"-{2,}", "-", text)
    if len(text) > limit:
        text = text[:limit].rstrip("-")
    return text


def stage_stem(number: str, stage_index: int, kind: str,
               doc_type: str = "", step_number: int = 0) -> str:
    """Имя файла документа этапа: «AG-1_Постановление_Этап_1_принятие…» (v49).

    История замечаний:

    * v42 — «раздели итоговые файлы .png для различных этапов, чтобы файл,
      полученный на 1 этапе, не заменялся файлами следующих этапов»;
    * v49 — «этап 1 и этап 2 обязаны иметь ОДИН номер (напр. `AG-1`), но разные
      папки и индивидуальные метки по этапу и „Виду решения“».

    Поэтому номер в имени один (`AG-1`), а различия дают метки этапа и вида
    решения: `AG-1_Постановление_Этап_1_принятие_Вид_отказе-в-принятии-обращения`.
    """
    safe = re.sub(r"[^\w\-. №]", "_", str(number or "").strip()) or "б-н"
    safe = safe.replace(" ", "-")
    # v60: у этапа 2 шагов несколько, и каждый — своё постановление.
    # Замечание пользователя: «2 этап это не 1 документ и не 1 шаг… может быть
    # как 1 шаговым так и много шаговым». Поэтому номер шага входит в имя:
    # «AG-1_Постановление_Этап_2.3_допрос_Вид_…».
    index = str(int(stage_index))
    label = stage_short(kind)
    if step_number:
        index = f"{index}.{int(step_number)}"
        # Метка шага по существу — по названию постановления («допрос»,
        # «истребование», «штраф»), а не общее «санкция»: по имени файла
        # должно быть видно, что это за документ.
        label = ogp_chain.step_short(ogp_chain.step_kind_from_text(doc_type)) \
            or "шаг"
    stem = f"{safe}_Постановление_Этап_{index}_{label}"
    slug = doc_type_slug(doc_type)
    if slug:
        stem = f"{stem}_Вид_{slug}"
    return stem


def node_step_label(case: "GovCase", kind) -> str:
    """Короткая подпись подэтапа для имени папки: «истребование», «допрос»."""
    step = dl.node_step(kind)
    if not step:
        text = str(kind or "").strip()
        if text == "3":
            return "итог"
        return "принятие"
    for item in case.work_plan or []:
        if int(item.get("n") or 0) != step:
            continue
        title = str(item.get("title") or item.get("kind") or "")
        return ogp_chain.step_short(ogp_chain.step_kind_from_text(title)) or ""
    return ""


def node_folder(root, case: "GovCase", kind, doc_type: str = "",
                create: bool = True, doc_number: str = "") -> Path:
    """Папка узла: v66 — новая схема, иначе прежняя «этап-N — label».

    Решение пользователя `v66_only`: новые правила действуют только для дел,
    созданных в v66. Старые дела остаются в своих папках (`AG-1`, `001`) — их
    нельзя переименовывать и переносить.
    """
    if case is not None and case.new_numbering():
        return case.stage_folder_v66(
            root, kind,
            step_label=node_step_label(case, kind) or
            doc_type_slug(doc_type), create=create)
    stage_kind = stage_kind_for_node(str(kind or ""))
    step = dl.node_step(kind)
    # Старое дело: папка постановления называется по номеру документа
    # (`AG-1`), как в v65, — ничего не переименовываем и не переносим.
    legacy = str(doc_number or "").strip() or getattr(case, "number", "") or ""
    return stage_folder(root, legacy,
                        case.stage_number(stage_kind) if case is not None else 1,
                        stage_kind,
                        case_number=getattr(case, "number", None),
                        step_number=step, doc_type=doc_type, create=create)


def node_folder_guess(root, case: "GovCase", kind, doc_number: str = ""):
    """Папка узла ДО разбора ответа ИИ (v67, находка 2).

    Сырой ответ сохраняется сразу — ещё до того, как из документа стало
    известно поле `doc_type` (по нему строится подпись папки: «Этап 2.1 —
    истребование»). Чтобы файл не уехал в другую папку, сначала ищем уже
    созданную папку этапа по её началу («Этап 2.1», «Этап 1», «Этап 3»), и
    только если её нет — создаём по обычным правилам `node_folder`.

    Для дел прежней нумерации (до v66) возвращает тот же путь, что и
    `node_folder`: их папки нельзя переименовывать и переносить.
    """
    folder = node_folder(root, case, kind, "", create=False,
                         doc_number=doc_number)
    if case is None or not case.new_numbering():
        return folder
    prefix = dl.stage_dir_name(kind)
    try:
        parent = folder.parent
        if parent.is_dir():
            for item in sorted(parent.iterdir()):
                name = item.name
                if item.is_dir() and name != "Архив" and name.startswith(prefix):
                    return item
    except OSError:
        pass
    return folder


def node_stem(case: "GovCase", kind, doc_number: str = "",
              doc_type: str = "") -> str:
    """Имя постановления без страниц: v66 — «0001-2 — Этап 2.1 — истребование»."""
    if case is not None and case.new_numbering():
        number = dl.normalize_base(case.doc_base)
        index = int((case.doc_index_table() or {}).get(str(kind)) or 0) or \
            dl.canonical_index(kind, len(case.current_plan() or []))
        return dl.file_stem(dl.document_number(number, index), kind,
                            node_step_label(case, kind) or doc_type_slug(doc_type))
    stage_kind = stage_kind_for_node(str(kind or ""))
    return stage_stem(doc_number or getattr(case, "number", ""),
                      case.stage_number(stage_kind) if case is not None else 1,
                      stage_kind, doc_type, step_number=dl.node_step(kind))


def node_doc_number(case: "GovCase", kind, fallback: str = "") -> str:
    """Номер постановления узла: v66 — «0001-2», иначе прежний номер формы."""
    if case is not None and case.new_numbering():
        number = case.doc_number_for(kind)
        if number:
            return number
    return str(fallback or "")


def stage_index_for(plan, kind: str) -> int:
    """Номер этапа для имени файла: место этапа в цепочке дела (v43).

    Замечание: «если обращение принято и я нажал Проверку ИИ, а затем
    Сформировать, этап по умолчанию сразу менялся с 1 на 2». Номер этапа не
    должен зависеть от того, сколько этапов уже выполнено: у каждого вида
    этапа своё постоянное место в цепочке. Поэтому «принятие» всегда этап 1,
    «санкция» — этап 2, а дополнительный этап получает следующий свободный
    номер.
    """
    kind = _normalize_stage_kind(kind)
    order = []
    for item in plan or ():
        name = item.get("kind") if isinstance(item, dict) else item
        if name and name not in order:
            order.append(name)
    if kind in order:
        return order.index(kind) + 1
    return len(order) + 1


def stage_folder(root, number: str, stage_index: int, kind: str,
                 case_number=None, step_number: int = 0,
                 doc_type: str = "", create: bool = True) -> Path:
    """Папка этапа постановления (v51/v53).

    v53: «AG-1» лежит внутри папки обращения — `postanovleniya/Обращение
    №1/AG-1/этап-1 — принятие`. Раньше папка постановления была прямо в корне,
    и по ней не было видно, к какому обращению она относится (замечание
    пользователя: «создавать папку „Обращение №_номер_обращения“ и в неё уже
    класть папки AG-1, AG-2 и т.д.»).
    """
    folder = base.doc_folder(Path(root), number, case_number)
    index = str(int(stage_index))
    label = stage_short(kind)
    if step_number:
        index = f"{index}.{int(step_number)}"
        label = ogp_chain.step_short(ogp_chain.step_kind_from_text(doc_type)) \
            or "шаг"
    target = folder / f"этап-{index} — {label}"
    if create:
        target.mkdir(parents=True, exist_ok=True)
    return target


# --------------------------------------------------------------------------- #
#  Проверка обращения: служебный блок «принято / отказано»
# --------------------------------------------------------------------------- #

#: Маркеры и состояния — в `postanovlenie_prokuror_data` (единый источник).
INTAKE_START = gdata.INTAKE_START
INTAKE_END = gdata.INTAKE_END
INTAKE_ACCEPTED = gdata.INTAKE_ACCEPTED
INTAKE_REFUSED = gdata.INTAKE_REFUSED
INTAKE_REDIRECTED = gdata.INTAKE_REDIRECTED
INTAKE_TERMINATED = gdata.INTAKE_TERMINATED
#: v60: «оставить без движения на 24 часа» — пятый исход этапа 1.
INTAKE_DEFECTS = gdata.INTAKE_DEFECTS

#: Что означает состояние обращения для «Обращения №» в программе.
INTAKE_STATUS_MAP: dict = {
    INTAKE_ACCEPTED: STATUS_IN_PROGRESS,
    INTAKE_REFUSED: STATUS_REFUSED,
    INTAKE_REDIRECTED: STATUS_CLOSED,
    INTAKE_TERMINATED: STATUS_CLOSED,
    # v60: недостатки формы не закрывают обращение — дело остаётся новым,
    # пока не истекут 24 часа или заявитель не исправит обращение.
    INTAKE_DEFECTS: STATUS_NEW,
}


def parse_intake_answer(text: str) -> dict:
    """Результат проверки ИИ + состояние дела (обёртка над данными).

    v64: в `raw` (а значит в `case.intake_answer` и в поле 2 «Ответ ИИ»)
    кладётся ЧИСТЫЙ ответ ИИ, а не весь текст страницы чата с эхом заявки,
    чипами вложений и элементами интерфейса — замечание пользователя по
    выгрузке `этап-1 — принятие.7z`: «в поле „Ответ ИИ (только чтение)“ каша,
    всё вперемешку». Разбор служебных строк при этом идёт по исходному тексту:
    он терпимее к мусору страницы.
    """
    result = gdata.parse_intake_answer(text)
    result["status"] = INTAKE_STATUS_MAP.get(result.get("state", ""), "")
    result["ok"] = bool(result.get("state") and result["status"])
    # v65: страница чата хранит все ответы диалога — берём последний блок
    # проверки обращения, а не первый ответ страницы.
    result["raw"] = (gdata.extract_ai_answer(text, want="intake")
                     or str(text or ""))
    return result


def intake_block_present(text: str) -> bool:
    return gdata.has_intake_block(text)


# --------------------------------------------------------------------------- #
#  Дело по обращению
# --------------------------------------------------------------------------- #

@dataclass
class GovCase:
    """Дело «Обращение №X»: обращения, состояние, этапы и работа прокурора."""

    #: Сквозной номер дела в программе («1», «2», …) — по нему открывается
    #: «Открыть обращение №X».
    number: str = ""
    #: Номер обращения с форума (если он там указан).
    appeal_number: str = ""
    #: Текст обращения целиком — ровно то, что скопировано с форума.
    appeal_raw: str = ""
    status: str = STATUS_NEW
    #: Основание, которое назвал ИИ («пункт 13 части 1 статьи 16 …»).
    intake_basis: str = ""
    #: Сырой ответ ИИ на проверку — чтобы прокурор мог перечитать решение.
    intake_answer: str = ""
    #: v64: «Вид решения» из ответа этапа 1 — по нему «Сформировать» просит ИИ
    #: вынести ДОКУМЕНТ этапа 1 («О принятии обращения к собственному
    #: производству» и т. п.).
    intake_doc_type: str = ""
    #: v64: блок «РЕКОМЕНДАЦИИ» из ответа этапа 1 — подсказка прокурору
    #: («Цепочка этапов»). В постановление и в контекст ИИ не попадает.
    recommendations: list = field(default_factory=list)
    #: Сколько раз обращение отправлялось на проверку ИИ (v43.4). Поле было
    #: «временным атрибутом»: `to_dict()` его не сохранял, в `case.json` его не
    #: было, и после перезапуска счётчик обнулялся.
    intake_checks: int = 0
    #: a) что прокурор увидел на видеофиксации.
    facts_video: str = ""
    #: b) доводы за.
    facts_for: str = ""
    #: b) доводы против.
    facts_against: str = ""
    #: c) наказание, которое прокурор предлагает (ИИ рассматривает, но не обязан принять).
    proposal: str = ""
    #: d) решение прокурора от своего лица.
    own_decision: str = ""
    #: v56: что из блоков «Обстоятельства» записал САМ ИИ, ответив на
    #: «Сформировать». Замечание пользователя: «информация в данных
    #: Обстоятельства это то, что дается ИИ для анализа… все что я пишу в
    #: Обстоятельства сам и исправляю, попасть не должно». Поэтому:
    #:   * в PNG уходит только текст ИИ (не эти блоки);
    #:   * если пользователь поправил блок, в следующий запрос уходит ТОЛЬКО
    #:     его правка — текст, который туда записал ИИ, повторно не
    #:     отправляется (иначе ИИ пересказывал сам себя).
    ai_facts: dict = field(default_factory=dict)
    #: Этап, с которым прокурор работал последним (v43). По нему форма
    #: открывает дело: этап не «прыгает» на следующий сам по себе, пока
    #: прокурор сам его не сменит.
    stage_kind: str = ""
    #: Выполненные и запланированные этапы: [{kind, index, doc_number, png, done, when}].
    stages: list = field(default_factory=list)
    #: Реквизиты обращения, которые ИИ прислал ключевыми словами в ответ на
    #: «Проверку ИИ» (v49): заявитель, его ID, нарушитель, дата события, предмет
    #: и нормы. Источник — только ответ ИИ; локальные фильтры по тексту
    #: обращения больше не подставляют реквизиты (пользователь может
    #: переставить их местами, и порядок строк ломает любые регулярки).
    intake_fields: dict = field(default_factory=dict)
    #: v60: исход этапа 1 — один из пяти (`ogp_chain.OUTCOMES`): принято,
    #: отказ, недостатки, переадресация, прекращение. Отдельное поле нужно
    #: потому, что старое `status` знает только «в процессе / отказано», а
    #: блок-схема ОГП различает ещё оставление без движения на 24 часа,
    #: переадресацию и прекращение — и у каждого свой документ.
    outcome: str = ""
    #: v60: план этапа 2, который ИИ прислал вместе с «принято»:
    #: [{n, kind, title, why, done, doc_number, png, when, note}].
    work_plan: list = field(default_factory=list)
    #: v60: номер текущего шага этапа 2 (0 — ни один не открыт).
    work_step: int = 0
    #: v62: варианты цепочки этапа 2 из ответа ИИ (минимум три):
    #: [{letter, title, why, steps: [{n, kind, title, why}],
    #:   nodes: {"2.1": {input, answer, stale, done, png, when, verdict}}}].
    #: Пользователь: «пусть ИИ даёт минимум 3 цепочки… прогресс другой цепочки
    #: не сотрётся, если он захочет выбрать другую».
    work_chains: list = field(default_factory=list)
    #: v62: буква активного варианта цепочки («A», «B», «C»).
    work_letter: str = "A"
    #: v63: когда и почему обращение закрыто кнопкой «Закрыть обращение».
    closed_at: str = ""
    close_reason: str = ""
    #: v62: узлы вне цепочки этапа 2 — этап 1 и этап 3:
    #: {"1": {input, answer, stale, done, png, when, verdict}, "3": {…}}.
    work_nodes: dict = field(default_factory=dict)
    #: v62: ручной порядок цепочки, который ИИ должен согласовать. Пусто —
    #: порядок от ИИ. Список названий шагов в нужном прокурору порядке.
    work_custom_order: list = field(default_factory=list)
    #: v66: признак дела, созданного в v66, — для него действует новая
    #: нумерация постановлений. Решение пользователя `v66_only`: старые дела
    #: продолжают жить в прежних папках (`AG-1`, `001`) и НЕ
    #: переименовываются.
    numbering: str = ""
    #: v66: общий номер набора постановлений обращения — «0001», «0002», …
    #: предлагается автоматически и правится прокурором вручную; занять номер
    #: другого обращения нельзя (решение `forbid`).
    doc_base: str = ""
    #: v66: зафиксированные индексы этапов {узел: индекс}. Этап 1 → 1,
    #: подэтап 2.1 → 2, 2.2 → 3, этап 3 → 4. Индекс выдаётся один раз и не
    #: пересчитывается, если план потом изменили (решение `freeze`).
    doc_index: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    source: str = "form"

    # ------------------------------------------------- v66: нумерация этапов

    def new_numbering(self) -> bool:
        """Дело создано в v66 — действуют номера `0001` / `0001-N`."""
        return dl.uses_new(self)

    def ensure_doc_base(self, used=()) -> str:
        """Общий номер обращения: назначаем свободный, если ещё не задан.

        Решение пользователя: номера предлагаются ПО ОБРАЩЕНИЯМ с возможностью
        ручной правки. Занятый номер не выдаётся (`forbid`).
        """
        # v66: `used` — номера ДРУГИХ дел (свой номер исключает вызывающий
        # через `GovCaseStore.used_doc_bases(exclude=…)`). Раньше свой номер
        # отсеивался здесь ПО ЗНАЧЕНИЮ, и если две базы совпадали, занятый
        # номер считался свободным (решение пользователя `forbid`).
        base = dl.normalize_base(self.doc_base)
        if base and dl.base_is_free(base, used):
            return base
        base = dl.suggest_base(used)
        self.doc_base = base
        if not self.numbering:
            self.numbering = dl.NEW_NUMBERING
        return base

    def doc_number_for(self, kind) -> str:
        """Номер постановления узла: «0001-2» (индекс фиксируется в деле).

        Для дел старой нумерации возвращает пустую строку — там номер
        documents остаётся прежним (`AG-1`, значение из формы или ответа ИИ).
        """
        base = dl.normalize_base(self.doc_base)
        if not base:
            return ""
        key = str(kind or "").strip()
        index = int(self.doc_index.get(key) or 0)
        if not index:
            index = dl.node_index(self.doc_index, key,
                                  plan_size=len(self.current_plan() or []))
            self.doc_index[key] = int(index)
        return dl.document_number(base, index)

    def doc_index_table(self) -> dict:
        """Копия зафиксированных индексов (для журнала и тестов)."""
        return dict(self.doc_index or {})

    # ------------------------------------------- v66: автозапрос постановления

    def auto_document_state(self, kind) -> str:
        """Состояние автоматического запроса постановления узла.

        Пусто — ещё не запрашивали; `requested` — заявка ушла (или в очереди);
        `done` — постановление узла получено; `failed` — не ушло, ждёт кнопки
        «Повторить отправку». Ровно один автоматический запрос на завершение
        узла (критерий 6 приёмки: без петель и дублей).
        """
        node = self.work_node(kind)
        return str(node.get("auto_document", "") or "")

    def set_auto_document(self, kind, state: str) -> dict:
        """Отметить состояние автозапроса в узле (сохраняется в `case.json`)."""
        return self.set_work_node(kind, auto_document=str(state or ""))

    def auto_document_due(self, kind) -> bool:
        """Нужен ли автоматический запрос постановления по этому узлу.

        Условия: узел завершён (ответ ИИ получен), автозапрос ещё не уходил и
        по узлу есть что выносить — для этапа 2 нужен план цепочки (цепочки
        клиент локально не выдумывает: если их нет, предлагается ручной повтор).
        """
        key = str(kind or "").strip()
        if not key:
            return False
        node = self.work_node(key)
        if not str(node.get("answer", "") or "").strip():
            return False
        if not node.get("done"):
            return False
        if self.auto_document_state(key) in ("requested", "done"):
            return False
        if key != "1":
            # v67 (п.3 обратной связи 66v, решение пользователя `one_call`):
            # по узлам 2.N и 3 постановление выносится ОДНИМ запросом — кнопка
            # «Этап N» сразу отправляет заявку «… (этап N) — ДОКУМЕНТ», и её
            # ответ печатает PNG. Второй, автоматический запрос по тому же узлу
            # больше не нужен: в выгрузке «Постановление_66v.7z» из-за него
            # каждый узел собирался дважды (12 `gov.rendered` на 6 узлов,
            # 15 снимков истории, «Версия-002» вместо «Версия-001»), а худший
            # ответ затирал лучший (узел 2.2: 1 страница вместо 2).
            # Автоматический запрос остаётся только у этапа 1: там сначала
            # идёт проверка обращения с цепочками, затем — документ
            # (согласовано: «для этапа 1 — как сейчас»).
            return False
        # v66 (критерий 5 приёмки): при отказе в принятии, переадресации и
        # прекращении постановление этапа 1 всё равно запрашивается, а этапы
        # 2 и 3 не открываются. С v67 автозапрос бывает только у этапа 1,
        # поэтому проверок на исход обращения и на план цепочки здесь больше
        # нет: они относились к узлам 2.N и 3.
        return True

    def stage_folder_v66(self, root, kind, step_label: str = "",
                         create: bool = True):
        """Папка этапа по правилам v66 (с подпапкой цепочки для этапа 2)."""
        return dl.stage_dir(root, self.number, self.doc_base, kind,
                            letter=self.work_letter if dl.node_step(kind) else "",
                            step_label=step_label, create=create)

    # ---------------------------------------------------- v62: узлы и цепочки

    def active_chain(self) -> dict:
        """Активный вариант цепочки этапа 2 ({} — цепочек ещё нет)."""
        for chain in self.work_chains or []:
            if str(chain.get("letter", "")) == self.work_letter:
                return chain
        if self.work_chains:
            return self.work_chains[0]
        return {}

    def use_chain(self, letter: str) -> bool:
        """Переключиться на другой вариант цепочки, не теряя прогресс (v62).

        Прогресс хранится ВНУТРИ варианта (`nodes`), поэтому переключение —
        это только смена активной буквы: вернувшись, прокурор увидит свои
        выполненные подэтапы.
        """
        letter = str(letter or "").strip().upper()[:1]
        for chain in self.work_chains or []:
            if str(chain.get("letter", "")) == letter:
                self.work_letter = letter
                self.work_plan = [dict(step) for step in chain.get("steps") or []]
                self.work_step = self.next_work_step() or 1
                self.touch()
                return True
        return False

    def apply_chains(self, chains: list) -> int:
        """Сохраняет варианты цепочки из ответа ИИ, не теряя прогресс (v62).

        Если вариант с той же буквой уже был, у него сохраняются ответы ИИ и
        сформированные PNG по совпавшим названиям подэтапов.
        """
        prepared = []
        for raw in chains or []:
            letter = str(raw.get("letter", "") or "").upper()[:1]
            steps = []
            for index, step in enumerate(raw.get("steps") or [], start=1):
                title = _clean(step.get("title", ""))
                if not title:
                    continue
                steps.append({"n": index,
                              "kind": _clean(step.get("kind", "")) or
                                      ogp_chain.step_kind_from_text(title),
                              "title": title,
                              "why": _clean(step.get("why", ""))})
            if not steps:
                continue
            chain = {"letter": letter or ogp_stages.chain_letters(
                        len(prepared) + 1)[-1],
                     "title": _clean(raw.get("title", "")) or "порядок от ИИ",
                     "why": _clean(raw.get("why", "")),
                     "steps": steps,
                     "nodes": {}}
            old_chain = {}
            for previous in self.work_chains or []:
                if str(previous.get("letter", "")) == chain["letter"]:
                    old_chain = previous
            old_by_title = {}
            for node_key, node in (old_chain.get("nodes") or {}).items():
                number = int(str(node_key).split(".")[-1] or 0)
                for step in old_chain.get("steps") or []:
                    if int(step.get("n") or 0) == number:
                        old_by_title[_key(step.get("title", ""))] = dict(node)
            for step in chain["steps"]:
                node = old_by_title.get(_key(step.get("title", ""))) or {}
                if node:
                    chain["nodes"][f"2.{step['n']}"] = node
            prepared.append(chain)
        if prepared:
            self.work_chains = prepared
            letters = [str(chain.get("letter", "")) for chain in prepared]
            if self.work_letter not in letters:
                self.work_letter = letters[0]
            self.work_plan = [dict(step)
                              for step in self.active_chain().get("steps") or []]
            self.work_step = self.next_work_step() or 1
        self.touch()
        return len(prepared)

    def set_custom_order(self, titles) -> None:
        """Ручной порядок цепочки (v62) — его согласует ИИ."""
        self.work_custom_order = [_clean(title) for title in (titles or [])
                                  if _clean(title)]
        self.touch()

    def apply_new_order(self, steps: list) -> int:
        """Применяет согласованный ИИ порядок к оставшимся подэтапам (v62).

        Пользователь: «выполненные подэтапы не трогаются, ИИ пересобирает
        порядок невыполненных и адаптирует этап 3». Поэтому шаги, у которых
        уже есть ответ ИИ, остаются на своих местах и со своими узлами.
        """
        nodes = dict((self.active_chain().get("nodes") or {}))
        done_titles = []
        for number in range(1, len(self.work_plan) + 1):
            node = nodes.get(f"2.{number}") or {}
            if str(node.get("answer", "") or "").strip():
                for step in self.work_plan:
                    if int(step.get("n") or 0) == number:
                        done_titles.append(step)
        new_steps = []
        for index, step in enumerate(done_titles, start=1):
            new_steps.append({"n": index,
                              "kind": step.get("kind", ""),
                              "title": step.get("title", ""),
                              "why": step.get("why", "")})
        done_count = len(new_steps)
        for raw in steps or []:
            title = _clean(raw.get("title", ""))
            if not title:
                continue
            if any(_key(step["title"]) == _key(title) for step in new_steps):
                continue
            new_steps.append({"n": len(new_steps) + 1,
                              "kind": _clean(raw.get("kind", "")) or
                                      ogp_chain.step_kind_from_text(title),
                              "title": title,
                              "why": _clean(raw.get("why", ""))})
        if not new_steps:
            return 0
        new_nodes = {}
        for number in range(1, done_count + 1):
            node = nodes.get(f"2.{number}")
            if node:
                new_nodes[f"2.{number}"] = node
        chain = self.active_chain()
        if chain:
            chain["steps"] = new_steps
            chain["nodes"] = new_nodes
        self.work_plan = [dict(step) for step in new_steps]
        self.work_step = self.next_work_step() or 1
        self.work_custom_order = []
        self.touch()
        return len(new_steps) - done_count

    def work_node(self, kind) -> dict:
        """Узел цепочки: этап 1, подэтап 2.N или этап 3 (v62).

        Узлы этапа 2 живут внутри варианта цепочки, поэтому переключение
        варианта не стирает прогресс другого.
        """
        key = str(kind or "").strip()
        if key.startswith("эт."):
            key = key[3:]
        parsed = ogp_stages.parse_node(key)
        if not parsed["etap"]:
            return {}
        if parsed["etap"] == 1:
            return self.work_nodes.get("1") or {}
        if parsed["etap"] == 3:
            return self.work_nodes.get("3") or {}
        chain = self.active_chain()
        return (chain.get("nodes") or {}).get(f"2.{parsed['step']}") or {}

    def set_work_node(self, kind, **values) -> dict:
        """Записать узел (ввод прокурора, ответ ИИ, PNG, «устарело»)."""
        key = str(kind or "").strip()
        parsed = ogp_stages.parse_node(key)
        if not parsed["etap"]:
            return {}
        if parsed["etap"] in (1, 3):
            node = dict(self.work_nodes.get(str(parsed["etap"])) or {})
            node.update(values)
            self.work_nodes[str(parsed["etap"])] = node
        else:
            chain = self.active_chain()
            if not chain:
                return {}
            nodes = dict(chain.get("nodes") or {})
            node = dict(nodes.get(f"2.{parsed['step']}") or {})
            node.update(values)
            nodes[f"2.{parsed['step']}"] = node
            chain["nodes"] = nodes
            # Шаги цепочки повторяют состояние узла: остальной код (цепочка,
            # список обращений, подсчёт прогресса) продолжает работать по
            # `work_plan`, не зная про узлы (v62).
            for step in self.work_plan:
                if int(step.get("n") or 0) != parsed["step"]:
                    continue
                step["done"] = bool(node.get("done"))
                step["png"] = str(node.get("png", "") or "")
                step["doc_number"] = str(node.get("doc_number", "") or "")
                step["when"] = str(node.get("when", "") or "")
                step["verdict"] = str(node.get("verdict", "") or "")
                step["note"] = str(node.get("input", "") or "")
                chain["steps"] = [dict(item) for item in self.work_plan]
        self.touch()
        return node

    def set_node_input(self, kind, text: str) -> bool:
        """Ввод прокурора (поле 1). Изменение после ответа ИИ → «устарело».

        Пользователь: «при изменении информации обязательно снова прогнать
        через ИИ» — поэтому старый ответ помечается устаревшим, а переход к
        следующему подэтапу блокируется до нового ответа.
        """
        node = self.work_node(kind)
        old = str(node.get("input", "") or "")
        new = str(text or "")
        if old == new:
            return False
        stale = bool(str(node.get("answer", "") or "").strip())
        self.set_work_node(kind, input=new, stale=stale)
        return True

    def clear_node(self, kind) -> None:
        # v66: сбрасываем и признак автозапроса — по новому ответу узла
        # постановление запрашивается заново (критерий 12 приёмки: два повтора
        # не затирают друг друга, старый комплект уходит в «Архив/Версия-…»).
        self.set_work_node(kind, input="", answer="", stale=False, done=False,
                           png="", when="", verdict="", auto_document="")

    # --------------------------------------------------------------- свойства

    def status_label(self) -> str:
        return CASE_STATUS_LABELS.get(self.status, CASE_STATUS_LABELS[STATUS_NEW])

    def status_color(self) -> str:
        return CASE_STATUS_COLORS.get(self.status, "#8f96bf")

    def accepted(self) -> bool:
        """Обращение принято к производству (можно писать обстоятельства)."""
        return self.status == STATUS_IN_PROGRESS

    def refused(self) -> bool:
        return self.status == STATUS_REFUSED

    def can_measure(self) -> bool:
        """Этап санкции открыт только после принятия обращения.

        Требование пользователя: «второй этап подключается только если первый
        пройден и обращение принято».
        """
        return self.accepted() or self.status == STATUS_CLOSED and any(
            stage.get("kind") == STAGE_INTAKE and stage.get("accepted")
            for stage in self.stages)

    def prosecutor_facts(self) -> str:
        """ВСЁ содержимое блоков «Обстоятельства» одним текстом.

        v56: это НЕ текст документа. Блоки «Обстоятельства» — вход для ИИ
        (замечание пользователя: «информация в данных Обстоятельства это то,
        что дается ИИ для анализа»), поэтому в PNG они больше не печатаются.
        """
        return self._facts_text({key: _clean(getattr(self, key, ""))
                                 for key in self.FACTS_KEYS})

    @staticmethod
    def _facts_text(values: dict) -> str:
        """Собирает текст обстоятельств из четырёх частей формы.

        Порядок и подписи — как просил пользователь: видеофиксация, доводы за
        и против, предложенное наказание и решение прокурора. Пустые части не
        печатаются.
        """
        parts = []
        video = _clean((values or {}).get("facts_video", ""))
        if video:
            parts.append(_sentence(video))
        arguments = []
        if _clean((values or {}).get("facts_for", "")):
            arguments.append("В пользу обращения: "
                             + _sentence(_clean(values["facts_for"])))
        if _clean((values or {}).get("facts_against", "")):
            arguments.append("Против обращения: "
                             + _sentence(_clean(values["facts_against"])))
        if arguments:
            parts.append(" ".join(arguments))
        if _clean((values or {}).get("proposal", "")):
            parts.append("Прокурор полагает возможным: "
                         + _sentence(_clean(values["proposal"])))
        if _clean((values or {}).get("own_decision", "")):
            parts.append("Решение прокурора: " + _sentence(_clean(values["own_decision"])))
        return "\n\n".join(parts)

    def has_facts(self) -> bool:
        return bool(self.prosecutor_facts().strip())

    # --------------------------------------------------- v56: обстоятельства

    #: Блоки вкладки «Обстоятельства» — это ВХОД для ИИ, а не текст документа.
    FACTS_KEYS = ("facts_video", "facts_for", "facts_against", "proposal",
                  "own_decision")

    def own_facts(self) -> dict:
        """Только то, что в блоках написал человек (v56).

        Значение блока минус то, что в него ранее записал ИИ. Если
        пользователь ничего не правил — блок считается «не своим» и в заявку
        не попадает: ИИ не должен получать обратно собственный пересказ.
        """
        out = {}
        for key in self.FACTS_KEYS:
            current = _clean(getattr(self, key, ""))
            if not current:
                continue
            written = _clean((self.ai_facts or {}).get(key, ""))
            if written:
                if current == written:
                    continue              # ровно текст ИИ — это не правка
                rest = current.replace(written, " ").strip(" \n\t;,.—")
                current = _clean(rest)
                if not current:
                    continue
            out[key] = current
        return out

    def own_facts_text(self) -> str:
        """«Обстоятельства» прокурора для заявки — только правки человека."""
        return self._facts_text(self.own_facts())

    def remember_ai_facts(self, values: dict) -> None:
        """Запоминает, что в блоки записал ИИ (v56)."""
        self.ai_facts = {str(key): _clean(value)
                         for key, value in (values or {}).items() if _clean(value)}

    def clear_facts(self) -> None:
        """Очищает блоки обстоятельств и память о тексте ИИ (v56)."""
        for key in self.FACTS_KEYS:
            setattr(self, key, "")
        self.ai_facts = {}

    # ------------------------------------------------------------------ этапы

    def plan(self) -> list:
        """Запланированная цепочка этапов (v43).

        Порядок устойчив: сначала минимальная цепочка «принятие → санкция»
        (этапы 1 и 2), затем дополнительные этапы в том порядке, в каком их
        добавил прокурор. Иначе добавленная «повторная проверка» вставала
        между ними и меняла номера уже существующих этапов — а номера входят
        в имена PNG-файлов (v43).
        """
        added = [stage.get("kind") for stage in self.stages if stage.get("kind")]
        kinds = list(DEFAULT_STAGE_PLAN)
        for kind in added:
            if kind not in kinds:
                kinds.append(kind)
        return kinds

    def next_stage(self) -> dict:
        """Следующий невыполненный этап: {kind, index} (или пустой словарь)."""
        done = [stage for stage in self.stages if stage.get("done")]
        planned = self.plan()
        for kind in planned:
            if kind == STAGE_MEASURE and not self.accepted():
                # Санкцию не выносим, пока обращение не принято.
                return {}
            if kind not in [stage.get("kind") for stage in done]:
                return {"kind": kind, "index": len(self.stages) + 1}
        return {}

    def intake_checked(self) -> bool:
        """«Проверка ИИ» уже выполнена по этому обращению (v43).

        Замечание: «пока не сделана Проверка ИИ, кнопка Сформировать должна
        быть недоступна». Проверка считается выполненной, когда дело вышло из
        состояния «Новое» либо этап принятия уже отмечен.
        """
        if self.status != STATUS_NEW:
            return True
        stage = self.stage_by_kind(STAGE_INTAKE)
        return bool(stage.get("done") or stage.get("accepted"))

    def open_stage_kind(self) -> str:
        """Какой этап открывается по делу (v43).

        Приоритет — этап, с которым прокурор работал последним: после
        «Проверки ИИ» и «Сформировать» этап не должен меняться сам (замечание
        про «этап 1 превращался в этап 2»). Если выбора ещё не было — первый
        невыполненный этап цепочки.
        """
        if self.stage_kind and self.stage_kind in STAGE_META:
            return self.stage_kind
        next_stage = self.next_stage()
        if next_stage:
            return next_stage["kind"]
        done = [stage.get("kind") for stage in self.stages if stage.get("done")]
        for kind in self.plan():
            if kind not in done:
                return kind
        return self.plan()[0] if self.plan() else STAGE_INTAKE

    def stage_number(self, kind: str) -> int:
        """Номер этапа для файлов и папок (см. `stage_index_for`)."""
        return stage_index_for(self.plan(), kind)

    def add_stage(self, kind: str) -> None:
        """Добавляет этап в цепочку (если такого ещё нет)."""
        if kind not in STAGE_META:
            return
        if kind in [stage.get("kind") for stage in self.stages]:
            return
        self.stages.append({"kind": kind, "done": False, "doc_number": "",
                            "png": "", "when": "", "accepted": False})
        self.touch()

    def mark_stage(self, kind: str, doc_number: str = "", png: str = "",
                   accepted: bool = False) -> None:
        """Отмечает этап выполненным и запоминает артефакты."""
        for stage in self.stages:
            if stage.get("kind") == kind:
                stage.update({"done": True, "doc_number": doc_number,
                              "png": png, "when": datetime.now().strftime("%d.%m.%Y %H:%M"),
                              "accepted": accepted})
                self.touch()
                return
        self.stages.append({"kind": kind, "done": True, "doc_number": doc_number,
                            "png": png, "accepted": accepted,
                            "when": datetime.now().strftime("%d.%m.%Y %H:%M")})
        self.touch()

    def stage_by_kind(self, kind: str) -> dict:
        for stage in self.stages:
            if stage.get("kind") == kind:
                return stage
        return {}

    # -------------------------------------------------------------- состояние

    def apply_intake(self, result: dict, force_accept: bool = False) -> None:
        """Применяет результат проверки ИИ к состоянию дела (v42)."""
        result = result or {}
        state = result.get("state", "")
        if force_accept:
            # Прокурор не согласен с отказом ИИ: основание отказа сохраняем
            # (пригодится при разборе), но решение — за прокурором.
            self.status = STATUS_IN_PROGRESS
            ai_basis = _clean(result.get("basis", ""))
            self.intake_basis = (
                f"решение прокурора вопреки ответу ИИ ({ai_basis})"
                if ai_basis else "решение прокурора вопреки ответу ИИ")
            self.intake_answer = str(result.get("raw", "") or "")
            self.intake_doc_type = _clean(result.get("doc_type_raw", "")
                                          or result.get("doc_type", ""))
            self.set_recommendations(
                ogp_stages.split_recommendations(self.intake_answer))
            self.mark_stage(STAGE_INTAKE, accepted=True)
            self.touch()
            return
        status = result.get("status") or INTAKE_STATUS_MAP.get(state, "")
        if not status:
            return
        self.status = status
        self.intake_basis = _clean(result.get("basis", ""))
        self.intake_answer = str(result.get("raw", "") or "")
        # v64: вид решения этапа 1 и рекомендации ИИ — из того же ответа.
        self.intake_doc_type = _clean(result.get("doc_type_raw", "")
                                      or result.get("doc_type", ""))
        self.set_recommendations(
            ogp_stages.split_recommendations(self.intake_answer))
        # v49: реквизиты обращения — из ключевых слов ответа ИИ, а не из
        # локального разбора текста обращения.
        self.apply_keywords(result.get("keywords") or {})
        if status == STATUS_IN_PROGRESS:
            self.mark_stage(STAGE_INTAKE, accepted=True)
        else:
            self.mark_stage(STAGE_INTAKE, accepted=False)
        self.touch()

    # ------------------------------------------------- v60: этап 1 и план шагов

    def set_outcome(self, outcome: str) -> None:
        """Исход этапа 1 (v60): один из пяти, см. `ogp_chain.OUTCOMES`.

        Замечание пользователя: «1 этап (принятие/отклонение) — тут ИИ
        анализирует входящее обращение, от пользователя никакой уточняющей
        информации не поступает». Прокурор может принять решение вопреки ИИ —
        тогда исход всё равно записывается, но с пометкой в основании.
        """
        if outcome not in ogp_chain.OUTCOMES:
            return
        self.outcome = outcome
        if outcome == ogp_chain.OUTCOME_ACCEPT:
            self.status = STATUS_IN_PROGRESS
        elif outcome in (ogp_chain.OUTCOME_REFUSE, ogp_chain.OUTCOME_REDIRECT,
                         ogp_chain.OUTCOME_STOP):
            # Отказ, переадресация и прекращение закрывают дело по существу:
            # этапы 2 и 3 не наступают (ТЗ, п. 2.1).
            self.status = STATUS_REFUSED if outcome == ogp_chain.OUTCOME_REFUSE \
                else STATUS_CLOSED
        # «Оставить без движения» статус не меняет: обращение ещё живо,
        # у заявителя 24 часа (ч. 1 ст. 12 Регламента).
        self.touch()

    def apply_plan(self, steps: list) -> int:
        """План этапа 2 из ответа ИИ (v60). Возвращает число шагов.

        Уже выполненные шаги не теряются: при повторном ответе ИИ план
        ДОПОЛНЯЕТСЯ, а совпавшие по названию шаги сохраняют номер документа и
        PNG. Иначе повторная «Проверка ИИ» стирала бы сделанную работу.
        """
        done = {}
        for step in self.work_plan:
            if step.get("done"):
                done[_key(step.get("title", ""))] = step
        plan = []
        for index, raw in enumerate(steps or [], start=1):
            title = _clean(raw.get("title", ""))
            if not title:
                continue
            step = {
                "n": index,
                "kind": _clean(raw.get("kind", "")) or ogp_chain.step_kind_from_text(title),
                "title": title,
                "why": _clean(raw.get("why", "")),
                "done": False,
                "doc_number": "",
                "png": "",
                "when": "",
                "note": "",
                # v61: положительный/отрицательный итог ИИ по этому шагу.
                # Этап 3 открывается, только когда ВСЕ шаги дали
                # положительный ответ (замечание пользователя).
                "verdict": "",
            }
            previous = done.get(_key(title))
            if previous:
                step.update({"done": True,
                             "doc_number": previous.get("doc_number", ""),
                             "png": previous.get("png", ""),
                             "when": previous.get("when", ""),
                             "note": previous.get("note", ""),
                             "verdict": previous.get("verdict", "")})
            plan.append(step)
        if plan:
            self.work_plan = plan
            if not self.work_step:
                self.work_step = self.next_work_step() or 1
        self.touch()
        return len(plan)

    def add_work_step(self, title: str, why: str = "") -> dict:
        """Прокурор добавляет свой шаг в цепочку (ТЗ: «вносить этапы самому»)."""
        title = _clean(title)
        if not title:
            return {}
        step = {"n": len(self.work_plan) + 1,
                "kind": ogp_chain.step_kind_from_text(title),
                "title": title, "why": _clean(why), "done": False,
                "doc_number": "", "png": "", "when": "", "note": ""}
        self.work_plan.append(step)
        self.touch()
        return step

    def remove_work_step(self, number: int) -> bool:
        """Убирает шаг из цепочки (например, ИИ предложил лишнее)."""
        rest = [step for step in self.work_plan if step.get("n") != number]
        if len(rest) == len(self.work_plan):
            return False
        for index, step in enumerate(rest, start=1):
            step["n"] = index
        self.work_plan = rest
        if self.work_step > len(rest):
            self.work_step = len(rest)
        self.touch()
        return True

    def mark_work_step(self, number: int, doc_number: str = "", png: str = "",
                       note: str = "", verdict: str = "") -> None:
        """Шаг этапа 2 выполнен: запоминает номер постановления и PNG."""
        for step in self.work_plan:
            if step.get("n") == number:
                step.update({"done": True,
                             "doc_number": _clean(doc_number),
                             "png": png,
                             "note": _clean(note),
                             "verdict": _clean(verdict) or step.get("verdict", ""),
                             "when": datetime.now().strftime("%d.%m.%Y %H:%M")})
                # Шаг выполнен — форма переходит к следующему невыполненному:
                # иначе «Сформировать» снова собирал бы уже вынесенное
                # постановление (замечание v60 о цепочке этапа 2).
                following = self.next_work_step()
                self.work_step = following or number
                self.touch()
                return

    def current_plan(self) -> list:
        """Шаги активного варианта цепочки (v63).

        `work_plan` — копия шагов активного варианта. Копия нужна старому коду,
        но легко «отстаёт»: `apply_chains()` сохранил три варианта и выставил
        `work_letter`, а `work_plan` остался прежним. Тогда `next_work_step()`
        искал шаг в чужом плане и возвращал 0 — форма рисовала «этап 3» раньше
        времени. Здесь единственный источник истины — активный вариант.
        """
        chain = self.active_chain() if self.work_chains else {}
        steps = list(chain.get("steps") or [])
        if steps:
            return steps
        return list(self.work_plan or [])

    def next_work_step(self) -> int:
        """Номер первого невыполненного шага этапа 2 (0 — все сделаны).

        v62: шаг считается пройденным, только когда ИИ ответил по нему и поле
        1 не менялось после ответа — иначе прокурор обязан прогнать подэтап
        снова.
        """
        for step in self.current_plan():
            number = int(step.get("n") or 0)
            node = self.work_node(f"2.{number}") if self.work_chains else {}
            if node:
                if not str(node.get("answer", "") or "").strip() or node.get("stale"):
                    return number
                continue
            if not step.get("done"):
                return number
        return 0

    def work_done(self) -> bool:
        return bool(self.work_plan) and not self.next_work_step()

    def current_work_step(self) -> dict:
        """Шаг этапа 2, с которым работает прокурор (пусто — цепочки нет)."""
        for step in self.work_plan:
            if step.get("n") == self.work_step:
                return step
        number = self.next_work_step()
        for step in self.work_plan:
            if step.get("n") == number:
                return step
        return {}

    def etap3_ready(self) -> bool:
        """Этап 3: принятие + все шаги этапа 2 пройдены (ТЗ, п. 2.1).

        v61: замечание пользователя — «как только все подэтапы этапа 2
        пройдены и все сделали, ИИ дал положительный ответ, то мы уже можем
        закрывать обращение и переходить к этапу 3». Поэтому одного «шаг
        выполнен» мало: нужен положительный итог ИИ по каждому шагу.
        """
        if self.outcome != ogp_chain.OUTCOME_ACCEPT:
            return False
        if not self.work_done():
            return False
        return ogp_flow.all_steps_positive(self)

    def etaps_summary(self) -> list:
        """Короткая строка состояния цепочки для формы и списка обращений."""
        if not self.outcome:
            return ["этап 1: не решён"]
        rows = [f"этап 1: {ogp_chain.outcome_label(self.outcome).lower()}"]
        if self.outcome != ogp_chain.OUTCOME_ACCEPT:
            rows.append("этап 2 и 3: не наступают")
            return rows
        total = len(self.work_plan)
        if not total:
            rows.append("этап 2: план не получен")
            return rows
        done = sum(1 for step in self.work_plan if step.get("done"))
        rows.append(f"этап 2: шагов {done} из {total}")
        if self.etap3_ready():
            rows.append("этап 3: можно выносить итог")
        return rows

    def apply_keywords(self, keywords: dict) -> None:
        """Сохраняет реквизиты обращения, присланные ИИ (v49).

        Новые значения дополняют уже известные: повторная «Проверка ИИ» может
        уточнить заявителя или нарушителя, но не должна обнулять реквизиты,
        которые ИИ подтвердил в прошлый раз.
        """
        cleaned = {}
        for key, value in dict(keywords or {}).items():
            text = _clean(value)
            if text:
                cleaned[str(key)] = text
        if not cleaned:
            return
        stored = dict(getattr(self, "intake_fields", {}) or {})
        stored.update(cleaned)
        self.intake_fields = stored
        self.touch()

    def keyword(self, key: str) -> str:
        """Реквизит обращения из ответа ИИ (пусто — ИИ его не назвал)."""
        return _clean((getattr(self, "intake_fields", {}) or {}).get(key, ""))

    def set_stage_kind(self, kind: str) -> None:
        """Запоминает этап, выбранный прокурором (v43)."""
        if kind in STAGE_META:
            self.stage_kind = kind
            self.touch()

    def set_status(self, status: str) -> None:
        if status in CASE_STATUS_LABELS:
            self.status = status
            self.touch()

    def close_work(self, reason: str = "", when: str = "") -> bool:
        """Закрывает обращение как отработанное (v63).

        Пользователь: «по нажатию обращение закрывается и помечается как
        отработанное». Отличие от `STATUS_REFUSED`: это не отказ в принятии, а
        нормальное завершение работы по делу — этап 3 выполнен. Документы,
        PNG и ответы ИИ остаются в деле; меняется только состояние.
        """
        self.status = STATUS_CLOSED
        self.closed_at = str(when or "")
        self.close_reason = _clean(reason)
        node3 = self.work_node("3") if callable(getattr(self, "work_node", None)) else {}
        if node3:
            self.set_work_node("3", done=True)
        self.touch()
        return True

    def set_recommendations(self, rows) -> None:
        """Рекомендации ИИ прокурору (блок «РЕКОМЕНДАЦИИ» ответа этапа 1)."""
        prepared = []
        for row in rows or []:
            if isinstance(row, dict):
                text = _clean(row.get("text", ""))
                if text:
                    prepared.append({"n": len(prepared) + 1, "text": text})
            elif _clean(row):
                prepared.append({"n": len(prepared) + 1, "text": _clean(row)})
        self.recommendations = prepared
        self.touch()

    def is_used(self) -> bool:
        """Есть ли в деле содержание (v67, п.1 обратной связи 66v).

        Решение пользователя `no_save_until_used`: «дело пишется на диск только
        когда появился текст обращения или ушла первая заявка; „Новое
        обращение“ и открытие формы дают чистую форму БЕЗ записи».

        Пустым («фантомным») считается дело, у которого нет ни текста
        обращения, ни номера обращения с форума, ни узлов/цепочек/плана, ни
        выданного номера постановления, ни ответа ИИ и ни решения по обращению.
        В выгрузке «Постановление_66v.7z» такими были дела 1, 3 и 4
        (`appeal_raw=""`, `work_plan=[]`, `status="new"`): они заняли общие
        номера 0001/0003/0004, и настоящее обращение стало №2 с `doc_base=0002`
        вместо `0001`.
        """
        if str(self.appeal_raw or "").strip():
            return True
        if str(getattr(self, "appeal_number", "") or "").strip():
            return True
        if str(getattr(self, "intake_answer", "") or "").strip():
            return True
        if str(getattr(self, "outcome", "") or "").strip():
            return True
        for name in ("work_nodes", "work_chains", "work_plan", "doc_index",
                     "ai_facts"):
            if getattr(self, name, None):
                return True
        if str(getattr(self, "doc_base", "") or "").strip() and \
                str(getattr(self, "status", "") or "") != STATUS_NEW:
            return True
        return False

    def touch(self) -> None:
        self.updated_at = time.time()

    def human_updated(self) -> str:
        return datetime.fromtimestamp(self.updated_at or time.time()).strftime(
            "%d.%m.%Y %H:%M")

    # ---------------------------------------------------------------- сериализация

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> "GovCase":
        data = data or {}
        allowed = {name for name in cls().__dict__}
        kwargs = {key: value for key, value in data.items() if key in allowed}
        case = cls(**kwargs)
        case.stages = [dict(stage) for stage in (data.get("stages") or [])
                       if isinstance(stage, dict)]
        # v49: реквизиты из ответа ИИ читаются как словарь (старые case.json
        # этого поля не имеют — тогда он просто пустой).
        fields = data.get("intake_fields")
        case.intake_fields = dict(fields) if isinstance(fields, dict) else {}
        # v56: что в блоках «Обстоятельства» записал ИИ (старые дела этого
        # поля не имеют — тогда считается, что ИИ туда ничего не писал).
        ai_facts = data.get("ai_facts")
        case.ai_facts = dict(ai_facts) if isinstance(ai_facts, dict) else {}
        # v66: зафиксированные индексы этапов (старые дела поля не имеют —
        # для них действует прежняя нумерация).
        index = data.get("doc_index")
        case.doc_index = {str(key): int(value)
                          for key, value in dict(index).items()
                          if str(key) and str(value or "").strip().isdigit()} \
            if isinstance(index, dict) else {}
        # v64: рекомендации ИИ (список словарей) — как `work_plan`.
        reco = data.get("recommendations")
        case.recommendations = [dict(row) for row in reco if isinstance(row, dict)] \
            if isinstance(reco, list) else []
        # v60: цепочка этапов и исход этапа 1. Старые дела (v59 и раньше)
        # этих полей не имеют: исход восстанавливается из статуса.
        plan = data.get("work_plan")
        case.work_plan = [dict(step) for step in plan if isinstance(step, dict)] \
            if isinstance(plan, list) else []
        # v62: варианты цепочки, узлы и ручной порядок.
        chains = data.get("work_chains")
        case.work_chains = []
        for chain in chains if isinstance(chains, list) else []:
            if not isinstance(chain, dict):
                continue
            prepared = dict(chain)
            prepared["steps"] = [dict(step) for step in chain.get("steps") or []
                                 if isinstance(step, dict)]
            nodes = chain.get("nodes")
            prepared["nodes"] = {str(k): dict(v) for k, v in nodes.items()
                                 if isinstance(v, dict)} \
                if isinstance(nodes, dict) else {}
            case.work_chains.append(prepared)
        nodes = data.get("work_nodes")
        case.work_nodes = {str(k): dict(v) for k, v in nodes.items()
                           if isinstance(v, dict)} \
            if isinstance(nodes, dict) else {}
        order = data.get("work_custom_order")
        case.work_custom_order = [str(item) for item in order] \
            if isinstance(order, list) else []
        # Старое дело (v60–v61): одного плана достаточно — он становится
        # единственным вариантом «A», а его шаги переносятся в узлы.
        if not case.work_chains and case.work_plan:
            legacy_nodes = {}
            for step in case.work_plan:
                number = int(step.get("n") or 0)
                if not number:
                    continue
                legacy_nodes[f"2.{number}"] = {
                    "input": str(step.get("note", "") or ""),
                    "answer": "",
                    "stale": False,
                    "done": bool(step.get("done")),
                    "png": str(step.get("png", "") or ""),
                    "doc_number": str(step.get("doc_number", "") or ""),
                    "when": str(step.get("when", "") or ""),
                    "verdict": str(step.get("verdict", "") or ""),
                }
            case.work_chains = [{"letter": "A", "title": "порядок от ИИ",
                                 "why": "",
                                 "steps": [dict(step) for step in case.work_plan],
                                 "nodes": legacy_nodes}]
            case.work_letter = "A"
        if case.work_chains:
            if case.work_letter not in [str(c.get("letter", ""))
                                        for c in case.work_chains]:
                case.work_letter = str(case.work_chains[0].get("letter", "A"))
        if not case.outcome:
            if case.status == STATUS_IN_PROGRESS:
                case.outcome = ogp_chain.OUTCOME_ACCEPT
            elif case.status == STATUS_REFUSED:
                case.outcome = ogp_chain.OUTCOME_REFUSE
        return case

    def label(self) -> str:
        """Строка списка: «№1 · Lordik Claw · В процессе»."""
        applicant = parse_applicant(self.appeal_raw)
        parts = [f"№{self.number or '—'}"]
        if applicant:
            parts.append(applicant)
        parts.append(self.status_label())
        return " · ".join(parts)


#: Подписи этапов для стартового окна: «✓ этап 1 принятие».
def stage_progress(case: "GovCase") -> str:
    """Сколько этапов пройдено и какой следующий (v43)."""
    done = [stage for stage in case.stages if stage.get("done")]
    total = len(case.plan())
    if not case.intake_checked():
        return "проверка ИИ не выполнена"
    if getattr(case, "outcome", "") and case.outcome != ogp_chain.OUTCOME_ACCEPT:
        return f"этап 1: {ogp_chain.outcome_label(case.outcome).lower()}"
    if getattr(case, "work_plan", None):
        total_steps = len(case.work_plan)
        done_steps = sum(1 for step in case.work_plan if step.get("done"))
        if done_steps < total_steps:
            return f"этап 2: шагов {done_steps} из {total_steps}"
    if len(done) >= total:
        return f"этапов {len(done)} из {total} — дело завершено"
    return (f"этапов {len(done)} из {total}, следующий — "
            f"{stage_title(case.open_stage_kind()).split('.')[0].strip()}")


def intake_summary(case: "GovCase") -> str:
    """Реквизиты обращения, названные ИИ: «заявитель X 34074; нарушитель Y».

    v67 (п.5 обратной связи 66v): в «Документы → Обращения» строка «№2
    заявитель не распознан» не обновлялась после «Этапа 1», хотя форма уже
    показывала «Реквизиты из ответа ИИ: заявитель Lorenzo Shroud 34074; …»
    (выгрузка «Постановление_66v.7z», `intake_fields` дела 2). Список брал
    заявителя из ТЕКСТА обращения, а ответ ИИ игнорировал. Формат тот же, что
    в форме (`_keyword_summary`), чтобы прокурор видел одни и те же слова.
    """
    fields = dict(getattr(case, "intake_fields", {}) or {})
    parts = []
    applicant = _clean(fields.get("applicant_name", ""))
    if applicant:
        ident = _clean(fields.get("applicant_id", ""))
        parts.append((f"заявитель {applicant} {ident}" if ident
                      else f"заявитель {applicant}"))
    subject = _clean(fields.get("subject_name", ""))
    if subject:
        department = _clean(fields.get("subject_department", ""))
        parts.append((f"нарушитель {subject} ({department})" if department
                      else f"нарушитель {subject}"))
    when = _clean(fields.get("event_date", "")) or _clean(fields.get("appeal_date", ""))
    if when:
        parts.append(f"событие {when}")
    return "; ".join(parts)


def case_row(case: "GovCase") -> dict:
    """Одна строка стартового окна «Обращения» (v43).

    Возвращает данные, а не виджеты: список обращений проверяется тестами, а
    форма только показывает готовые поля.

    v67 (п.5): заявитель и нарушитель берутся из полей ответа ИИ
    (`intake_fields`), а текст обращения — запасной вариант, пока «Этап 1» не
    выполнен. Раньше список всегда показывал «заявитель не распознан», если
    `parse_applicant` не нашёл имени в тексте обращения.
    """
    applicant = case.keyword("applicant_name") or parse_applicant(case.appeal_raw)
    subject = case.keyword("subject_name")
    requisites = intake_summary(case)
    number = f"№{case.number or '—'}"
    forum = f" (с форума {case.appeal_number})" if case.appeal_number else ""
    title = f"{number}{forum} · {applicant or 'заявитель не распознан'}"
    if subject and subject not in title:
        title = f"{title} · нарушитель {subject}"
    closed = bool(getattr(case, "closed_at", ""))
    return {
        "number": case.number,
        "title": title,
        "closed_at": getattr(case, "closed_at", ""),
        "closed": closed,
        "status": (f"{case.status_label()} · отработано {case.closed_at}"
                   if closed else case.status_label()),
        "status_key": case.status,
        "color": case.status_color(),
        "progress": stage_progress(case),
        "updated": case.human_updated(),
        "appeal_raw": case.appeal_raw,
        # v67 (п.5): реквизиты из ответа ИИ — отдельная строка списка.
        "applicant": applicant,
        "subject": subject,
        "requisites": requisites,
    }


def case_rows(store) -> list:
    """Все дела для стартового окна, свежие сверху (v43)."""
    return [case_row(case) for case in store.items()]


def _clean(text) -> str:
    return " ".join(str(text or "").split())


def _key(text) -> str:
    return _clean(text).lower().strip(" .:;")


def _sentence(text: str) -> str:
    """Закрывает предложение точкой, чтобы части не склеивались (v42)."""
    body = str(text or "").strip()
    if not body:
        return ""
    return body if body[-1] in ".!?…" else body + "." 


def _rmtree_if_empty(folder) -> int:
    """Убирает каталог, если он пуст (в том числе пустыми подкаталогами).

    v67 (находка 4, одобрена пользователем): вместе с фантомным делом удаляется
    и его папка «Обращение №N» — но ТОЛЬКО если в ней ничего нет. Папка
    «Обращение №1» из выгрузки «Постановление_66v.7z» не пуста (в ней лежит
    дело прежней версии `AG-1`), поэтому такой каталог остаётся нетронутым.
    Возвращает число удалённых каталогов.
    """
    folder = Path(folder)
    if not folder.is_dir():
        return 0
    removed = 0
    for path in sorted(folder.rglob("*"), key=lambda item: len(item.parts),
                       reverse=True):
        try:
            if path.is_dir() and not any(path.iterdir()):
                path.rmdir()
                removed += 1
        except OSError:
            continue
    try:
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
            removed += 1
    except OSError:
        pass
    return removed


def parse_applicant(appeal_raw: str) -> str:
    """Имя заявителя из обращения — для подписи в списке дел."""
    try:
        import postanovlenie_prokuror_data as gdata
        parsed = gdata.parse_appeal_text(str(appeal_raw or ""))
        name = (parsed.get("applicant_name") or "").strip()
        if name:
            return name
    except Exception:
        pass
    match = re.search(r"Данные о заявителе:?\s*([А-ЯЁA-Z][\w\-]+(?:\s+[А-ЯЁA-Z][\w\-]+)+)",
                      str(appeal_raw or ""), re.IGNORECASE)
    return match.group(1) if match else ""


# --------------------------------------------------------------------------- #
#  Хранилище дел
# --------------------------------------------------------------------------- #

class GovCaseStore:
    """Папка дел: `postanovleniya/cases/<номер>/case.json` (v42)."""

    def __init__(self, root):
        self.root = Path(root) / "cases"
        self.root.mkdir(parents=True, exist_ok=True)

    def folder(self, number: str) -> Path:
        safe = re.sub(r"[^\w\-.]", "_", str(number or "").strip()) or "без-номера"
        return self.root / safe

    def path(self, number: str) -> Path:
        return self.folder(number) / "case.json"

    # ------------------------------------------------------------------ запись

    def appeal_folder(self, number: str) -> Path:
        """Папка обращения в корне документов: `Обращение №1` (v53).

        Создаётся сразу при заведении обращения — до того, как напечатан хоть
        один PNG, — чтобы в выбранной папке было видно дело целиком, а не
        отдельные «AG-1» вперемешку с другими обращениями (замечание:
        «при создании обращения необходимо создавать папку „Обращение
        №_номер_обращения“ и в неё уже класть папки AG-1, AG-2 и т.д.»).
        """
        return base.appeal_folder(self.root.parent, number)

    def save(self, case: GovCase, force: bool = False) -> Path | None:
        """Пишет дело на диск. Пустое дело НЕ сохраняется (v67, п.1).

        Решение пользователя `no_save_until_used`: «дело пишется на диск только
        когда появился текст обращения или ушла первая заявка; „Новое
        обращение“ и открытие формы дают чистую форму БЕЗ записи».

        Раньше форма сохраняла чистое дело сразу (`new_case()` →
        `self.cases.save(...)`), и каждое открытие формы заводило «фантом»:
        в выгрузке «Постановление_66v.7z» это дела 1, 3 и 4 с пустым
        `appeal_raw`, занявшие общие номера 0001/0003/0004, из-за чего
        настоящее обращение получило «0002-X» вместо «0001-X».

        `force=True` — сохранить в любом случае (служебные сценарии и тесты).
        Возвращает путь к `case.json` или `None`, если запись пропущена.
        """
        if not case.number:
            case.number = self.next_number()
        case.touch()
        if not force and not case.is_used():
            base.diag_event("gov.case_not_saved_empty",
                            number=str(case.number or ""))
            return None
        # v53: папка обращения появляется вместе с делом, а не при печати PNG.
        # v67 (находка 4): только вместе с НАСТОЯЩИМ делом — пустые папки
        # «Обращение №3», «Обращение №4» в выгрузке появились именно отсюда.
        try:
            self.appeal_folder(case.number)
        except Exception:
            pass
        path = self.path(case.number)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(case.to_dict(), ensure_ascii=False, indent=2),
                        encoding="utf-8")
        return path

    def purge_empty(self) -> list:
        """Убирает «фантомные» дела, которые уже успели появиться (v67, п.1).

        Решение пользователя: «уже лежащие пустые дела (`appeal_raw` пуст, узлов
        нет) удаляются при старте вместе с их пустыми папками; номер и
        `doc_base=0001` достаются первому настоящему обращению».

        Удаляется запись `cases/<номер>/case.json`, её пустая папка и ПУСТАЯ
        папка «Обращение №<номер>». Если в папке обращения что-то лежит
        (например, дело прежней версии `AG-1` в «Обращение №1») — она остаётся.
        Возвращает список удалённых номеров.
        """
        removed = []
        for case in self.items():
            try:
                if case.is_used():
                    continue
            except Exception:
                continue
            number = str(case.number or "").strip()
            if not number:
                continue
            path = self.path(number)
            try:
                if path.is_file():
                    path.unlink()
            except OSError:
                base.diag_event("gov.case_purge_failed", number=number)
                continue
            _rmtree_if_empty(self.folder(number))
            folders = _rmtree_if_empty(
                self.root.parent / base.appeal_folder_name(number))
            removed.append(number)
            base.diag_event("gov.case_purged", number=number,
                            doc_base=str(case.doc_base or ""),
                            appeal_folders=folders)
        return removed

    def merge_save(self, number: str, **values) -> GovCase | None:
        """Обновить ОТДЕЛЬНЫЕ поля дела, не затирая остальные (v66).

        Пункт 6 диагноза по выгрузке `Постановление_65v.7z`: у клиента две копии
        `GovCase` — своя в форме и свежая в `main._apply_gov_answer`. Форма
        сохраняла свою (устаревшую) копию, и из `case.json` пропадали
        `doc_number`, `png` и ответ узла, которые только что записал main.

        Поэтому поля, которые форма знает лучше всех (блоки «Обстоятельства»,
        ввод прокурора), пишутся не целым объектом, а поверх последней записи на
        диске: словарь-значение (`ai_facts`, `doc_index`) объединяется,
        остальные поля заменяются.
        """
        case = self.load(str(number or ""))
        if case is None:
            return None
        for key, value in dict(values or {}).items():
            if not hasattr(case, key):
                continue
            current = getattr(case, key)
            if isinstance(current, dict) and isinstance(value, dict):
                merged = dict(current)
                merged.update(value)
                setattr(case, key, merged)
            elif isinstance(current, list) and isinstance(value, list):
                setattr(case, key, list(value))
            else:
                setattr(case, key, value)
        case.touch()
        self.save(case)
        return case

    def load(self, number: str) -> GovCase | None:
        path = self.path(number)
        if not path.exists():
            return None
        try:
            return GovCase.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            return None

    def delete(self, number: str) -> bool:
        path = self.path(number)
        try:
            path.unlink()
            return True
        except Exception:
            return False

    def find(self, number) -> GovCase | None:
        """Дело по номеру или `None`, если номер пустой/некорректный (v43.4).

        `load()` вызывают из обработчиков ответа ИИ, где номер приходит из
        записи отложенной отправки. Пустое значение раньше превращалось в
        папку «без-номера», а любое исключение при чтении уходило в
        `sys.excepthook` и выглядело как «ошибка после ответа ИИ».
        """
        text = str(number or "").strip()
        if not text:
            return None
        try:
            return self.load(text)
        except Exception:
            return None

    def items(self) -> list:
        """Все дела, свежие сверху."""
        cases = []
        for path in sorted(self.root.glob("*/case.json")):
            try:
                cases.append(GovCase.from_dict(
                    json.loads(path.read_text(encoding="utf-8"))))
            except Exception:
                continue
        cases.sort(key=lambda case: case.updated_at, reverse=True)
        return cases

    def labels(self) -> list:
        return [case.label() for case in self.items()]

    def next_number(self) -> str:
        """Следующий номер дела: максимальный + 1 (по всем делам)."""
        top = 0
        for case in self.items():
            match = re.search(r"\d+", str(case.number or ""))
            if match:
                top = max(top, int(match.group(0)))
        for path in self.root.glob("*"):
            match = re.fullmatch(r"(\d+)", path.name)
            if match:
                top = max(top, int(match.group(1)))
        return str(top + 1)

    def find_by_appeal(self, appeal_number: str) -> GovCase | None:
        """Дело по номеру обращения с форума (если такой уже заведён)."""
        target = _key(appeal_number)
        if not target:
            return None
        for case in self.items():
            if _key(case.appeal_number) == target:
                return case
        return None

    def open_or_create(self, appeal_raw: str = "") -> GovCase:
        """Открывает дело по тексту обращения или заводит новое.

        Одно и то же обращение, вставленное повторно, не создаёт второе дело:
        ищем по совпадению текста (без пробелов и регистра).
        """
        text = str(appeal_raw or "").strip()
        if text:
            flat = _key(text)
            for case in self.items():
                if flat and _key(case.appeal_raw) == flat:
                    case.appeal_raw = text
                    return case
        case = GovCase(number=self.next_number(), appeal_raw=text)
        # v66: новое обращение сразу получает общий номер набора постановлений
        # («0001», «0002», …) и признак новой нумерации. Решение пользователя
        # `v66_only`: для дел, созданных до v66, правила не меняются.
        case.numbering = dl.NEW_NUMBERING
        case.ensure_doc_base(self.used_doc_bases(exclude=case.number))
        self.save(case)
        return case

    def used_doc_bases(self, exclude: str = "") -> list:
        """Общие номера, уже занятые ДРУГИМИ обращениями (v66).

        `exclude` — номер своего дела: свой номер себе не мешает, а номер
        ДРУГОГО дела с той же базой мешает (решение пользователя `forbid`:
        занятым общим номером распорядиться нельзя).
        """
        skip = str(exclude or "").strip()
        return [str(case.doc_base or "") for case in self.items()
                if str(case.doc_base or "").strip()
                and (not skip or str(case.number or "") != skip)]


# --------------------------------------------------------------------------- #
#  Заявка для ИИ на проверку обращения
# --------------------------------------------------------------------------- #

def intake_request(case: GovCase, prosecutor: str = "") -> str:
    """Текст запроса в чат: «проверь обращение и скажи, принято или отказано».

    Формат ответа задан жёстко: клиент читает служебный блок, а не
    рассуждения. Всё остальное ИИ пишет по промту.
    """
    lines = [
        "ЗАЯВКА НА ПРОВЕРКУ ОБРАЩЕНИЯ (этап 1)",
        "",
        "Проверь обращение по Регламенту подачи обращений и скажи, принимается "
        "оно к производству Офиса Генерального прокурора или в принятии "
        "отказывается. Оценивай ТОЛЬКО само обращение и его форму: личность "
        "заявителя, предмет обращения, описание события, дата и время, "
        "нарушитель, доказательства, подпись и расшифровка. Санкцию не "
        "назначай, уголовное дело не возбуждай.",
        "",
        # v44: варианты решения перечислены БЕЗ шаблона «Состояние: …».
        # Раньше строка «Если есть основание для отказа — состояние «Отказано»»
        # заставляла разбор считать СВОЮ заявку ответом ИИ с отказом.
        "Реши, подлежит ли обращение принятию по форме и Регламенту, и укажи "
        "решение служебной строкой (см. ниже). Если вопрос не нашего "
        "ведомства — укажи адресата. Если обращение отозвано или утратило "
        "предмет — так и укажи.",
        "",
        # v44: в заявке НЕТ служебных маркеров ответа. Раньше описание
        # формата печаталось теми же строками, что и настоящий ответ
        # («===ПРОВЕРКА ОБРАЩЕНИЯ===», «Состояние: Принято или Отказано»,
        # «===КОНЕЦ ПРОВЕРКИ===»), и клиент принимал СВОЮ заявку за ответ ИИ:
        # разбор возвращал «Отказано» с основанием «норма Регламента с номером
        # статьи…». Теперь формат описан словами, без маркеров.
        # v44: НИ ОДНОЙ строки-маркера ответа в заявке быть не должно —
        # иначе клиент принимает свою заявку за готовый ответ ИИ.
        "Ответь строго служебными блоками, без вступлений и без описания "
        "вложений. Начни ответ маркером проверки обращения (он задан в "
        "промте), затем строки «Состояние:», «Основание:», «Вид решения:», "
        "«Что проверить:», затем маркер окончания проверки. Если обращение "
        "принято — следом напечатай блоки вариантов цепочки этапа 2 и блок "
        "рекомендаций, и ТОЛЬКО В САМОМ КОНЦЕ — маркер окончания ответа. "
        "Маркер окончания ответа печатай один раз и последним: всё, что "
        "напечатано после него, клиент отбрасывает.",
        # v62 (замечание пользователя): состояний этапа 1 ровно три, и этого
        # достаточно: «недостатки» и «прекращение» из прежней схемы не
        # открывают работу, поэтому для нас это отказ.
        f"В служебной строке состояния укажи ровно одно слово-решение: "
        f"{INTAKE_ACCEPTED}, {INTAKE_REFUSED} или {INTAKE_REDIRECTED}.",
        f"Выбирай решение: {INTAKE_ACCEPTED} — обращение по форме и по "
        f"существу нашего ведомства (дальше этап 2); {INTAKE_REFUSED} — "
        f"нарушения формы или основание статьи 16 Регламента (в «Основание:» "
        f"обязателен НОМЕР конкретного пункта); {INTAKE_REDIRECTED} — вопрос "
        f"чужого органа, укажи конкретный орган по статье 19 Регламента. "
        f"Других состояний нет: обращение, которое не идёт в работу, — это "
        f"отказ.",
        "В строке «Основание:» — норма Регламента с номером статьи, части и "
        "пункта (для отказа обязательно; для принятия — что именно проверено).",
        # v49: реквизиты обращения определяет ИИ — он же присылает их
        # ключевыми словами, и клиент подставляет их в форму постановления.
        # Строка задана без двоеточий, чтобы собственная заявка не выглядела
        # готовым ответом (v44): значения приходят только от ИИ.
        "Дополнительно к служебным строкам перечисли реквизиты обращения "
        "ключевыми словами по одному в строке — заявитель, ID заявителя, "
        "представитель заявителя, нарушитель, удостоверение нарушителя, "
        "организация нарушителя, дата и время события, предмет обращения, "
        "дата обращения. Значения бери только из текста обращения.",
        "",
        # v60: ТЗ — «ИИ в зависимости от ситуации анализирует, что необходимо
        # сделать по описанному… выстроить цепочку действий сразу». Поэтому
        # вместе с решением «Принято» ИИ присылает план этапа 2: конкретные
        # постановления по порядку. Клиент показывает их списком в GUI.
        # v62 (замечание пользователя): «пусть ИИ даёт минимум 3 цепочки,
        # которые предлагаются пользователю на выбор».
        # v66 (решение пользователя): не «минимум три», а ОБЪЕКТИВНЫЙ минимум —
        # «если ситуация предвещает 1-2 ситуации или 3-4 и далее, то пусть так
        # и будет. Незачем ограничиваться 3 цепочками. Может быть будет всего
        # 1 цепочка».
        "ЕСЛИ обращение принимается к производству — тут же предложи варианты "
        "цепочки этапа 2: столько законных путей решения, сколько ОБЪЕКТИВНО "
        "возможно по этой ситуации. Вариант может быть один, два, три и "
        "больше: выдумывать альтернативы ради количества не нужно, но и реально "
        "доступные законные пути не обрезай. Обозначай варианты буквами A, B, "
        "C и далее — это разные законные пути решения в правильном порядке "
        "(уголовное дело, истребование видеофиксации или информации, вызов на "
        "допрос, запрос личного дела, приостановление трудового договора, "
        "внесение изменений, санкция, передача дела в суд). Опирайся на "
        "блок-схему Офиса и материалы обращения: в варианте от одного до "
        "десяти подэтапов.",
        "Каждый вариант верни отдельным блоком (точные маркеры заданы в "
        "промте): буква и название варианта, строка «ЗАЧЕМ:» — чем этот путь "
        "лучше остальных и чем рискует, затем по одному постановлению в строке: "
        "номер, название постановления, через тире — зачем этот подэтап нужен. "
        "Если обращение не принято (отказ или переадресация) — блоков цепочек "
        "не печатай.",
    ]
    if prosecutor:
        lines.insert(2, f"Прокурор: {prosecutor}")
        lines.insert(3, "")
    if case.appeal_number:
        lines.insert(2, f"Номер обращения: {case.appeal_number}")
    # v50 (замечание пользователя): «текст обращения экспортируй в файл
    # „Обращение.txt" и экспортируй его в ИИ, минимум текста в поле запроса».
    # Сам текст обращения в заявку больше НЕ попадает — он приходит отдельным
    # вложением (ai_request.APPEAL_FILE_NAME), а здесь остаётся только ссылка
    # на файл и напоминание формата.
    lines.append("")
    lines.append("ОБРАЩЕНИЕ ГРАЖДАНИНА — во вложении «Обращение.txt» "
                 "(текст целиком, форма ОГП).")
    lines.append("Читай обращение из этого файла, служебный блок проверки "
                 "верни по формату промта.")
    return "\n".join(lines).rstrip() + "\n"


def node_request(case: GovCase, kind, prosecutor: str = "") -> str:
    """Заявка в ИИ по конкретному узлу: этап 1, подэтап 2.N или этап 3 (v62).

    Пользователь: «чтобы ИИ помнила, что она до этого отвечала… работая на
    этапе 2.3, в ИИ внутри документа (.txt) отсылается информация из поля
    „Обращение", ответ по „Этап 1", ответ по „Этап 2.1", „Этап 2.2"». Поэтому
    в заявку идёт весь `ogp_stages.context_block`, а под ним — задача узла и
    поле 1 (что стало известно).
    """
    parsed = ogp_stages.parse_node(kind)
    etap, step = parsed["etap"], parsed["step"]
    node = case.work_node(kind)
    user_input = str(node.get("input", "") or "").strip()
    lines: list = []
    if etap == 1:
        return intake_request(case, prosecutor)
    lines.append("ЗАЯВКА НА ПОСТАНОВЛЕНИЕ ПО ОБРАЩЕНИЮ (этап "
                 f"{ogp_stages.step_label(case.work_letter, step) if etap == 2 else 3})")
    lines.extend(_number_lines(case, kind))
    if prosecutor:
        lines.append(f"Прокурор: {prosecutor}")
    lines.append("")
    lines.append(ogp_stages.context_block(case, kind))
    if etap == 2:
        current = {}
        for item in case.work_plan or []:
            if int(item.get("n") or 0) == step:
                current = item
                break
        total = len(case.work_plan or [])
        lines.append(f"ЗАДАЧА: вынеси постановление по подэтапу 2.{step} из "
                     f"{total} — «{current.get('title', '')}».")
        if current.get("why"):
            lines.append(f"ЗАЧЕМ ЭТОТ ПОДЭТАП: {current.get('why')}")
        lines.append(f"ВАРИАНТ ЦЕПОЧКИ: {case.work_letter} — "
                     f"{case.active_chain().get('title', '')}")
        if user_input:
            lines.append("")
            lines.append("ЧТО СТАЛО ИЗВЕСТНО (ввод прокурора, учти обязательно):")
            lines.append(user_input)
        lines.append("")
        lines.append("Вынеси РОВНО этот документ и не подменяй его итоговым "
                     "решением по обращению. В резолюции однозначно скажи, "
                     "выполнен ли подэтап (истребовать / вызвать / установлено / "
                     "подтверждено) или нет (отказать / не установлено / "
                     "прекратить): по этой фразе клиент поймёт итог шага.")
        lines.extend(_facts_lines(case, stage_meta(STAGE_MEASURE)))
    else:
        lines.append("ЗАДАЧА: прими ИТОГОВОЕ решение по обращению (этап 3).")
        lines.append("Сам выбери вид завершения по смыслу обращения, решения "
                     "этапа 1 и всех пройденных подэтапов этапа 2: завершение "
                     "производства по обращению, завершение уголовного дела и "
                     "передача его в окружной суд, наложение санкции, "
                     "прекращение производства — тот, который действительно "
                     "следует из собранных материалов. Обоснуй нормой.")
        if user_input:
            lines.append("")
            lines.append("ЧТО ДОБАВИЛ ПРОКУРОР (учти обязательно):")
            lines.append(user_input)
    lines.extend(_facts_lines(case, stage_meta(STAGE_CLOSE)))
    lines.append("")
    lines.append(doc_placeholder())
    return "\n".join(lines).rstrip() + "\n"


def _number_lines(case: GovCase, kind) -> list:
    """Строка номера постановления для заявки узла (v66, критерий 15).

    Приёмка v66: «один и тот же номер постановления используется в форме,
    в заявке, в PNG, в данных дела и в именах файлов и папок». Клиент номер
    назначает сам (`GovCase.doc_number_for`) и передаёт его ИИ — иначе ИИ
    вписывает в поле «Номер» свой вариант («1», «001», «AG-1»), и комплект
    дела расходится с формой. Для дел старой нумерации строки нет.
    """
    number = node_doc_number(case, kind)
    if not number:
        return []
    return [f"НОМЕР ПОСТАНОВЛЕНИЯ: {number} — назначен клиентом; впиши его в "
            "поле «Номер» дословно, свой вариант не придумывай."]


def _facts_lines(case: GovCase, meta: dict) -> list:
    """Блоки «Обстоятельства» прокурора для заявки (v66).

    Обнаружено при разборе v66: с v64 эти строки печатала только
    `gov_case.stage_request`, которая перестала вызываться (форма отправляет
    `node_request` и `node_document_request`). В результате правки прокурора в
    блоках «Что видно на видеофиксации», «Доводы за / против», «Предлагаемое
    наказание» и «Решение прокурора» до ИИ НЕ ДОХОДИЛИ, хотя по решению v56
    именно они — вход для анализа.

    Правило v56 сохранено: в заявку идут ТОЛЬКО правки человека
    (`case.own_facts_text()`), текст, который в эти блоки записал сам ИИ,
    обратно ему не отправляется.
    """
    if not meta.get("needs_facts"):
        return []
    facts = case.own_facts_text()
    if not facts:
        return []
    label = _clean(meta.get("needs_facts_label")) or "Обстоятельства прокурора"
    return ["", f"ОБСТОЯТЕЛЬСТВА ПРОКУРОРА ({label}) — учти обязательно:",
            facts]


def node_document_request(case: GovCase, kind, prosecutor: str = "") -> str:
    """Заявка кнопки «Сформировать»: ДОКУМЕНТ по узлу (v64).

    Решение пользователя по выгрузке `этап-1 — принятие.7z`: «по нажатию
    „Сформировать“ ДЛЯ ВСЕХ ЭТАПОВ данные отправляются в ИИ и по ответу ты уже
    формируешь .png постановление… находясь на „Этап 2.2 — Об истребовании“
    при нажатии „Сформировать“ в чат с ИИ должна попасть информация из полей
    „Что стало известно“ + „Ответ ИИ“ от Этап 1, Этап 2.1, Этап 2.2».

    Отличия от `node_request` (кнопка этапа — решение ИИ по узлу):

    * на этапе 1 ответом был служебный блок, а не документ, поэтому «Вид
      документа» здесь называется явно — из «Вид решения:» ответа этапа 1;
    * в контекст идут ОТВЕТЫ ИИ, очищенные от мусора страницы чата
      (`ogp_stages.answer_for_context`): без цепочек и рекомендаций, которые
      заявка и так печатает отдельными строками;
    * ответ требуется ОДНИМ блоком постановления: служебные блоки ИИ уже
      присылал, повторять их не нужно (иначе они уезжают в «УСТАНОВИЛ»).

    Весь текст уходит вложением .txt, в поле запроса печатается короткая
    реплика (`ai_request.document_message`) — «прямой текст по минимуму».
    """
    parsed = ogp_stages.parse_node(kind)
    etap, step = parsed["etap"], parsed["step"]
    node = case.work_node(kind)
    user_input = str(node.get("input", "") or "").strip()
    stage_kind = _normalize_stage_kind("measure" if etap == 2 else
                                       ("intake" if etap == 1 else "close"))
    label = {1: "1", 2: f"2.{step}", 3: "3"}[etap]
    lines: list = [
        f"ЗАЯВКА НА ПОСТАНОВЛЕНИЕ ПО ОБРАЩЕНИЮ (этап {label}) — ДОКУМЕНТ",
    ]
    lines.extend(_number_lines(case, kind))
    if prosecutor:
        lines.append(f"Прокурор: {prosecutor}")
    lines.append("")
    lines.append(ogp_stages.context_block(case, kind))
    if etap == 1:
        doc_type = _clean(getattr(case, "intake_doc_type", "")) or \
            gdata.auto_type_for(gdata.GOV_STAGE_PRIMARY)
        lines.append(f"ЗАДАЧА: вынеси постановление этапа 1 — «{doc_type}».")
        lines.append("Это ПЕРВИЧНОЕ решение по обращению: только принятие к "
                     "производству, отказ в принятии, переадресация или "
                     "прекращение. Санкцию не назначай, уголовное дело не "
                     "возбуждай, штраф не выписывай.")
        if case.intake_basis:
            lines.append(f"ОСНОВАНИЕ ЭТАПА 1 (из проверки обращения): "
                         f"{case.intake_basis}")
    elif etap == 2:
        current = {}
        for item in case.work_plan or []:
            if int(item.get("n") or 0) == step:
                current = item
                break
        total = len(case.work_plan or [])
        lines.append(f"ЗАДАЧА: вынеси постановление по подэтапу 2.{step} из "
                     f"{total} — «{current.get('title', '')}».")
        if current.get("why"):
            lines.append(f"ЗАЧЕМ ЭТОТ ПОДЭТАП: {current.get('why')}")
        lines.append(f"ВАРИАНТ ЦЕПОЧКИ: {case.work_letter} — "
                     f"{case.active_chain().get('title', '')}")
        done = [f"2.{int(s.get('n') or 0)}) {s.get('title', '')}"
                for s in case.work_plan or [] if s.get("done")]
        if done:
            lines.append("УЖЕ ВЫНЕСЕНО ПО ДЕЛУ: " + "; ".join(done))
        rest = [f"2.{int(s.get('n') or 0)}) {s.get('title', '')}"
                for s in case.work_plan or [] if not s.get("done")]
        if rest:
            lines.append("ОСТАЛОСЬ ПО ПЛАНУ: " + "; ".join(rest))
        lines.append("Вынеси РОВНО этот документ и не подменяй его итоговым "
                     "решением по обращению. В резолюции однозначно скажи, "
                     "выполнен ли подэтап или нет: по этой фразе клиент "
                     "определит итог шага.")
    else:
        lines.append("ЗАДАЧА: прими ИТОГОВОЕ решение по обращению (этап 3).")
        lines.append("Вид завершения выбери сам по смыслу обращения, решения "
                     "этапа 1 и всех пройденных подэтапов этапа 2 и обоснуй "
                     "нормой.")
    if user_input:
        lines.append("")
        lines.append("ЧТО СТАЛО ИЗВЕСТНО (ввод прокурора, учти обязательно):")
        lines.append(user_input)
    lines.extend(_facts_lines(case, stage_meta(
        _normalize_stage_kind("measure" if etap == 2 else
                              ("intake" if etap == 1 else "close")))))
    lines.append("")
    lines.append("Ответь ОДНИМ блоком постановления: поля документа, "
                 "мотивировочная часть и резолюция с нумерованными пунктами. "
                 "Служебные блоки (проверка обращения, цепочки этапа 2, "
                 "рекомендации прокурору) уже получены ранее — в этом ответе "
                 "их повторять не нужно.")
    lines.append("")
    lines.append(doc_placeholder())
    return "\n".join(lines).rstrip() + "\n"


def doc_placeholder() -> str:
    """Напоминание о формате документа (маркеры заданы в промте).

    v66: в тексте заявки НЕ ДОЛЖНО быть строк-маркеров ответа. Правило v44 —
    «ни одной строки-маркера ответа в заявке, иначе клиент принимает свою
    заявку за готовый ответ ИИ»: прежняя формулировка называла «УСТАНОВИЛ» и
    «ПОСТАНОВИЛ», а по ним `postanovlenie_data.is_own_request` отличает ответ
    от заявки (пункт 2). Поэтому разделы документа описаны словами.
    """
    return ("Поля документа заполни по делу; мотивировочную часть начни с "
            "установленных по делу фактов, резолюцию — с нумерованных пунктов; "
            "ответ заверши служебным маркером окончания ответа из промта.")


def reorder_request(case: GovCase, prosecutor: str = "") -> str:
    """Заявка «согласовать новый порядок» цепочки этапа 2 (v62).

    Пользователь: «если я вношу ручные изменения в „Этап 2…", то ИИ
    калибрует дальнейший порядок и адаптирует его». Выполненные подэтапы не
    трогаются — ИИ пересобирает только оставшиеся и подстраивает этап 3.
    """
    lines = ["ЗАЯВКА НА КАЛИБРОВКУ ЦЕПОЧКИ ЭТАПА 2"]
    if prosecutor:
        lines.append(f"Прокурор: {prosecutor}")
    lines.append("")
    lines.append(ogp_stages.context_block(case, ""))
    lines.append("ПРОКУРОР ИЗМЕНИЛ ПОРЯДОК ЦЕПОЧКИ. Предложенный им порядок:")
    order = list(case.work_custom_order or [])
    if not order:
        order = [str(step.get("title", "")) for step in case.work_plan or []]
    for index, title in enumerate(order, start=1):
        lines.append(f"  {index}. {title}")
    lines.append("")
    lines.append("Выполненные подэтапы не меняй — они уже закрыты "
                 "постановлениями. Пересобери оставшийся порядок так, чтобы он "
                 "был законным и рабочим, и подстрой под него итог этапа 3. "
                 "Верни новый порядок отдельным блоком (маркеры заданы в "
                 "промте): название подэтапа и через тире — зачем он нужен.")
    return "\n".join(lines).rstrip() + "\n"


def stage_artifacts(root, case: GovCase, kind: str, stage_index: int,
                    doc, answer_text: str, step_number: int = 0) -> dict:
    """Складывает ответ ИИ этапа: текст, поля и PNG — в свою папку (v42).

    PNG печатается вызывающим кодом (здесь нет Pillow-рендера), но имена
    файлов и папка этапа определяются здесь — так их видят и форма, и main.
    """
    if not step_number and kind == STAGE_MEASURE:
        step_number = int(case.current_work_step().get("n") or 0)
    folder = stage_folder(root, doc.get("doc_number") or case.number,
                          stage_index, kind, case_number=case.number,
                          step_number=step_number,
                          doc_type=doc.get("doc_type"))
    written = {}
    stem = stage_stem(doc.get("doc_number") or case.number, stage_index, kind,
                      doc.get("doc_type"), step_number=step_number)
    raw = str(answer_text or "")
    if raw.strip():
        path = folder / "ИИ-ответ.txt"
        path.write_text(raw.replace("\r\n", "\n"), encoding="utf-8")
        written["answer"] = path
    path = folder / "ИИ-поля.json"
    path.write_text(json.dumps(doc.to_dict(), ensure_ascii=False, indent=2),
                    encoding="utf-8")
    written["fields"] = path
    path = folder / "ИИ-документ.txt"
    path.write_text(doc.to_document_txt(), encoding="utf-8")
    written["document"] = path
    path = folder / "Этап.txt"
    path.write_text(
        f"Этап: {stage_index}. {stage_title(kind)}\n"
        f"Обращение №: {case.number}\n"
        f"Состояние: {case.status_label()}\n"
        f"Документ: {stem}\n",
        encoding="utf-8")
    written["stage"] = path
    written["stem"] = stem
    written["folder"] = folder
    return written


# ------------------------------------------------------------------ v43.2 ---
#  Формирование документа по делу со статусом «Отказано».
#
#  Замечание: «если обращение имеет статус "Отказано", сформировать .png
#  постановление всё равно нужно». До этой правки форма открывала такое дело
#  сразу на этапе 2 («санкция»), а `_form` требовал «Сначала примите обращение
#  (этап 1)» и «Обстоятельства» — то есть отказ в принятии вообще нельзя было
#  оформить постановлением. Документ об отказе — это ПЕРВИЧНОЕ решение
#  (этап 1), его и должно печатать PNG.

#: Этап документа (вкладка «Постановление») ↔ вид этапа дела (v49).
#: Два значения `gdata.GOV_STAGE_*` — это «Вид решения», а не «этап»; этап
#: дела один и тот же, поэтому связь задаётся явно, а не «угадывается» по
#: названию документа.
GOV_STAGE_TO_KIND: dict = {
    gdata.GOV_STAGE_PRIMARY: STAGE_INTAKE,
    gdata.GOV_STAGE_FINAL: STAGE_MEASURE,
}

#: Обратное соответствие: вид этапа дела → этап документа.
KIND_TO_GOV_STAGE: dict = {
    STAGE_INTAKE: gdata.GOV_STAGE_PRIMARY,
    STAGE_REDIRECT: gdata.GOV_STAGE_PRIMARY,
    STAGE_MEASURE: gdata.GOV_STAGE_FINAL,
    STAGE_ADDITIONAL: gdata.GOV_STAGE_FINAL,
    STAGE_RECHECK: gdata.GOV_STAGE_FINAL,
    STAGE_APPEAL: gdata.GOV_STAGE_FINAL,
    STAGE_CLOSE: gdata.GOV_STAGE_FINAL,
}


def stage_kind_for_gov_stage(stage: str) -> str:
    """Вид этапа дела по этапу документа («1. Первичное решение» → принятие)."""
    return GOV_STAGE_TO_KIND.get(str(stage or ""), STAGE_INTAKE)


def gov_stage_for_kind(kind: str) -> str:
    """Этап документа по виду этапа дела (v49).

    v64: принимает и узел цепочки («2.1») — иначе подэтап этапа 2 получал
    `doc_stage = primary` (этап 1) и неверный «Вид документа» по умолчанию.
    """
    return KIND_TO_GOV_STAGE.get(_normalize_stage_kind(kind),
                                 gdata.GOV_STAGE_PRIMARY)


def stage_kind_for_node(node: str) -> str:
    """Вид этапа дела по узлу цепочки: «1» / «2.N» / «3» (v62.3).

    В v62 вкладки «Постановление» с комбобоксом этапа нет — этап берётся из
    узла, который прокурор ведёт в «Цепочке этапов». Узел этапа 1 — первичное
    решение по обращению, любой подэтап 2.N — санкция по существу, узел 3 —
    итог по обращению.
    """
    key = str(node or "").strip()
    if key.startswith("эт."):
        key = key[3:]
    if not key:
        return STAGE_INTAKE
    if key == "1":
        return STAGE_INTAKE
    if key == "3":
        return STAGE_CLOSE
    if key.startswith("2"):
        return STAGE_MEASURE
    return STAGE_INTAKE


def primary_stage_kind(case: "GovCase") -> str:
    """Этап для печати документа об отказе: всегда этап первичного решения."""
    if case is not None and case.refused() and not case.accepted():
        return STAGE_INTAKE
    return case.open_stage_kind() if case is not None else STAGE_INTAKE


def stage_block_reason(case: "GovCase", kind: str) -> str:
    """Почему этот этап нельзя печатать по состоянию дела (пусто — можно).

    Единственный запрет — санкция без принятого обращения. Для отказа в
    принятии этап 1 разрешён: именно им и оформляется отказ.
    """
    if kind == STAGE_MEASURE and not case.can_measure():
        return "Санкцию можно оформить только после принятия обращения (этап 1)"
    return ""


def stage_is_ready(case: "GovCase", kind: str, reason: str) -> bool:
    """Доступен ли этап в списке (v43.2: отказ не блокирует этап 1)."""
    if reason:
        return False
    return bool(stage_meta(kind)) and (
        kind != STAGE_MEASURE or case.can_measure())
