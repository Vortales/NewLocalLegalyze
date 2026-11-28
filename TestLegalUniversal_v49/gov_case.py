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


def stage_meta(kind: str) -> dict:
    """Описание этапа (для незнакомого вида — описание санкции)."""
    return STAGE_META.get(kind, STAGE_META[STAGE_MEASURE])


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
               doc_type: str = "") -> str:
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
    stem = f"{safe}_Постановление_Этап_{int(stage_index)}_{stage_short(kind)}"
    slug = doc_type_slug(doc_type)
    if slug:
        stem = f"{stem}_Вид_{slug}"
    return stem


def stage_index_for(plan, kind: str) -> int:
    """Номер этапа для имени файла: место этапа в цепочке дела (v43).

    Замечание: «если обращение принято и я нажал Проверку ИИ, а затем
    Сформировать, этап по умолчанию сразу менялся с 1 на 2». Номер этапа не
    должен зависеть от того, сколько этапов уже выполнено: у каждого вида
    этапа своё постоянное место в цепочке. Поэтому «принятие» всегда этап 1,
    «санкция» — этап 2, а дополнительный этап получает следующий свободный
    номер.
    """
    order = []
    for item in plan or ():
        name = item.get("kind") if isinstance(item, dict) else item
        if name and name not in order:
            order.append(name)
    if kind in order:
        return order.index(kind) + 1
    return len(order) + 1


def stage_folder(root, number: str, stage_index: int, kind: str) -> Path:
    """Папка этапа внутри папки документа: `postanovleniya/<номер>/этап-N …`."""
    folder = base.doc_folder(Path(root), number)
    target = folder / f"этап-{int(stage_index)} — {stage_short(kind)}"
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

#: Что означает состояние обращения для «Обращения №» в программе.
INTAKE_STATUS_MAP: dict = {
    INTAKE_ACCEPTED: STATUS_IN_PROGRESS,
    INTAKE_REFUSED: STATUS_REFUSED,
    INTAKE_REDIRECTED: STATUS_CLOSED,
    INTAKE_TERMINATED: STATUS_CLOSED,
}


def parse_intake_answer(text: str) -> dict:
    """Результат проверки ИИ + состояние дела (обёртка над данными)."""
    result = gdata.parse_intake_answer(text)
    result["status"] = INTAKE_STATUS_MAP.get(result.get("state", ""), "")
    result["ok"] = bool(result.get("state") and result["status"])
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
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    source: str = "form"

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
        """«Обстоятельства» прокурора одним текстом для бланка.

        Собираются из четырёх частей, которые просил пользователь:
        видеофиксация, доводы за и против, предложенное наказание и решение
        прокурора. Пустые части не печатаются, служебных подписей в бланке
        нет — только текст.
        """
        parts = []
        if _clean(self.facts_video):
            parts.append(_sentence(_clean(self.facts_video)))
        arguments = []
        if _clean(self.facts_for):
            arguments.append("В пользу обращения: " + _sentence(_clean(self.facts_for)))
        if _clean(self.facts_against):
            arguments.append("Против обращения: " + _sentence(_clean(self.facts_against)))
        if arguments:
            parts.append(" ".join(arguments))
        if _clean(self.proposal):
            parts.append("Прокурор полагает возможным: "
                         + _sentence(_clean(self.proposal)))
        if _clean(self.own_decision):
            parts.append("Решение прокурора: " + _sentence(_clean(self.own_decision)))
        return "\n\n".join(parts)

    def has_facts(self) -> bool:
        return bool(self.prosecutor_facts().strip())

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
            self.mark_stage(STAGE_INTAKE, accepted=True)
            self.touch()
            return
        status = result.get("status") or INTAKE_STATUS_MAP.get(state, "")
        if not status:
            return
        self.status = status
        self.intake_basis = _clean(result.get("basis", ""))
        self.intake_answer = str(result.get("raw", "") or "")
        # v49: реквизиты обращения — из ключевых слов ответа ИИ, а не из
        # локального разбора текста обращения.
        self.apply_keywords(result.get("keywords") or {})
        if status == STATUS_IN_PROGRESS:
            self.mark_stage(STAGE_INTAKE, accepted=True)
        else:
            self.mark_stage(STAGE_INTAKE, accepted=False)
        self.touch()

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
    if len(done) >= total:
        return f"этапов {len(done)} из {total} — дело завершено"
    return (f"этапов {len(done)} из {total}, следующий — "
            f"{stage_title(case.open_stage_kind()).split('.')[0].strip()}")


def case_row(case: "GovCase") -> dict:
    """Одна строка стартового окна «Обращения» (v43).

    Возвращает данные, а не виджеты: список обращений проверяется тестами, а
    форма только показывает готовые поля.
    """
    applicant = parse_applicant(case.appeal_raw)
    number = f"№{case.number or '—'}"
    forum = f" (с форума {case.appeal_number})" if case.appeal_number else ""
    title = f"{number}{forum} · {applicant or 'заявитель не распознан'}"
    return {
        "number": case.number,
        "title": title,
        "status": case.status_label(),
        "status_key": case.status,
        "color": case.status_color(),
        "progress": stage_progress(case),
        "updated": case.human_updated(),
        "appeal_raw": case.appeal_raw,
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

    def save(self, case: GovCase) -> Path:
        if not case.number:
            case.number = self.next_number()
        case.touch()
        path = self.path(case.number)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(case.to_dict(), ensure_ascii=False, indent=2),
                        encoding="utf-8")
        return path

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
        self.save(case)
        return case


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
        "Ответь строго служебным блоком, без вступлений и без описания "
        "вложений. Начни ответ маркером проверки обращения (он задан в "
        "промте), затем строки «Состояние:», «Основание:», «Вид решения:», "
        "«Что проверить:», затем маркер окончания проверки и маркер "
        "окончания ответа.",
        f"В служебной строке состояния укажи ровно одно слово-решение: "
        f"{INTAKE_ACCEPTED}, {INTAKE_REFUSED}, {INTAKE_REDIRECTED} или "
        f"{INTAKE_TERMINATED}.",
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
    ]
    if prosecutor:
        lines.insert(2, f"Прокурор: {prosecutor}")
        lines.insert(3, "")
    if case.appeal_number:
        lines.insert(2, f"Номер обращения: {case.appeal_number}")
    lines.append("")
    lines.append("ОБРАЩЕНИЕ ГРАЖДАНИНА (текст целиком, форма ОГП):")
    lines.append(str(case.appeal_raw or "").strip())
    return "\n".join(lines).rstrip() + "\n"


def stage_request(case: GovCase, kind: str, doc) -> str:
    """Заявка для ИИ на конкретный этап (в дополнение к `to_chat_txt`)."""
    meta = stage_meta(kind)
    lines = [
        f"ЭТАП: {meta['title']}",
        meta["guide"],
        "",
    ]
    facts = case.prosecutor_facts()
    if meta["needs_facts"] and facts:
        lines.append(f"ОБСТОЯТЕЛЬСТВА ПРОКУРОРА ({meta['needs_facts_label']}):")
        lines.append(facts)
        lines.append("")
    if case.intake_basis:
        lines.append("Основание первичного решения по обращению: "
                     + case.intake_basis)
        lines.append("")
    lines.append(doc.to_chat_txt())
    return "\n".join(lines)


def stage_artifacts(root, case: GovCase, kind: str, stage_index: int,
                    doc, answer_text: str) -> dict:
    """Складывает ответ ИИ этапа: текст, поля и PNG — в свою папку (v42).

    PNG печатается вызывающим кодом (здесь нет Pillow-рендера), но имена
    файлов и папка этапа определяются здесь — так их видят и форма, и main.
    """
    folder = stage_folder(root, doc.get("doc_number") or case.number,
                          stage_index, kind)
    written = {}
    stem = stage_stem(doc.get("doc_number") or case.number, stage_index, kind,
                      doc.get("doc_type"))
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
    """Этап документа по виду этапа дела (v49)."""
    return KIND_TO_GOV_STAGE.get(str(kind or ""), gdata.GOV_STAGE_PRIMARY)


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


def refusal_intake_request(case: "GovCase", doc) -> str:
    """Заявка на постановление об ОТКАЗЕ в принятии обращения (этап 1).

    Отдельный текст нужен потому, что по такому делу нет ни «Обстоятельств»,
    ни санкции: ИИ оформляет уже принятое решение об отказе, с основанием из
    части 1 статьи 16 Регламента, и не должен «придумывать» нарушение.
    """
    lines = [
        "ЭТАП: первичное решение по обращению (документ об отказе).",
        "Обращение уже рассмотрено: в принятии обращения отказано. Оформи это "
        "решение постановлением Прокурора — вид решения «Об отказе в принятии "
        "обращения к производству» с номером конкретного основания из части 1 "
        "статьи 16 Регламента подачи обращений.",
        "НЕ назначай санкцию, не возбуждай уголовное дело и не выписывай "
        "штраф: отказ в принятии — это первичное решение, а не наказание. "
        "Фразу «Указанные обстоятельства образуют нарушение» не пиши.",
        "",
    ]
    if _clean(case.intake_basis):
        lines.append("ОСНОВАНИЕ ОТКАЗА (по проверке обращения): "
                     + _clean(case.intake_basis))
        lines.append("")
    if _clean(case.intake_answer):
        lines.append("ОТВЕТ ИИ ПО ПРОВЕРКЕ ОБРАЩЕНИЯ (для сверки, дословно):")
        lines.append(_clean(case.intake_answer)[:4000])
        lines.append("")
    lines.append(doc.to_chat_txt())
    return "\n".join(lines)
