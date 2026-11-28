"""upload_gate.py — «можно ли отправлять запрос в чат ИИ» (v58).

Замечание пользователя по четырём дампам страницы ИИ
(`Загрузка.txt`, `После загрузки.txt`, `Ошибка загрузки.txt`, `Удалить.txt`):

    «Если при экспорте файлов в чат с ИИ один из них ещё не загрузился, то есть
    кнопка отправки недоступна, то ожидать кнопку отправки и только тогда,
    когда она сменит состояние на активное, то отправлять запрос… Если имеется
    хоть одна ошибка в экспортируемом файле, то удалить файл и заново его
    загрузить (логика автодогрузки при отсутствующих файлах уже имеется)».

Что показало сравнение дампов:

| Состояние | хост чипа | значок/текст | кнопка «Отправить» |
|---|---|---|---|
| идёт загрузка | `gcB4oc oacyne` | — | `IbS5tc wdK4Nc`, `tabindex="-1"` |
| готово | `gcB4oc` | — | без этих классов, `tabindex="0"` |
| ошибка | `gcB4oc bGkj2b` | `div.rUmyzd`, `div.oIIrVc` + «При загрузке произошла ошибка» | `IbS5tc wdK4Nc`, `tabindex="-1"` |
| наведение на удаление | `gcB4oc XYXmld` | — | — |

Крестик `div.zXgSbc` и `data-progressvalue="0"` есть во ВСЕХ состояниях —
поэтому старый признак «есть крестик → файл готов» мог подтвердить файл,
который вообще не загрузился.

Правила этого модуля (проверяются тестами):

1. ошибка важнее всего: пока в чате есть чип с ошибкой — отправлять нельзя;
2. ошибку считаем настоящей, только если она видна `ERROR_CONFIRM` раз подряд
   (защита от мигания индикатора);
3. иначе ждём активную кнопку «Отправить» не дольше `SEND_BUTTON_TIMEOUT`;
4. после `CHIP_ERROR_RETRIES` неудачных попыток на файл — перезагрузка браузера;
5. пустой чат (нет текста в поле ввода) — не повод ни ждать, ни перезагружать:
   ориентир только вложения;
6. запрос без вложений не отправляется никогда.

Модуль не зависит от Qt и CDP: состояние ему подаётся словарями.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
#  Константы времени и порогов
# --------------------------------------------------------------------------- #
#: Сколько максимум ждём активную кнопку «Отправить» (решение пользователя).
SEND_BUTTON_TIMEOUT = 20.0
#: Сколько ждём после активации кнопки, чтобы чипы «устоялись».
SETTLE_SECONDS = 0.6
#: Сколько раз подряд должна подтвердиться ошибка загрузки (антидребезг).
ERROR_CONFIRM = 2
#: Опрос состояния страницы, секунды.
POLL_SECONDS = 0.25
#: Сколько раз пробуем «удалить сломанный чип → прикрепить заново».
CHIP_ERROR_RETRIES = 3
#: Сколько раз перезагружаем чат, если кнопка так и не стала активной.
RELOAD_RETRIES = 2

#: Причины отказа отправки (для журнала и статуса окна).
REASON_NO_PAGE = "no-page"
REASON_NO_FILES = "no-files"
REASON_UPLOADING = "uploading"
REASON_ERROR = "upload-error"
REASON_INCOMPLETE = "incomplete"
REASON_BUTTON = "send-disabled"
REASON_SETTLING = "settling"
REASON_RELOAD = "reloading"


def file_name(path) -> str:
    """Имя файла без папки (пустая строка, если пути нет)."""
    from pathlib import Path

    if not path:
        return ""
    try:
        return Path(str(path)).name
    except Exception:
        return ""


def required_names(pdf_path=None, template_path=None, document_path=None,
                   template_name: str = "", appeal_path=None) -> list:
    """Имена файлов, которые обязаны быть в чате (v58: как в chat_ready)."""
    names = []
    for value in (pdf_path, template_path, document_path, appeal_path):
        name = file_name(value)
        if name and name not in names:
            names.append(name)
    if template_name and template_name not in names:
        names.append(template_name)
    return names


def normalize_chips(raw) -> list:
    """Приводит список чипов со страницы к `[{name, uploading, error}]`.

    Страница может отдать мусор (None, строки, лишние поля) — здесь всё
    приводится к предсказуемому виду, дубли имён склеиваются: на три файла
    Google отдаёт шесть вложенных узлов (v43.5).
    """
    chips: list = []
    seen: dict = {}
    for item in (raw or []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        uploading = bool(item.get("uploading"))
        error = bool(item.get("error"))
        key = name.lower()
        if key and key in seen:
            chip = seen[key]
            chip["uploading"] = chip["uploading"] or uploading
            chip["error"] = chip["error"] or error
            continue
        chip = {"name": name, "uploading": uploading, "error": error}
        chips.append(chip)
        if key:
            seen[key] = chip
    return chips


def normalize_send(raw) -> dict:
    """Состояние кнопки «Отправить» → `{found, enabled, reason}`."""
    raw = raw if isinstance(raw, dict) else {}
    found = bool(raw.get("found"))
    if not found:
        return {"found": False, "enabled": False, "reason": "not-found"}
    disabled_attr = bool(raw.get("disabled"))
    aria = str(raw.get("aria_disabled") or "").lower()
    classes = str(raw.get("classes") or "")
    tabindex = str(raw.get("tabindex") or "")
    blocked = ("IbS5tc" in classes or "wdK4Nc" in classes
               or tabindex.strip() == "-1")
    enabled = not (disabled_attr or aria == "true" or blocked)
    reason = "" if enabled else "disabled"
    return {"found": True, "enabled": enabled, "reason": reason}


def error_chips(chips: list) -> list:
    """Чипы с ошибкой загрузки."""
    return [chip for chip in (chips or []) if chip.get("error")]


def uploading_chips(chips: list) -> list:
    """Чипы, которые ещё грузятся."""
    return [chip for chip in (chips or []) if chip.get("uploading")]


def ready_chips(chips: list) -> list:
    """Чипы, которые загрузились (ни ошибки, ни загрузки)."""
    return [chip for chip in (chips or [])
            if not chip.get("error") and not chip.get("uploading")]


def missing_chips(chips: list, required: list) -> list:
    """Каких обязательных файлов нет среди загруженных."""
    have = {str(chip.get("name") or "").strip().lower()
            for chip in ready_chips(chips)}
    return [name for name in (required or [])
            if name and name.strip().lower() not in have]


def should_send(chips: list, send: dict, required: list,
                error_streak: int = 0, confirmed_at: float | None = None,
                now: float | None = None, settle: float = SETTLE_SECONDS) -> dict:
    """Можно ли прямо сейчас отправить запрос в чат.

    Порядок проверок — как решил пользователь:

    1. в чате есть чип с ошибкой (подтверждённой `error_streak` раз) — нельзя;
    2. идёт загрузка файла — нельзя, ждём;
    3. кнопка «Отправить» неактивна — нельзя, ждём (до 20 с);
    4. не все обязательные файлы загрузились — нельзя, ждём;
    5. после подтверждения не прошла выдержка — нельзя, ждём.
    """
    import time

    now = time.monotonic() if now is None else float(now)
    chips = normalize_chips(chips)
    send = normalize_send(send)
    required = [name for name in (required or []) if name]
    result = {"ready": False, "reason": "", "chips": chips,
              "names": [chip["name"] for chip in ready_chips(chips)]}

    errors = error_chips(chips)
    if errors and int(error_streak) >= int(ERROR_CONFIRM):
        result["reason"] = REASON_ERROR
        result["errors"] = [chip["name"] for chip in errors][:4]
        return result
    if not required:
        result["reason"] = REASON_NO_FILES
        return result
    if uploading_chips(chips):
        result["reason"] = REASON_UPLOADING
        return result
    if not send["enabled"]:
        # Кнопка закрыта и тогда, когда файлы ещё не прикрепились: это и есть
        # главный признак «экспорт не завершён» (v58).
        result["reason"] = REASON_BUTTON if send["found"] else REASON_NO_PAGE
        return result
    missing = missing_chips(chips, required)
    if missing:
        result["reason"] = REASON_INCOMPLETE
        result["missing"] = missing[:4]
        return result
    if confirmed_at is not None and now - float(confirmed_at) < float(settle):
        result["reason"] = REASON_SETTLING
        return result
    result["ready"] = True
    return result


def button_timed_out(waited: float, timeout: float = SEND_BUTTON_TIMEOUT) -> bool:
    """Истекло ли ожидание активной кнопки (20 секунд по умолчанию)."""
    return float(waited) >= float(timeout)


def retries_exhausted(attempt: int, limit: int = CHIP_ERROR_RETRIES) -> bool:
    """Пора ли перезагружать браузер после неудачных попыток с файлом.

    Пользователь: «После 3 неудачных попыток, перезагружай браузер».
    """
    return int(attempt) >= int(limit)


def reloads_exhausted(count: int, limit: int = RELOAD_RETRIES) -> bool:
    """Исчерпан ли лимит перезагрузок чата."""
    return int(count) >= int(limit)


def status_text(reason: str, waited: float = 0.0, attempt: int = 0) -> str:
    """Текст для строки состояния окна (по-русски, без технического мусора)."""
    waited = max(0.0, float(waited))
    if reason == REASON_ERROR:
        return ("ИИ не принял файл — удаляю его из чата и прикрепляю заново"
                + (f" (попытка {attempt})" if attempt else ""))
    if reason == REASON_UPLOADING:
        return f"ИИ принимает файл… {waited:.0f} с"
    if reason in (REASON_BUTTON, REASON_NO_PAGE):
        return f"Ждём, пока ИИ примет файл (кнопка «Отправить» ещё неактивна)… {waited:.0f} с"
    if reason == REASON_INCOMPLETE:
        return f"Ждём вложения в чате… {waited:.0f} с"
    if reason == REASON_SETTLING:
        return "Файлы на месте — отправляем…"
    if reason == REASON_NO_FILES:
        return "Некому отправлять: файлы не выбраны"
    if reason == REASON_RELOAD:
        return "Чат не принял файлы — перезагружаю страницу ИИ и повторяю"
    return "Готовим отправку…"


def blocked_text() -> str:
    """Сообщение, когда файл так и не прикрепился."""
    return ("ИИ не принял файл: запрос без вложений не отправляю. "
            "Проверьте соединение и повторите «Сформировать».")
