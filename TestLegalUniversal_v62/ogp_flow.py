"""ogp_flow.py — управление этапами формы «Постановление Прокурора» (v61).

Замечание пользователя (v61): «1. Мы вставляем текст обращения… На 1 этапе,
когда обращение ещё не было отправлено в чат с ИИ, „Проверка ИИ“ должна быть
на месте „Сформировать“ и называться „Этап 1“… Как только 1 этап выполнен,
кнопка меняет название на „Этап 2“… На 2 этапе после 1 этапа в „Цепочка
этапов“ появляется рекомендуемый порядок автоматически после ответа ИИ на 1
этапе… на каждый подэтап Этапа 2 предусмотри формирование Постановления (.png
файла) и как только все подэтапы этапа 2 пройдены и все сделали, ИИ дал
положительный ответ, то мы уже можем закрывать обращение и переходить к
этапу 3».

То есть вместо двух разных кнопок («Проверка ИИ» и «Сформировать») в форме
одна главная кнопка, которая сама знает, какой этап выполняет:

* этап 1 — «Проверка ИИ» (текст обращения → служебный блок + план этапа 2);
* этап 2 — постановление по текущему шагу цепочки (PNG на каждый подэтап);
* этап 3 — итоговое постановление по обращению.

Модуль не зависит от Qt: раскладку проверяют обычные тесты, окно только
показывает готовые подписи.
"""

from __future__ import annotations

import ogp_chain

#: Главная кнопка формы: (подпись, что делает, доступна ли).
ACTION_INTAKE = "intake"
ACTION_STEP = "step"
ACTION_FINAL = "final"
ACTION_CLOSED = "closed"


def main_action(case) -> dict:
    """Что должна делать главная кнопка формы для этого дела (v61).

    Возвращает ``{"action": …, "caption": …, "enabled": bool, "hint": str}``.
    Подписи — ровно те, что просил пользователь: «Этап 1», «Этап 2»,
    «Этап 3».
    """
    if not str(getattr(case, "appeal_raw", "") or "").strip():
        return {
            "action": ACTION_INTAKE,
            "caption": "Этап 1",
            "enabled": False,
            "hint": "Вставьте текст обращения целиком — без него этап 1 не "
                    "запускается.",
        }
    if not case.outcome:
        return {
            "action": ACTION_INTAKE,
            "caption": "Этап 1",
            "enabled": True,
            "hint": "Этап 1: ИИ проверит обращение, скажет, принято оно или "
                    "нет, и сразу предложит цепочку этапа 2.",
        }
    if case.outcome != ogp_chain.OUTCOME_ACCEPT:
        return {
            "action": ACTION_CLOSED,
            "caption": "Этап 1 (завершён)",
            "enabled": False,
            "hint": "Этап 1 завершён отказным решением: этапы 2 и 3 не "
                    "наступают. Решение можно изменить на вкладке «Цепочка "
                    "этапов».",
        }
    if case.work_plan and not case.work_done():
        step = case.current_work_step()
        number = step.get("n") if step else case.next_work_step()
        return {
            "action": ACTION_STEP,
            "caption": "Этап 2",
            "enabled": True,
            "hint": f"Этап 2, шаг {number} из {len(case.work_plan)}: "
                    f"{(step or {}).get('title', '')}. По каждому шагу — своё "
                    "постановление и свой PNG.",
        }
    if not case.work_plan:
        return {
            "action": ACTION_STEP,
            "caption": "Этап 2",
            "enabled": False,
            "hint": "Цепочка этапа 2 ещё не получена: выполните этап 1 "
                    "(Проверку ИИ) — план придёт вместе с решением.",
        }
    return {
        "action": ACTION_FINAL,
        "caption": "Этап 3",
        "enabled": True,
        "hint": "Все шаги этапа 2 пройдены — можно закрывать обращение "
                "итоговым постановлением этапа 3.",
    }


def stage_badge(case) -> str:
    """Короткая подпись текущего этапа для заголовка окна (v61)."""
    action = main_action(case)["action"]
    if action == ACTION_INTAKE:
        return "этап 1"
    if action == ACTION_CLOSED:
        return "этап 1 — решение принято"
    if action == ACTION_FINAL:
        return "этап 3"
    step = case.current_work_step()
    total = len(case.work_plan or [])
    if step:
        return f"этап 2 — шаг {step.get('n')} из {total}"
    return f"этап 2 — шагов {total}"


#: Признак положительного ответа ИИ по шагу этапа 2. Пока все шаги не дали
#: положительный ответ, этап 3 не открывается (замечание пользователя:
#: «как только все подэтапы этапа 2 пройдены и все сделали, ИИ дал
#: положительный ответ, то мы уже можем закрывать обращение»).
NEGATIVE_MARKS = ("отказать", "отказано", "не подлежит", "не подтвержд",
                  "недостаточно", "прекратить", "прекращено", "не установлен",
                  "не выявлено")
POSITIVE_MARKS = ("признать виновн", "привлечь к ответственности",
                  "назначить штраф", "возбудить уголовное дело",
                  "удовлетворить", "истребовать", "вызвать на допрос",
                  "приостановить", "внести изменения", "установлено",
                  "подтверждено")


def verdict_of(text: str) -> str:
    """Итог шага этапа 2 по ответу ИИ: «positive», «negative» или пусто.

    Смотрим резолютивную часть (после «ПОСТАНОВИЛ:»), а не весь текст: в
    мотивировочной части ИИ часто пишет «оснований для отказа не выявлено».
    """
    raw = str(text or "")
    low = raw.lower()
    if not low.strip():
        return ""
    marker = low.rfind("постановил")
    body = low[marker:] if marker >= 0 else low
    if any(mark in body for mark in NEGATIVE_MARKS):
        if not any(mark in body for mark in POSITIVE_MARKS):
            return "negative"
    if any(mark in body for mark in POSITIVE_MARKS):
        return "positive"
    if any(mark in body for mark in NEGATIVE_MARKS):
        return "negative"
    return ""


def apply_verdict(case, step_number: int, text: str) -> str:
    """Записывает итог шага в цепочку дела и возвращает его (v61)."""
    verdict = verdict_of(text)
    if not verdict:
        return ""
    for step in case.work_plan or []:
        if step.get("n") == step_number:
            step["verdict"] = verdict
            # Отрицательный итог по шагу не закрывает дело насильно: прокурор
            # решает сам. Но этап 3 открывается только при положительных.
            case.touch()
            return verdict
    return ""


def all_steps_positive(case) -> bool:
    """Все шаги этапа 2 пройдены и по каждому итог положительный (v61)."""
    plan = list(case.work_plan or [])
    if not plan:
        return False
    return all(step.get("done") and step.get("verdict") == "positive"
               for step in plan)
