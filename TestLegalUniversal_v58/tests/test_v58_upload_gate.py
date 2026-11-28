"""v58 — шлюз экспорта в чат ИИ: чипы вложений и кнопка «Отправить».

Замечание пользователя (дампы `Загрузка.txt`, `После загрузки.txt`,
`Ошибка загрузки.txt`, `Удалить.txt`):

    «При экспорте файлов в чат с ИИ, если один из них ещё не загрузился, то
    есть кнопка отправки недоступна, ожидать кнопку отправки и только тогда,
    когда она сменит состояние на активное, отправлять запрос, во всех
    случаях, когда ты что-либо экспортируешь в чат с ИИ. А если имеется хоть
    одна ошибка в экспортируемом файле, то удалить файл и заново его
    загрузить (логика автодогрузки при отсутствующих файлах уже имеется)».

Что дало сравнение дампов:

* загрузка  → у хоста чипа `oacyne`, у кнопки `IbS5tc wdK4Nc`, `tabindex=-1`;
* готово    → ни `oacyne`, ни `IbS5tc`/`wdK4Nc`, `tabindex=0`;
* ошибка    → `bGkj2b`, `div.rUmyzd`, `div.oIIrVc` («При загрузке произошла
  ошибка»), кнопка снова `IbS5tc wdK4Nc`, `tabindex=-1`;
* крестик `div.zXgSbc` и `data-progressvalue="0"` есть ВО ВСЕХ состояниях.

Последнее и было дефектом: старая проверка «есть крестик → файл прикреплён»
подтверждала файл, который не загрузился.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

import upload_gate as ugate  # noqa: E402


def _src(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


#: Кнопка «Отправить» в состоянии загрузки/ошибки (как в дампах).
BUTTON_BLOCKED = {"found": True,
                  "classes": "uMMzHc OEueve IbS5tc wdK4Nc u7hiGb",
                  "tabindex": "-1"}
#: Кнопка «Отправить» после загрузки.
BUTTON_READY = {"found": True, "classes": "uMMzHc OEueve u7hiGb",
                "tabindex": "0"}


def _chip(name="Memphis.pdf", uploading=False, error=False):
    return {"name": name, "uploading": uploading, "error": error}


class ChipStateTests(unittest.TestCase):
    """Разбор состояния чипов: загрузка / ошибка / готово."""

    def test_uploading_chip_blocks_sending(self):
        gate = ugate.should_send([_chip(uploading=True)], BUTTON_BLOCKED,
                                 ["Memphis.pdf"])
        self.assertFalse(gate["ready"])
        self.assertEqual(gate["reason"], ugate.REASON_UPLOADING)

    def test_error_chip_blocks_sending(self):
        gate = ugate.should_send([_chip(error=True)], BUTTON_BLOCKED,
                                 ["Memphis.pdf"], error_streak=2)
        self.assertFalse(gate["ready"])
        self.assertEqual(gate["reason"], ugate.REASON_ERROR)
        self.assertEqual(gate["errors"], ["Memphis.pdf"])

    def test_error_is_confirmed_with_a_delay(self):
        """Ошибку считаем настоящей со второго замера (антидребезг)."""
        first = ugate.should_send([_chip(error=True)], BUTTON_BLOCKED,
                                  ["Memphis.pdf"], error_streak=1)
        second = ugate.should_send([_chip(error=True)], BUTTON_BLOCKED,
                                   ["Memphis.pdf"], error_streak=2)
        self.assertNotEqual(first["reason"], ugate.REASON_ERROR)
        self.assertEqual(second["reason"], ugate.REASON_ERROR)
        self.assertEqual(ugate.ERROR_CONFIRM, 2)

    def test_ready_chip_allows_sending(self):
        gate = ugate.should_send([_chip()], BUTTON_READY, ["Memphis.pdf"],
                                 confirmed_at=0.0)
        self.assertTrue(gate["ready"])
        self.assertEqual(gate["names"], ["Memphis.pdf"])

    def test_cross_mark_is_not_a_readiness_sign(self):
        """v58: в разборе чипов крестик `zXgSbc` больше не участвует."""
        source = _src("main.py")
        start = source.index('JS_CHECK_FILES = r"""')
        block = source[start:source.index('JS_SEND_BUTTON = r"""')]
        self.assertNotIn("zXgSbc') ) { done", block)
        # «готово» определяется через oacyne / ошибку, а не через крестик
        self.assertIn("oacyne", block)
        self.assertIn("oIIrVc", block)
        self.assertIn("rUmyzd", block)

    def test_duplicate_chips_are_merged(self):
        chips = ugate.normalize_chips([
            {"name": "Memphis.pdf", "uploading": True, "error": False},
            {"name": "Memphis.pdf", "uploading": False, "error": False},
            {"name": "", "uploading": False, "error": False},
        ])
        self.assertEqual(len([c for c in chips if c["name"]]), 1)
        self.assertTrue(chips[0]["uploading"])


class SendButtonTests(unittest.TestCase):
    """Кнопка «Отправить» — главный признак «экспорт завершён»."""

    def test_blocked_by_classes(self):
        state = ugate.normalize_send(BUTTON_BLOCKED)
        self.assertTrue(state["found"])
        self.assertFalse(state["enabled"])

    def test_ready_button(self):
        self.assertTrue(ugate.normalize_send(BUTTON_READY)["enabled"])

    def test_blocked_by_disabled_attribute(self):
        raw = dict(BUTTON_READY, disabled=True)
        self.assertFalse(ugate.normalize_send(raw)["enabled"])

    def test_blocked_by_aria(self):
        raw = dict(BUTTON_READY, aria_disabled="true")
        self.assertFalse(ugate.normalize_send(raw)["enabled"])

    def test_missing_button_is_not_ready(self):
        state = ugate.normalize_send({"found": False})
        self.assertFalse(state["enabled"])
        gate = ugate.should_send([_chip()], {"found": False}, ["Memphis.pdf"])
        self.assertEqual(gate["reason"], ugate.REASON_NO_PAGE)

    def test_timeout_is_20_seconds(self):
        self.assertEqual(ugate.SEND_BUTTON_TIMEOUT, 20.0)
        self.assertFalse(ugate.button_timed_out(19.9))
        self.assertTrue(ugate.button_timed_out(20.0))

    def test_button_priority_over_missing_files(self):
        """Неактивная кнопка сообщается раньше, чем «нет файла»."""
        gate = ugate.should_send([], BUTTON_BLOCKED, ["Memphis.pdf"])
        self.assertEqual(gate["reason"], ugate.REASON_BUTTON)


class NoSendWithoutFilesTests(unittest.TestCase):
    """Запрос без вложений не отправляется никогда."""

    def test_no_required_files(self):
        gate = ugate.should_send([], BUTTON_READY, [])
        self.assertFalse(gate["ready"])
        self.assertEqual(gate["reason"], ugate.REASON_NO_FILES)

    def test_missing_required_file(self):
        gate = ugate.should_send([_chip("Шаблон.txt")], BUTTON_READY,
                                 ["Memphis.pdf"])
        self.assertFalse(gate["ready"])
        self.assertEqual(gate["reason"], ugate.REASON_INCOMPLETE)
        self.assertEqual(gate["missing"], ["Memphis.pdf"])

    def test_empty_chat_is_ignored_until_files_appear(self):
        """Решение: пустой чат не повод ждать кнопку — ориентир только файлы.

        Пока файлов нет, причина — «нет файлов»/«нет страницы», то есть окно
        не перезагружается по факту пустого поля ввода.
        """
        gate = ugate.should_send([], BUTTON_READY, ["Memphis.pdf"])
        self.assertFalse(gate["ready"])
        self.assertIn(gate["reason"], (ugate.REASON_INCOMPLETE,
                                       ugate.REASON_NO_PAGE))


class ChipRetryTests(unittest.TestCase):
    """Удаление сломанного чипа и перезагрузка после 3 неудач."""

    def test_three_attempts_then_reload(self):
        self.assertFalse(ugate.retries_exhausted(1))
        self.assertFalse(ugate.retries_exhausted(2))
        self.assertTrue(ugate.retries_exhausted(3))
        self.assertEqual(ugate.CHIP_ERROR_RETRIES, 3)

    def test_reload_limit(self):
        self.assertFalse(ugate.reloads_exhausted(1))
        self.assertTrue(ugate.reloads_exhausted(2))
        self.assertEqual(ugate.RELOAD_RETRIES, 2)


class SourceIntegrationTests(unittest.TestCase):
    """Правки на месте в исходниках: без CDP-сессии проверяем текст."""

    def test_force_unlock_is_removed_from_send_script(self):
        source = _src("main.py")
        block = source[source.index("JS_STOP_AND_SEND = r"):]
        block = block[:block.index("JS_REMOVE_ALL_FILES")]
        # только строки кода: в комментарии эти вызовы упомянуты по делу
        code = "\n".join(line for line in block.splitlines()
                         if not line.strip().startswith("//"))
        self.assertNotIn("el.disabled = false", code)
        self.assertNotIn("removeAttribute('disabled')", code)
        self.assertNotIn("setAttribute('aria-disabled', 'false')", code)
        self.assertIn("reason: 'disabled'", code)

    def test_chip_error_removes_only_that_chip(self):
        source = _src("main.py")
        block = source[source.index("    def _handle_chip_error("):]
        block = block[:block.index("    def _reload_for_upload_retry(")]
        self.assertIn("remove_chip(page", block)
        self.assertNotIn("JS_REMOVE_ALL_FILES", block)
        self.assertIn("retries_exhausted", block)

    def test_send_waiting_is_used_everywhere(self):
        source = _src("main.py")
        for marker in ("documents.send_blocked", "gov.intake.send_blocked",
                       "pending_send.send_blocked"):
            self.assertIn(marker, source, marker)
        self.assertEqual(source.count("wait_send_button(page)"), 3)

    def test_pending_send_uses_gate(self):
        source = _src("main.py")
        block = source[source.index("    def _try_send_pending("):]
        block = block[:block.index("    @pyqtSlot(bool, str)")]
        self.assertIn("ugate.should_send", block)
        self.assertIn("_handle_chip_error", block)
        self.assertIn("_reload_for_upload_retry", block)

    def test_upload_thread_treats_error_chips_as_missing(self):
        source = _src("main.py")
        self.assertIn("upload.chip_error_seen", source)
        self.assertIn("ugate.error_chips", source)


class StatusTextTests(unittest.TestCase):
    """Тексты в окне понятны человеку (без технического мусора)."""

    def test_uploading_text_shows_seconds(self):
        text = ugate.status_text(ugate.REASON_UPLOADING, waited=7.4)
        self.assertIn("принимает файл", text)
        self.assertIn("7", text)

    def test_error_text_mentions_reupload(self):
        text = ugate.status_text(ugate.REASON_ERROR, attempt=2)
        self.assertIn("заново", text)
        self.assertIn("2", text)

    def test_blocked_text_explains_why_nothing_was_sent(self):
        text = ugate.blocked_text()
        self.assertIn("без вложений", text)
        self.assertIn("Сформировать", text)


class VersionTests(unittest.TestCase):
    """Правило проекта: новая версия — своя папка и поднятый номер."""

    def test_version_is_bumped(self):
        self.assertIn('CURRENT_VERSION = "2.14.0"', _src("config.py"))

    def test_release_revision(self):
        self.assertIn("universal-embedded-v58", _src("diagnostics.py"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
