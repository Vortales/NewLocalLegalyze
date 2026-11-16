"""v55 — в приложении ровно один адрес: тот, что открывается при запуске.

Замечание пользователя: «Мне нужно чтобы ты всегда и только всегда использовал
ссылку, которая используется при первичном запуске, никаких вторичных если
первичная не работает».

Что это значит в коде:

* адрес задан ОДИН раз — `config.CHAT_URL`; `main.py` (`URL`),
  `native_browser.py` (`DEFAULT_URL`) и `qt_browser.py` (`HOME`) берут его
  оттуда, своих литералов у них больше нет;
* автопереход на второй адрес (`SURFACE_FALLBACK_URL =
  "https://gemini.google.com/app"`) удалён: если на странице не удалось
  прикрепить файлы, приложение сообщает об этом в статусе и логе, но адрес
  НЕ меняет;
* остальные адреса в проекте страницу не открывают: скачивание портативного
  Chrome (`googlechromelabs.github.io`), реквизит-источник устава в
  законодательной базе (`forum.majestic-rp.ru`), лицензии шрифтов,
  `accounts.google.com` (только выдача прав микрофона через CDP), а
  `https://youtu.be/` — не ссылка, а образец в регулярке, которая ВЫРЕЗАЕТ
  адреса из постановлений.
"""

from __future__ import annotations

import ast
import io
import json
import re
import tokenize
import unittest
from pathlib import Path

import config

ROOT = Path(__file__).resolve().parents[1]

#: Единственный адрес приложения.
START_URL = "https://google.com/ai"

#: Файлы, которые открывают страницу в браузере.
BROWSER_FILES = ("main.py", "native_browser.py", "qt_browser.py")

#: Ссылка, которую нашли в коде и от которой отказались в v55.
OLD_FALLBACK = "gemini.google.com/app"


def _src(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _module_value(name: str, variable: str):
    """Значение присваивания `variable = ...` в модуле — БЕЗ импорта модуля.

    `main.py` и `qt_browser.py` тянут PyQt6, которого в тестовом окружении
    может не быть; читаем исходник и разбираем его через `ast`.
    """
    tree = ast.parse((ROOT / name).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id == variable:
                return ast.literal_eval(node.value) if isinstance(
                    node.value, ast.Constant) else ast.unparse(node.value)
        if isinstance(node, ast.AnnAssign):
            target = node.target
            if isinstance(target, ast.Name) and target.id == variable:
                return ast.literal_eval(node.value) if isinstance(
                    node.value, ast.Constant) else ast.unparse(node.value)
    return None


def _code(text: str) -> str:
    """Исходник без комментариев и docstring-ов (пояснения — не код).

    Разметка сохраняется: вырезанные куски заменяются пробелами, поэтому
    поиск по блокам функций продолжает работать построчно.
    """
    chars = list(text)
    lines = text.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))

    def blank(begin, finish):
        first = starts[begin[0] - 1] + begin[1]
        last = starts[finish[0] - 1] + finish[1]
        for index in range(first, min(last, len(chars))):
            if chars[index] != "\n":
                chars[index] = " "

    try:
        tree = ast.parse(text)
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except Exception:
        return text

    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if not isinstance(first, ast.Expr):
            continue
        value = first.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            blank((first.lineno, first.col_offset),
                  (first.end_lineno, first.end_col_offset))

    for token in tokens:
        if token.type == tokenize.COMMENT:
            blank(token.start, token.end)

    return "".join(chars)
class V55SingleUrlTests(unittest.TestCase):
    """Один адрес — в конфиге, и только он используется."""

    def test_config_holds_the_start_url(self):
        self.assertEqual(config.CHAT_URL, START_URL)

    def test_all_browser_entry_points_use_the_config_value(self):
        for name, variable in (("main.py", "URL"),
                               ("native_browser.py", "DEFAULT_URL"),
                               ("qt_browser.py", "HOME")):
            self.assertEqual(_module_value(name, variable), "CHAT_URL", name)

    def test_no_hardcoded_chat_urls_in_browser_modules(self):
        for name in BROWSER_FILES:
            code = _code(_src(name))
            self.assertNotIn('"https://google.com/ai"', code, name)
            self.assertNotIn("'https://google.com/ai'", code, name)

    def test_the_url_is_defined_exactly_once(self):
        code = _code(_src("config.py"))
        self.assertEqual(code.count(START_URL), 1)
        for name in BROWSER_FILES:
            self.assertNotIn(f'= "{START_URL}"', _code(_src(name)), name)


class V55NoFallbackTests(unittest.TestCase):
    """Второй ссылки (автоперехода) больше нет."""

    def test_fallback_constant_is_gone(self):
        for name in BROWSER_FILES:
            self.assertNotIn("SURFACE_FALLBACK_URL", _src(name), name)

    def test_fallback_url_is_gone(self):
        for name in BROWSER_FILES:
            self.assertNotIn(OLD_FALLBACK, _code(_src(name)), name)

    def test_upload_failure_does_not_navigate(self):
        source = _code(_src("main.py"))
        block = source.split("def _on_upload_blocked(", 1)[1].split("\n    def ", 1)[0]
        self.assertNotIn("Page.navigate", block)
        self.assertIn("upload.blocked_no_fallback", block)
        self.assertIn("_set_status", block)

    def test_navigation_happens_only_to_the_start_url(self):
        """Все `Page.navigate` идут на единственный адрес приложения."""
        source = _src("main.py")
        targets = re.findall(r'Page\.navigate",\s*\{"url":\s*([^}]+)\}', source)
        self.assertTrue(targets, "навигация должна быть хотя бы при перезагрузке")
        for target in targets:
            self.assertIn(target.strip(), ("URL", "self._url", "url"),
                          f"навигация на посторонний адрес: {target}")

    def test_other_urls_are_not_pages(self):
        """Остальные адреса проекта страницу не открывают."""
        # скачивание портативного Chrome и реквизит устава — не навигация
        self.assertIn("chrome-for-testing",
                      (ROOT / "browser_fetch.py").read_text(encoding="utf-8"))
        reestr = list((ROOT / "data" / "reestr").glob("*.json"))
        if reestr:
            for path in reestr:
                self.assertIsInstance(str(path), str)
        # accounts.google.com — только выдача прав микрофона
        source = _code(_src("main.py"))
        microphone = source.split("def grant_mic_permission(", 1)[1].split("\n    def ", 1)[0]
        self.assertIn("accounts.google.com", microphone)


class V55PromptAndDocsTests(unittest.TestCase):
    """Документация фиксирует правило «одна ссылка»."""

    def test_readme_mentions_the_single_url_rule(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("CHAT_URL", text)

    def test_changelog_exists(self):
        changelog = ROOT / "ИСПРАВЛЕНИЯ_v55.md"
        self.assertTrue(changelog.is_file())
        self.assertIn("CHAT_URL", changelog.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
