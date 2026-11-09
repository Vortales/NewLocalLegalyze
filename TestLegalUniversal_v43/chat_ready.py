"""chat_ready.py — «файлы в чате точно готовы?» перед отправкой запроса (v43.4).

Замечание пользователя (сессия `logs_43v`): «В чат с ИИ не успевают
экспортироваться файлы, ответ даёт ИИ без файлов… файлы, когда экспортируются,
они могут ещё загружаться и только спустя 1-2 секунды уже готовы к отправке,
если раньше отправить запрос, то файлы не прикрепятся и ответ ИИ будет без
файлов (.pdf и .txt)».

По логу это видно построчно:

    11:06:51.125  pending_send.sent            (запрос уже ушёл в чат)
    11:06:51.157  upload.state  present=0       (в чате НОЛЬ файлов)
    11:06:51.561  upload.attempt pdf   Memphis.pdf
    11:06:51.905  upload.attempt template Шаблон.txt
    11:06:52.511  upload.attachments_confirmed complete=true

Запрос уходил на 1.4 секунды раньше, чем файлы вообще начали прикрепляться.
Причина — в проверке готовности: поток вложений считал «всё готово», пока
в чате не было ни одного файла (`present=0`, `files_loaded=False`), а
отложенная отправка смотрела только на флаг `pdf_attached`.

Этот модуль держит одно правило, проверяемое тестами:

    запрос уходит, только когда в чате ПОДТВЕРЖДЁНЫ все нужные файлы,
    загрузка не идёт (uploading=False), файлы не появились только что
    (нужна выдержка), и количество файлов не превышает ожидаемое —
    иначе рядом висит «остаток» от прошлой загрузки.

Модуль не зависит от Qt: состояние ему подаётся словарём.
"""

from __future__ import annotations

import time
from pathlib import Path

#: Сколько ждать после подтверждения файлов, прежде чем отправлять запрос.
#: По логу файлам нужно 1–2 секунды, чтобы «устояться» в поле ввода: если
#: отправить сразу, часть вложений ещё не прикрепилась.
SETTLE_SECONDS = 2.2

#: Сколько ждать подтверждения файлов, прежде чем считать это сбоем.
READY_TIMEOUT = 150.0

#: Почему отправлять нельзя (для журнала и статуса окна).
REASON_NO_PAGE = "no-page"
REASON_NO_FILES = "no-files"
REASON_UPLOADING = "uploading"
REASON_INCOMPLETE = "incomplete"
REASON_EXTRA = "extra-files"
REASON_SETTLING = "settling"
REASON_TIMEOUT = "timeout"
REASON_STALE = "stale-state"


def file_name(path) -> str:
    """Имя файла без папки (пустая строка, если пути нет)."""
    if not path:
        return ""
    try:
        return Path(str(path)).name
    except Exception:
        return ""


def required_names(pdf_path=None, template_path=None, document_path=None,
                   template_name: str = "") -> list:
    """Имена файлов, которые обязательно должны быть в чате.

    Заявка на постановление (`document_path`) — тоже обязательное вложение:
    ИИ собирает документ именно из неё, а не из текста сообщения.
    """
    names = []
    for value in (pdf_path, template_path, document_path):
        name = file_name(value)
        if name and name not in names:
            names.append(name)
    if template_name and template_name not in names:
        names.append(template_name)
    return names


def normalize(state: dict) -> dict:
    """Приводит состояние из страницы к предсказуемому виду."""
    state = state or {}
    try:
        present = int(state.get("present", 0) or 0)
    except Exception:
        present = 0
    names = [str(name or "") for name in (state.get("names") or [])]
    return {
        "present": max(0, present),
        "uploading": bool(state.get("uploading", False)),
        "names": names,
        "probe_ok": bool(state.get("probe_ok", True)),
    }


def check(state: dict, required: list, confirmed_at: float | None = None,
          now: float | None = None, settle: float = SETTLE_SECONDS) -> dict:
    """Можно ли отправлять запрос в чат прямо сейчас.

    Возвращает `{"ready": bool, "reason": str, "names": [...], "present": int}`.
    `confirmed_at` — момент, когда пришло подтверждение вложений от потока
    загрузки; пока с него не прошло `settle` секунд, отправка ждёт.
    """
    now = time.monotonic() if now is None else float(now)
    info = normalize(state)
    required = [name for name in (required or []) if name]
    result = {"ready": False, "reason": "", "names": info["names"],
              "present": info["present"]}

    if info["uploading"]:
        result["reason"] = REASON_UPLOADING
        return result
    if confirmed_at is None:
        result["reason"] = REASON_STALE
        return result
    if not required:
        result["reason"] = REASON_NO_FILES
        return result
    if info["names"]:
        flat = [name.strip().lower() for name in info["names"] if name.strip()]
        missing = [name for name in required if name.strip().lower() not in flat]
    else:
        missing = list(required)
    if info["present"] < len(required) or missing:
        result["reason"] = REASON_INCOMPLETE
        result["missing"] = missing[:4]
        return result
    # Лишние файлы: обычно это «остаток» прошлой загрузки. С ним ИИ получает
    # чужие вложения, поэтому сначала чистим чат, а не отправляем.
    if info["present"] > len(required) + 1:
        result["reason"] = REASON_EXTRA
        return result
    if now - float(confirmed_at) < float(settle):
        result["reason"] = REASON_SETTLING
        return result
    result["ready"] = True
    return result


def wait_message(reason: str, waited: float = 0.0) -> str:
    """Человеческая подпись состояния ожидания (статус окна и журнал)."""
    waited = max(0.0, float(waited or 0.0))
    table = {
        REASON_NO_PAGE: "Ждём страницу чата…",
        REASON_NO_FILES: "Ждём файлы заявки…",
        REASON_UPLOADING: "Файлы ещё загружаются — ждём окончания загрузки…",
        REASON_INCOMPLETE: "Прикрепляются файлы — ждём полный набор…",
        REASON_EXTRA: "В чате лишние файлы — очищаем перед отправкой…",
        REASON_SETTLING: "Файлы на месте, проверяем готовность…",
        REASON_TIMEOUT: "Файлы не подтвердились — отправка отменена",
        REASON_STALE: "Ждём подтверждение вложений…",
    }
    text = table.get(reason, "Ждём готовности чата…")
    if waited >= 10 and reason in (REASON_UPLOADING, REASON_INCOMPLETE,
                                   REASON_SETTLING, REASON_STALE):
        text = f"{text} ({int(waited)} с)"
    return text or "Ждём готовности чата…"


def expired(waited: float, timeout: float = READY_TIMEOUT) -> bool:
    """Ожидание файлов вышло за отведённое время."""
    return max(0.0, float(waited or 0.0)) > float(timeout)
