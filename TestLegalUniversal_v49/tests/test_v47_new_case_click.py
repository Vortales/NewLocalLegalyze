"""v47: «Новое обращение» создаёт обращение с ОДНОГО нажатия.

Замечание пользователя: «В кнопке "Документ", внутри "Постановление Прокурора"
наш объект → кнопка "Новое обращение". Для создания в "Обращения" обращения
нужно 2 раза нажать на "Новое обращение"».

Причина: стартовый список обращений (`GovCasesDialog`) возвращал `None` и когда
пользователь нажал «Новое обращение», и когда просто закрыл окно. Форма
`PostanovlenieProkuroraDialog` получала `case=None` и по нему решала, что дело
не выбрано: `ask_case_on_show = bool(ask_case) and case is None`, а в
`showEvent` снова открывала список обращений. Поэтому первое нажатие
«Новое обращение» приводило ко второму окну списка, и создать обращение
получалось только со второго раза.
"""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MAIN = (ROOT / "main.py").read_text(encoding="utf-8")
UI = (ROOT / "postanovlenie_prokuror_ui.py").read_text(encoding="utf-8")


def block(source: str, name: str, stop: str) -> str:
    start = source.index(f"def {name}")
    return source[start:source.index(f"def {stop}", start)]


class NewCaseFromStartListTests(unittest.TestCase):
    """Выбор «Новое обращение» в списке — это уже выбор, а не пустое дело."""

    def setUp(self):
        self.opener = block(MAIN, "_open_postanovlenie_prokurora",
                            "_choose_gov_case")
        self.chooser = block(MAIN, "_choose_gov_case",
                             "_on_pending_send_restart")

    def test_new_requested_is_remembered(self):
        self.assertIn("self._gov_case_new_requested = True", self.chooser)
        self.assertIn("dialog.new_requested", self.chooser)

    def test_flag_is_reset_for_each_showing(self):
        # Сброс стоит ДО вызова списка, иначе прошлый выбор повлиял бы на новое
        # открытие формы.
        self.assertIn("self._gov_case_new_requested = False", self.opener)
        self.assertLess(self.opener.index("self._gov_case_new_requested = False"),
                        self.opener.index("self._choose_gov_case(store)"))

    def test_form_is_not_asked_again_after_new_requested(self):
        self.assertIn("ask_case=not self._gov_case_new_requested", self.opener)

    def test_new_case_starts_empty_right_away(self):
        # Дело создаётся до показа формы — как это делало второе нажатие.
        self.assertIn("dlg.new_case()", self.opener)
        self.assertIn("if self._gov_case_new_requested:", self.opener)
        self.assertLess(self.opener.index("dlg.new_case()"),
                        self.opener.index("dlg.exec()"))
        self.assertLess(self.opener.index("ask_case=not"),
                        self.opener.index("dlg.new_case()"))

    def test_form_keeps_asking_when_list_was_skipped(self):
        # ask_case остаётся параметром формы: старый вызов без него работает.
        self.assertIn("ask_case=True", UI)
        self.assertIn("self.ask_case_on_show = bool(ask_case) and case is None",
                      UI)


class FormShowsListOnlyWhenNoChoiceTests(unittest.TestCase):
    """Форма спрашивает дело ровно один раз — на первом показе."""

    def test_show_event_asks_once(self):
        show = block(UI, "showEvent", "mousePressEvent")
        self.assertIn("self.ask_case_on_show = False", show)
        self.assertLess(show.index("self.ask_case_on_show = False"),
                        show.index("self._open_case_dialog()"))

    def test_new_case_is_a_real_case(self):
        new_case = block(UI, "new_case", "refresh_case")
        self.assertIn("cases.GovCase(number=self.cases.next_number())", new_case)
        self.assertIn("self.cases.save(self.case)", new_case)


if __name__ == "__main__":
    unittest.main()
