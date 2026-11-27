"""pending_send.py — отложенная отправка заявки через перезапуск (v43.2).

Замечание пользователя: «нужно, чтобы при нажатии кнопок "Сформировать" и
"Проверка ИИ" программа перезапускалась (это сделано, чтобы очистить чат с ИИ,
все записи и ответы и т.д.) и после перезагрузки программы уже отправлять ИИ».

Отсюда два правила, которые держит этот модуль:

1. Кнопки НЕ отправляют заявку сразу. Они сохраняют её на диск и просят
   перезапуск клиента.
2. После перезапуска клиент поднимает чат заново (чистый чат), и уже потом
   отправляет сохранённую заявку — ровно один раз.

Что важно:

* заявка не отправляется без файлов в чате: если промт и «Шаблон.txt» ещё не
  прикрепились (поток выгрузки идёт), отправка ждёт события о вложениях;
* повторный запуск не отправляет заявку дважды: отправленная запись переходит в
  состояние `sent` и `is_fresh()` её больше не пропускает;
* истёкшая заявка (клиент был закрыт надолго) не отправляется: иначе в чат
  уходил бы старый запрос после случайного запуска;
* v65 (решение пользователя `keep_draft`): если ИИ не ответил — страница не
  поднялась, кнопка отправки не активна, ответ не пришёл за отведённое время, —
  заявка остаётся на диске (состояния `sent` и `failed`), и прокурор повторяет
  её одной кнопкой «Повторить отправку» в форме. Убирает запись только
  разобранный ответ ИИ (`main._pending_answer_received`) или отмена.

Модуль намеренно не зависит от Qt и браузера: всё проверяется тестами.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

#: Отправка ждёт, пока клиент поднимется и прикрепит файлы.
STATE_WAITING = "waiting"
#: Заявка ушла в чат.
STATE_SENT = "sent"
#: Заявка устарела и не отправлена.
STATE_EXPIRED = "expired"
#: Заявка отменена пользователем.
STATE_CANCELLED = "cancelled"
#: v65: заявка НЕ ушла в чат (страница не поднялась, отправка заблокирована).
#: Запись остаётся на диске — её повторяют кнопкой «Повторить отправку».
STATE_FAILED = "failed"

#: Свежесть заявки: дольше этого срока после перезапуска она не отправляется.
DEFAULT_MAX_AGE = 10 * 60


def _clean(value) -> str:
    return " ".join(str(value or "").split())


def record_path(root) -> Path:
    """Файл отложенной отправки внутри папки постановлений."""
    return Path(root) / "pending_send.json"


def build_record(*, kind: str, request_text: str, source_path: str = "",
                 case_number: str = "", stage_kind: str = "",
                 stage_index: int = 1, doc_type: str = "",
                 document_kind: str = "gov", title: str = "",
                 appeal_text: str = "", message_text: str = "",
                 appeal_path: str = "", node_kind: str = "") -> dict:
    """Что именно отправить после перезапуска (v43.2 / v50).

    `kind` — «document» (Сформировать) или «intake» (Проверка ИИ).
    Текст заявки сохраняется целиком: после перезапуска формы уже нет, а
    отправить в чат нужно ровно то, что пользователь сформировал.

    v50 (замечание пользователя): «текст обращения экспортируй в файл
    „Обращение.txt", минимум текста в поле запроса». Поэтому у записи два
    разных текста:

    * `request_text` — полная заявка, она уходит ВЛОЖЕНИЕМ (`source_path`);
    * `message_text` — короткое сообщение, которое печатается в поле запроса;
    * `appeal_path` — файл «Обращение.txt», второе вложение.
    """
    return {
        "kind": str(kind or "document"),
        "document_kind": str(document_kind or "gov"),
        "title": _clean(title),
        "request_text": str(request_text or ""),
        #: v50: что именно напечатать в поле запроса. Пусто — печатаем заявку
        #: целиком (поведение до v50, совместимость со старыми записями).
        "message_text": str(message_text or ""),
        #: v50: файл «Обращение.txt» — второе вложение рядом с заявкой.
        "appeal_path": str(appeal_path or ""),
        #: Текст обращения БЕЗ заявки (v44): по нему находится дело. Раньше в
        #: дело попадал весь текст заявки — «ЗАЯВКА НА ПРОВЕРКУ …» вместо
        #: обращения гражданина.
        "appeal_text": str(appeal_text or ""),
        "source_path": str(source_path or ""),
        "case_number": str(case_number or ""),
        "stage_kind": str(stage_kind or ""),
        #: v64: узел цепочки («1», «2.1», «3») — ОТДЕЛЬНО от вида этапа.
        #: Раньше форма клала узел в `stage_kind`, и `stage_number("2.1")`
        #: давал 3: документ подэтапа уезжал в папку «этап-3 — санкция».
        #: Старые записи этого поля не имеют — тогда узел ищется в
        #: `stage_kind` (см. `main._load_pending_fields`).
        "node_kind": str(node_kind or ""),
        "stage_index": int(stage_index or 1),
        "doc_type": _clean(doc_type),
        "created_at": time.time(),
        "state": STATE_WAITING,
        "sent_at": 0.0,
        "restarts": 0,
    }


def save(root, record: dict) -> Path:
    """Сохраняет заявку до перезапуска."""
    path = record_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = dict(record or {})
    data.setdefault("created_at", time.time())
    data.setdefault("state", STATE_WAITING)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


def load(root) -> dict:
    """Читает заявку; повреждённый файл считается отсутствующим."""
    path = record_path(root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def clear(root) -> bool:
    """Убирает заявку (после отправки или отмены)."""
    path = record_path(root)
    try:
        path.unlink()
        return True
    except Exception:
        return False


def is_fresh(record: dict, max_age: int = DEFAULT_MAX_AGE,
             now: float | None = None) -> bool:
    """Заявка ещё актуальна? (не отправлялась и не старше max_age)."""
    if not record or not _clean(record.get("request_text")):
        return False
    # v65: отправленная и «не ушедшая» заявки автоматически не повторяются —
    # только вручную, кнопкой «Повторить отправку» (`can_resend` + `rearm`).
    if record.get("state") in (STATE_SENT, STATE_FAILED):
        return False
    created = float(record.get("created_at") or 0.0)
    if not created:
        return False
    age = (now if now is not None else time.time()) - created
    return age <= float(max_age)


def describe(record: dict) -> str:
    """Короткая подпись для статуса окна и журнала."""
    if not record:
        return ""
    kind = record.get("kind")
    if kind == "intake":
        return "Проверка обращения"
    number = _clean(record.get("case_number"))
    stage = int(record.get("stage_index") or 1)
    doc_type = _clean(record.get("doc_type"))
    parts = [f"Обращение №{number}" if number else "Постановление"]
    parts.append(f"этап {stage}")
    if doc_type:
        parts.append(doc_type)
    return " · ".join(parts)


def mark_sent(root, record: dict) -> dict:
    """Фиксирует отправку: запись остаётся на диске в состоянии `sent`.

    v65 (решение пользователя `keep_draft`): раньше запись удалялась, и если
    ИИ не отвечал, прокурору приходилось заново собирать документ и заполнять
    поля. Теперь заявка хранится до разбора ответа, а повторно её можно
    отправить одной кнопкой. Автоматически она не уходит: `is_fresh()` для
    состояния `sent` возвращает False.
    """
    data = dict(record or {})
    data["state"] = STATE_SENT
    data["sent_at"] = time.time()
    save(root, data)
    return data


def mark_failed(root, record: dict, reason: str = "") -> dict:
    """Заявка не ушла в чат — запись остаётся, чтобы повторить её вручную (v65)."""
    data = dict(record or {})
    data["state"] = STATE_FAILED
    data["failed_at"] = time.time()
    data["fail_reason"] = _clean(reason)
    save(root, data)
    return data


#: Состояния, из которых заявку можно отправить повторно вручную.
RESENDABLE = (STATE_WAITING, STATE_SENT, STATE_EXPIRED, STATE_FAILED)


def can_resend(record: dict) -> bool:
    """Есть ли сохранённая заявка, которую можно отправить ещё раз (v65).

    Срок свежести здесь не указ: повтор делает человек, а не автозапуск.
    """
    if not record or not _clean(record.get("request_text")):
        return False
    return str(record.get("state") or STATE_WAITING) in RESENDABLE


def rearm(record: dict) -> dict:
    """Та же заявка — снова готова к отправке (счётчик повторов растёт)."""
    data = dict(record or {})
    data["state"] = STATE_WAITING
    data["created_at"] = time.time()
    data["restarts"] = int(data.get("restarts") or 0) + 1
    data.pop("sent_at", None)
    data.pop("failed_at", None)
    data.pop("fail_reason", None)
    return data


def mark_cancelled(root, record: dict) -> dict:
    data = dict(record or {})
    data["state"] = STATE_CANCELLED
    clear(root)
    return data
