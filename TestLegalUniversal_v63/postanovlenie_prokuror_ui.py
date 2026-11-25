"""postanovlenie_prokuror_ui.py — форма постановления Прокурора (v43.2).

Логика работы с обращением переработана по замечаниям v42:

1. **Мало текста в форме.** Подсказки-абзацы про статьи Регламента убраны:
   в бланке остались только поля и короткие пояснения.
2. **Вид решения всегда «Определит ИИ».** Конкретные виды решений — только
   дополнительные варианты списка; по умолчанию стоит «определит ИИ» своего
   этапа.
3. **Нет «Предпросмотра» и «Сохранить без отправки».** Бланк печатается из
   ответа ИИ по кнопке «Сформировать»; отдельный предпросмотр и сохранение
   без отправки только путали.
4. **Работа идёт по делу «Обращение №X»:**
   * обращение вставляется в поле и проверяется кнопкой **«Проверка ИИ»**;
   * по ответу ИИ дело получает состояние **«В процессе»** или **«Отказано»**;
   * при отказе прокурор может проверить снова или принять обращение вопреки
     ответу ИИ («Принять всё равно»);
   * у принятого обращения открываются «Обстоятельства» прокурора четырьмя
     частями: видеофиксация, доводы за, доводы против, предложенное
     наказание и собственное решение;
   * работа поэтапна: минимальная цепочка «принять → постановить», но
     доступны и дополнительные этапы (доп. проверка, перепроверка,
     переадресация, жалоба, завершение);
   * PNG каждого этапа получает своё имя
     («AG-1_Постановление_Этап_1_принятие_Вид_…») и свою папку: номер
     постановления у этапов один, а метки этапа и вида решения — разные (v49),
     поэтому первый документ не затирается следующим;
   * **реквизиты обращения определяет ИИ** (v49): заявитель, нарушитель, дата
     события и предмет приходят ключевыми словами в ответе и подставляются в
     форму — локального «распознавания» по тексту больше нет;
   * **этап выбирается в одном месте** — на вкладке «Постановление» (v49),
     дубль этих полей из «Обстоятельств» убран;
   * окно не закрывается по «Проверке ИИ» и «Сформировать» (v49): форма
     обновляется по отправленному запросу и полученному ответу ИИ.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QTextOption
from PyQt6.QtWidgets import (
    QAbstractScrollArea, QApplication, QComboBox, QDialog, QFileDialog,
    QFormLayout, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QTabWidget,
    QVBoxLayout, QWidget,
)

import ai_request
import diagnostics as diag
import doc_render as render
import postanovlenie_data as pdata
import postanovlenie_prokuror_data as gdata
import gov_case as cases
import ogp_chain
import ogp_flow
import ogp_stages
import pending_send


def _dark_style() -> str:
    """Стиль формы в палитре остального клиента (никаких «прозрачных» надписей)."""
    return """
    QDialog, QWidget { background: #0e1020; color: #e6e8f5; }
    QWidget#Sheet { background: #0e1020; }
    QLabel { color: #e6e8f5; font-size: 12px; background: transparent; }
    QLabel#Title { color: #9aa1ff; font-size: 17px; font-weight: 700; }
    QLabel#Caption { color: #8f96bf; font-size: 11px; }
    QLabel#Section {
        color: #9aa1ff; font-size: 11px; font-weight: 700;
        padding-top: 6px; letter-spacing: 0.4px;
    }
    QLabel#Field { color: #c9cde8; font-size: 12px; }
    QLineEdit, QPlainTextEdit, QComboBox {
        background: #171b38; color: #e6e8f5; border: 1px solid #262a4a;
        border-radius: 7px; padding: 5px 8px; font-size: 12px;
        selection-background-color: #5b61e8;
    }
    QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {
        border-color: #7c83ff; background: #1b1f40;
    }
    QComboBox QAbstractItemView {
        background: #171b38; color: #e6e8f5; border: 1px solid #262a4a;
        selection-background-color: #5b61e8;
    }
    QPushButton {
        background: #171b38; color: #e6e8f5; border: 1px solid #262a4a;
        border-radius: 8px; padding: 7px 13px; font-size: 12px; font-weight: 600;
    }
    QPushButton:hover { border-color: #7c83ff; background: #1b1f40; }
    QPushButton:disabled { color: #5d6486; border-color: #22263f; }
    QPushButton#Primary {
        background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
            stop:0 #7c83ff, stop:1 #5b61e8);
        border: none; color: #ffffff;
    }
    QPushButton#Primary:hover { background: #5b61e8; }
    QTabWidget::pane { border: 1px solid #262a4a; border-radius: 8px; top: -1px; }
    QTabBar::tab {
        background: #12162b; color: #8f96bf; padding: 7px 16px;
        border: 1px solid #262a4a; border-bottom: none;
        border-top-left-radius: 7px; border-top-right-radius: 7px;
    }
    QTabBar::tab:selected { background: #1b1f40; color: #e6e8f5; }
    QScrollArea { border: none; background: transparent; }
    QListWidget {
        background: #171b38; color: #e6e8f5; border: 1px solid #262a4a;
        border-radius: 7px; font-size: 12px;
    }
    QListWidget::item:selected { background: #5b61e8; }
    """


class GovCasesDialog(QDialog):
    """Стартовое окно «Обращения»: список дел и их состояний (v43).

    Замечание: «хочу, чтобы при нажатии Документ → Постановление Прокурора
    открывалось стартовое окно с обращениями и их состояниями». Поэтому форма
    постановления больше не открывается «в пустое поле»: сначала список дел,
    из которого можно открыть обращение или начать новое.
    """

    def __init__(self, store, parent=None, title="Обращения"):
        super().__init__(parent)
        self.store = store
        #: Выбранное дело (None — начать новое обращение).
        self.selected_case = None
        #: Пользователь попросил начать новое обращение.
        self.new_requested = False
        self.setWindowTitle(title)
        self.setMinimumSize(720, 460)
        self.resize(820, 520)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Dialog
        )
        self.setModal(True)
        self.setStyleSheet(_dark_style())
        self._build()
        self._refresh()
        self._drag_pos = None

    # ------------------------------------------------------------------ вид
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(8)
        head = QHBoxLayout()
        head.addWidget(self._title("Обращения"))
        head.addStretch()
        self.count_label = QLabel("")
        self.count_label.setObjectName("Caption")
        head.addWidget(self.count_label)
        root.addLayout(head)

        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _i: self._open_selected())
        self.list.currentRowChanged.connect(lambda _i: self._refresh_buttons())
        root.addWidget(self.list, 1)

        self.empty_label = QLabel(
            "Обращений пока нет. Нажмите «Новое обращение»: вставьте текст "
            "обращения с форума и выполните «Проверку ИИ».")
        self.empty_label.setObjectName("Caption")
        self.empty_label.setWordWrap(True)
        root.addWidget(self.empty_label)

        self.info_label = QLabel("")
        self.info_label.setObjectName("Caption")
        self.info_label.setWordWrap(True)
        root.addWidget(self.info_label)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(lambda _c=False: self.reject())
        buttons.addWidget(close_btn)
        delete_btn = QPushButton("Удалить")
        delete_btn.setToolTip("Удалить выбранное обращение из списка дел")
        delete_btn.clicked.connect(lambda _c=False: self._delete_selected())
        self.delete_btn = delete_btn
        buttons.addWidget(delete_btn)
        buttons.addStretch()
        new_btn = QPushButton("Новое обращение")
        new_btn.clicked.connect(lambda _c=False: self._new_case())
        buttons.addWidget(new_btn)
        open_btn = QPushButton("Открыть")
        open_btn.setObjectName("Primary")
        open_btn.clicked.connect(lambda _c=False: self._open_selected())
        self.open_btn = open_btn
        buttons.addWidget(open_btn)
        root.addLayout(buttons)

    @staticmethod
    def _title(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("Title")
        return label

    # -------------------------------------------------------------- данные
    def _refresh(self) -> None:
        self.list.clear()
        rows = cases.case_rows(self.store)
        for row in rows:
            # v43.1: `QListWidget.addItem(str)` в PyQt6 возвращает None, и
            # `item.setForeground(...)` падал с AttributeError — стартовое окно
            # обращений вообще не открывалось (лог 20261108-004441-4820-baf125,
            # postanovlenie_prokuror_ui.py:198). Создаём элемент явно.
            item = QListWidgetItem(
                f"{row['title']} — {row['status']}\n"
                f"    {row['progress']} · обновлено {row['updated']}")
            # состояние дела видно сразу: цвет строки = цвет состояния
            item.setForeground(QBrush(QColor(row["color"])))
            item.setData(Qt.ItemDataRole.UserRole, row["number"])
            self.list.addItem(item)
        self.count_label.setText(f"Всего: {len(rows)}")
        self.empty_label.setVisible(not rows)
        if rows:
            self.list.setCurrentRow(0)
        self._refresh_buttons()

    def reload(self) -> None:
        """Перечитать дела с диска и перерисовать список (v46).

        Нужно после корректного ответа «Проверки ИИ»: окно обращений и
        подпись выбранной строки показывали состояние на момент открытия, и
        пользователь видел «Обращение №1: Отказано (…)» — как будто программа
        зависла на старом ответе (замечание пользователя).
        """
        selected = None
        if self.list.currentRow() >= 0:
            selected = self.list.currentItem().data(Qt.ItemDataRole.UserRole)
        self._refresh()
        if selected is not None:
            for row in range(self.list.count()):
                item = self.list.item(row)
                if str(item.data(Qt.ItemDataRole.UserRole) or "") == str(selected):
                    self.list.setCurrentRow(row)
                    break
        self._refresh_buttons()
        self.list.viewport().update()

    def _refresh_buttons(self) -> None:
        has_choice = self.list.currentRow() >= 0
        for button in (getattr(self, "open_btn", None),
                       getattr(self, "delete_btn", None)):
            if button is not None:
                button.setEnabled(has_choice)
        if not has_choice:
            self.info_label.setText("")
            return
        number = self.list.currentItem().data(Qt.ItemDataRole.UserRole)
        case = self.store.load(str(number or ""))
        if case is None:
            self.info_label.setText("")
            return
        facts = "обстоятельства заполнены" if case.has_facts() else "обстоятельств пока нет"
        basis = f" · основание: {case.intake_basis}" if case.intake_basis else ""
        self.info_label.setText(
            f"Обращение №{case.number}: {case.status_label()} ({facts}){basis}")

    # ------------------------------------------------------------ действия
    def _current_case(self):
        row = self.list.currentRow()
        if row < 0:
            return None
        number = self.list.item(row).data(Qt.ItemDataRole.UserRole)
        return self.store.load(str(number or ""))

    def _open_selected(self) -> None:
        case = self._current_case()
        if case is None:
            self.info_label.setText("Выберите обращение в списке.")
            return
        self.selected_case = case
        self.accept()

    def _new_case(self) -> None:
        self.new_requested = True
        self.selected_case = None
        self.accept()

    def _delete_selected(self) -> None:
        case = self._current_case()
        if case is None:
            return
        confirm = QMessageBox.question(
            self, "Удалить обращение?",
            f"Удалить из списка обращение №{case.number}?\n\n"
            "Файлы постановлений в папке документов останутся на диске.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self.store.delete(case.number)
        self._refresh()

    # ------------------------------------------------- перетаскивание окна
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            child = self.childAt(event.position().toPoint())
            if not isinstance(child, QListWidget):
                self._drag_pos = (event.globalPosition().toPoint()
                                  - self.frameGeometry().topLeft())
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton and self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        super().mouseReleaseEvent(event)


class PostanovlenieProkuroraDialog(QDialog):
    """Форма постановления Прокурора: дело по обращению → этапы → PNG."""

    #: Документ сформирован: (PostanovlenieProkurora, путь к .txt для чата, [png]).
    formed = pyqtSignal(object, str, object)
    #: Нужна проверка обращения ИИ: (текст запроса, номер дела).
    intake_requested = pyqtSignal(str, str)
    #: Заявка готова, но отправлять её нужно ПОСЛЕ перезапуска (v43.2):
    #: (запись отложенной отправки, путь к .txt заявки).
    restart_requested = pyqtSignal(object, str)
    #: v62: нажата «Сформировать» у узла цепочки: (узел, документ, ответ ИИ).
    print_node_document = pyqtSignal(object, object, str)
    #: v63: нажата «Закрыть обращение» на этапе 3: (номер дела).
    case_closed = pyqtSignal(str)
    #: v63: заявка узла 2.N / 3 уходит в ТЕКУЩИЙ диалог, без перезагрузки
    #: браузера: (запись отложенной отправки, путь к .txt заявки).
    send_in_chat = pyqtSignal(object, str)

    def __init__(self, cfg, base_dir=None, history=None, assets=None,
                 regulation=None, parent=None, case_store=None, case=None,
                 ask_case=True):
        super().__init__(parent)
        self.cfg = cfg or {}
        self.base_dir = Path(base_dir) if base_dir else Path(__file__).resolve().parent
        self.assets = assets or render.DocAssets(self.base_dir)
        self.regulation = regulation
        self.history = history or gdata.GovHistory(self.base_dir / "postanovleniya")
        self.cases = case_store or cases.GovCaseStore(
            self.base_dir / "postanovleniya")
        #: Текущее дело («Обращение №X»).
        self.case = cases.GovCase(number=self.cases.next_number())
        self.document = gdata.PostanovlenieProkurora()
        self.chat_txt_path = ""
        #: v50: путь к «Обращение.txt» — второе вложение заявки.
        self.appeal_txt_path = ""
        self.png_paths: list = []
        self._drag_pos = None
        #: Ответ ИИ на проверку обращения ещё не пришёл.
        self.intake_pending = False
        #: Что отправлено и ждёт ответа ИИ («Проверка ИИ» / «Постановление»).
        #: v49: окно не закрывается, поэтому отметка живёт в самой форме.
        self.waiting_for_answer = ""
        #: Дело, открытое сразу (из стартового окна обращений, v43).
        self.initial_case = case
        #: Нужно спросить, какое обращение открыть, при первом показе (v43).
        self.ask_case_on_show = bool(ask_case) and case is None

        self.setWindowTitle("Документы — Постановление Прокурора")
        self.setMinimumSize(1080, 720)
        self.resize(1240, 880)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Dialog
        )
        self.setModal(True)
        self.setStyleSheet(_dark_style())
        self._build()
        self._fill_defaults()
        self._refresh_case_view()
        if self.initial_case is not None:
            self.open_case(self.initial_case)

    # ------------------------------------------------------------------ утиль

    @staticmethod
    def _clean(text) -> str:
        """Схлопнутые пробелы: подписи формы и значения ключевых слов (v49)."""
        return " ".join(str(text or "").split())

    def _label(self, text: str, kind: str = "") -> QLabel:
        label = QLabel(text)
        if kind:
            label.setObjectName(kind)
        return label

    def _field_label(self, text: str) -> QLabel:
        return self._label(text, "Field")

    def _input(self, placeholder: str = "") -> QLineEdit:
        field = QLineEdit()
        field.setPlaceholderText(placeholder)
        field.setMinimumHeight(28)
        field.setMinimumWidth(190)
        field.setSizePolicy(QSizePolicy.Policy.Expanding,
                            QSizePolicy.Policy.Fixed)
        field.setClearButtonEnabled(False)
        return field

    def _text_area(self, placeholder: str = "", height: int = 120) -> QPlainTextEdit:
        """Текстовое поле с переносом по словам и без «уезжания» вправо (v38)."""
        area = QPlainTextEdit()
        area.setPlaceholderText(placeholder)
        area.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        area.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setSizeAdjustPolicy(
            QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored)
        area.setMinimumWidth(220)
        area.setMinimumHeight(height)
        area.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        return area

    @staticmethod
    def _compact(form: QFormLayout) -> QFormLayout:
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(6)
        form.setContentsMargins(0, 0, 0, 0)
        return form

    # ------------------------------------------------------------- построение

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(8)

        head = QHBoxLayout()
        head.addWidget(self._label("Постановление", "Title"))
        self.case_title = self._label("", "Title")
        head.addWidget(self.case_title)
        self.chain_badge = self._label("", "Caption")
        self.chain_badge.setStyleSheet("color: #8f96bf; font-size: 11px;")
        head.addWidget(self.chain_badge)
        self.status_badge = self._label("Новое", "Caption")
        self.status_badge.setStyleSheet("color: #8f96bf; font-size: 12px; font-weight: 700;")
        head.addWidget(self.status_badge)
        head.addStretch()
        self.status = self._label("", "Caption")
        head.addWidget(self.status)
        root.addLayout(head)

        # v62 (замечание пользователя): вкладки «Постановление» и
        # «Обстоятельства» убраны — «Цепочка этапов» делает всё сама, а
        # обстоятельства живут в отдельном окне.
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_appeal_tab(), "Обращение")
        self.tabs.addTab(self._build_chain_tab(), "Цепочка этапов")
        self.tabs.addTab(self._build_history_tab(), "История")
        root.addWidget(self.tabs, 1)
        # v61: общие реквизиты — отдельным блоком снизу окна.
        root.addWidget(self._build_requisites())

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(lambda _c=False: self.reject())
        buttons.addWidget(close_btn)
        self.cases_btn = QPushButton("Обращения")
        self.cases_btn.setToolTip(
            "Открыть список обращений и их состояний (или начать новое)")
        self.cases_btn.clicked.connect(lambda _c=False: self._open_case_dialog())
        buttons.addWidget(self.cases_btn)
        # v62: обстоятельства — отдельное окно слева, открывается/скрывается.
        self.facts_btn = QPushButton("Обстоятельства")
        self.facts_btn.setToolTip(
            "Показать окно обстоятельств: ввод прокурора и ответ ИИ по "
            "каждому этапу и подэтапу")
        self.facts_btn.clicked.connect(lambda _c=False: self._toggle_facts_window())
        buttons.addWidget(self.facts_btn)
        buttons.addStretch()
        # v61 (замечание пользователя): «Проверка ИИ» должна быть на месте «Сформировать»
        # «Сформировать» и называться «Этап 1»; как только этап 1 выполнен,
        # кнопка меняет название на «Этап 2», а после всей цепочки — на
        # «Этап 3». Поэтому главная кнопка одна и сама знает свой этап.
        self.form_btn = QPushButton("Этап 1")
        self.form_btn.setObjectName("Primary")
        self.form_btn.setToolTip("Этап 1: Проверка ИИ")
        self.form_btn.setEnabled(False)
        self.form_btn.clicked.connect(lambda _c=False: self._run_main_action())
        buttons.addWidget(self.form_btn)
        root.addLayout(buttons)

    # ------------------------------------------------ v61: реквизиты (подокно)

    def _build_requisites(self) -> QWidget:
        """Общий блок реквизитов под формой (v61, переверстан в v62).

        Замечание пользователя (v62): «„Реквизиты постановления (общие для
        этапов)" слегка налезают друг на друга, в „Кто выносит" аналогично…
        убери длинную строку про путь сохранения, оставь просто надпись
        „Сохранение" над „Папка…"». Поэтому блок собран сеткой: у каждой
        подписи своя ячейка, поля тянутся по ширине, а длинных пояснений нет.
        """
        panel = QWidget()
        panel.setObjectName("Sheet")
        grid = QGridLayout(panel)
        grid.setContentsMargins(0, 4, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(3)

        grid.addWidget(self._label("Реквизиты постановления", "Section"), 0, 0, 1, 3)
        grid.addWidget(self._label("Кто выносит", "Section"), 0, 3, 1, 3)
        grid.addWidget(self._label("Сохранение", "Section"), 0, 6, 1, 2)

        # --- номер и дата
        self.fields["doc_number"] = self._input("AG-1")
        self.fields["doc_number"].setMaximumWidth(120)
        self.fields["doc_date"] = self._input("06.11.2026")
        self.fields["doc_date"].setMaximumWidth(110)
        suggestion = QPushButton("Следующий №")
        suggestion.setFixedWidth(110)
        suggestion.setToolTip("Подставить следующий номер постановления по делу")
        suggestion.clicked.connect(lambda _c=False: self._suggest_number())
        grid.addWidget(self._field_label("Номер"), 1, 0)
        grid.addWidget(self.fields["doc_number"], 1, 1)
        grid.addWidget(self._field_label("Дата"), 1, 2)
        grid.addWidget(self.fields["doc_date"], 1, 3)
        grid.addWidget(suggestion, 1, 4)

        # --- кто выносит
        self.fields["author_name"] = self._input("Mekyla Barbosov")
        self.fields["author_name"].setMinimumWidth(150)
        self.fields["author_rank"] = self._input("Прокурор")
        self.fields["author_rank"].setMinimumWidth(110)
        self.fields["author_id"] = self._input("15902")
        self.fields["author_id"].setMaximumWidth(90)
        grid.addWidget(self._field_label("ФИО"), 1, 5)
        grid.addWidget(self.fields["author_name"], 1, 6)
        grid.addWidget(self._field_label("Должность"), 2, 0)
        grid.addWidget(self.fields["author_rank"], 2, 1)
        grid.addWidget(self._field_label("Удостоверение"), 2, 2)
        grid.addWidget(self.fields["author_id"], 2, 3)
        self.signature_hint = self._label("", "Caption")
        self.signature_hint.setWordWrap(True)
        grid.addWidget(self.signature_hint, 3, 0, 1, 5)
        self.fields["author_name"].textChanged.connect(
            lambda text: self.signature_hint.setText(
                f"Подпись в документе: {pdata.initials_signature(text) or '—'}"))

        # --- папка сохранения: только подпись «Сохранение» и кнопки
        folder_row = QHBoxLayout()
        folder_row.setSpacing(6)
        choose_folder = QPushButton("Папка…")
        choose_folder.setToolTip("Папка, куда сохраняются документы дела")
        choose_folder.clicked.connect(lambda _c=False: self._choose_folder())
        open_folder = QPushButton("Открыть")
        open_folder.clicked.connect(lambda _c=False: self._open_folder())
        folder_row.addWidget(choose_folder)
        folder_row.addWidget(open_folder)
        grid.addLayout(folder_row, 1, 6, 1, 2)
        self.folder_label = self._label("", "Caption")
        self.folder_label.setWordWrap(True)
        self.folder_label.setVisible(False)
        grid.addWidget(self.folder_label, 2, 6, 1, 2)

        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(6, 2)
        return panel

    def _refresh_main_action(self) -> None:
        """Подпись и доступность главной кнопки по текущему этапу (v61)."""
        if not hasattr(self, "form_btn"):
            return
        # v62: подпись кнопки — активный узел («Этап 1» → «Этап 2.1» →
        # «Этап 3»), доступность решает состояние этого узла.
        caption = ogp_stages.node_caption(self.case)
        kind = self._active_node_kind()
        node = ogp_stages.node_state(self.case, kind)
        self.form_btn.setText(caption)
        self.form_btn.setToolTip(node.get("hint", "") or caption)
        self.form_btn.setEnabled(bool(node.get("can_run")))
        if hasattr(self, "chain_badge"):
            self.chain_badge.setText(ogp_stages.overall_badge(self.case))

    def _active_node_kind(self) -> str:
        """Активный узел цепочки: «1», «2.N» или «3» (v62)."""
        state = ogp_stages.stage1_from_outcome(self.case.outcome)
        if not state or ogp_stages.stage1_closes(state):
            return "1"
        number = int(self.case.next_work_step() or 0)
        if number:
            return f"2.{number}"
        return "3"

    def _run_main_action(self) -> None:
        """Главная кнопка: отправить активный узел в ИИ (v62).

        В отличие от v61, эта кнопка НЕ печатает документ: .png собирает
        отдельная кнопка «Сформировать» у каждого этапа/подэтапа (замечание
        пользователя).
        """
        self._collect_case()
        kind = self._active_node_kind()
        if kind == "1":
            self._intake_check()
            return
        self._send_node(kind)

    def _send_node(self, kind) -> None:
        """Заявка по узлу цепочки: контекст всех этапов + ввод прокурора."""
        if not str(self.case.appeal_raw or "").strip():
            QMessageBox.information(self, "Обращение",
                                    "Вставьте текст обращения целиком.")
            return
        if str(kind).startswith("2."):
            self.case.work_step = int(str(kind).split(".")[-1] or 0)
        doc = self._collect()
        step_number = int(str(kind).split(".")[-1]) if str(kind).startswith("2.") else 0
        if kind == "1":
            stage_kind, stage_index, title = (
                cases.STAGE_INTAKE, self.case.stage_number(cases.STAGE_INTAKE),
                "Проверка обращения")
        elif kind == "3":
            stage_kind, stage_index, title = (
                cases.STAGE_CLOSE, self.case.stage_number(cases.STAGE_CLOSE),
                "Итог по обращению")
        else:
            step_title = ""
            for step in self.case.work_plan or []:
                if int(step.get("n") or 0) == step_number:
                    step_title = str(step.get("title", ""))
            stage_kind = cases.STAGE_MEASURE
            stage_index = self.case.stage_number(cases.STAGE_MEASURE)
            title = f"Постановление 2.{step_number} — {step_title}"
        try:
            folder = cases.stage_folder(
                self._documents_root(), doc.get("doc_number") or self.case.number,
                stage_index, stage_kind, case_number=self.case.number)
            request_text = cases.node_request(
                self.case, kind, self._prosecutor_name())
            request_path = folder / "Заявка (отправлено в чат).txt"
            request_path.write_text(request_text, encoding="utf-8")
            self.chat_txt_path = str(request_path)
            appeal_path = ai_request.write_appeal_file(
                folder, str(self.case.appeal_raw or ""))
            self.appeal_txt_path = str(appeal_path)
            self.png_paths = []
            self.cases.save(self.case)
            record = pending_send.build_record(
                kind="document",
                request_text=request_text,
                message_text=ai_request.document_message(
                    request_path.name, appeal_path.name),
                appeal_path=str(appeal_path),
                appeal_text=str(self.case.appeal_raw or ""),
                source_path=str(request_path),
                case_number=str(self.case.number),
                stage_kind=str(kind),
                stage_index=stage_index,
                doc_type=doc.get("doc_type"),
                document_kind="gov",
                title=f"{title} · Обращение №{self.case.number}")
            pending_send.save(self._documents_root(), record)
        except Exception as exc:
            diag.exception("ui.gov.node.pending")
            QMessageBox.warning(self, "Ошибка",
                                f"Не удалось отправить заявку: {exc}")
            return
        self.history.save(doc, draft=True)
        self._refresh_history()
        self.formed.emit(doc, self.chat_txt_path, self.png_paths)
        # v63 (п.3 выгрузки `Постановление.7z`): браузер перезагружается
        # ТОЛЬКО на этапе 1 — чтобы этап 1 шёл в чистом чате и был точным.
        # Подэтапы этапа 2 и этап 3 отправляют заявку в ТЕКУЩИЙ диалог: ИИ
        # должен помнить, что он ответил на этапах 1, 2 и 3, иначе решения
        # разных подэтапов расходятся между собой.
        if str(kind) == "1":
            self.restart_requested.emit(record, str(self.chat_txt_path))
        else:
            self.send_in_chat.emit(record, str(self.chat_txt_path))
        self._mark_request_sent(
            f"Этап {kind}" if str(kind) != "1" else "Этап 1")

    # ------------------------------------------- v62: «Сформировать» для узла

    def _save_node_document(self, kind) -> None:
        """Отдельная кнопка «Сформировать»: .png постановления узла (v62).

        Документ собирается из поля 2 (ответ ИИ) и реквизитов: в ИИ ничего не
        уходит, это только печать.
        """
        state = ogp_stages.node_state(self.case, kind)
        if not state.get("can_save"):
            QMessageBox.information(
                self, "Нечего печатать",
                "По этому этапу ещё нет ответа ИИ. Нажмите кнопку этапа, "
                "дождитесь ответа и повторите.")
            return
        raw = str(self.case.work_node(kind).get("answer", "") or "")
        doc = gdata.parse_gov_answer(raw, base=self._collect())
        if doc is None:
            doc = self._collect()
        self.print_node_document.emit(str(kind), doc, raw)

    def _close_case_requested(self) -> None:
        """«Закрыть обращение» на этапе 3: дело отработано (v63).

        Пользователь: «в конце предусмотреть кнопку „Закрыть обращение“ на
        Этапе 3, по нажатию обращение закрывается и помечается как
        отработанное, и браузер перезагружается».

        Документ при этом не печатается: .png на каждом узле делает своя
        кнопка «Сформировать». Здесь только состояние дела и чистый чат.
        """
        state = ogp_stages.node_state(self.case, "3")
        if not state.get("answer"):
            QMessageBox.information(
                self, "Этап 3 не выполнен",
                "Сначала выполните этап 3: нажмите «Этап 3», дождитесь ответа "
                "ИИ и, если нужно, сформируйте .png.")
            return
        confirm = QMessageBox.question(
            self, "Закрыть обращение?",
            f"Обращение №{self.case.number} будет помечено как отработанное, "
            "браузер перезагрузится с чистым чатом.\n\n"
            "Документы дела останутся в папке обращения.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes)
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self.case.close_work(
            reason="этап 3 выполнен",
            when=datetime.now().strftime("%d.%m.%Y %H:%M"))
        self.cases.save(self.case)
        self._refresh_case_view()
        self.status.setText(
            f"Обращение №{self.case.number} закрыто — отработано")
        self.case_closed.emit(str(self.case.number))

    def _save_current_step(self) -> None:
        number = int(self.case.work_step or self.case.next_work_step() or 0)
        if not number:
            return
        self._save_node_document(f"2.{number}")

    # --------------------------------------------------------- вкладка «Обращение»

    def _build_appeal_tab(self) -> QWidget:
        page = QWidget()
        page.setObjectName("Sheet")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(6)
        top.addWidget(self._field_label("Обращение №"))
        self.fields = {}
        self.texts = {}
        self.fields["case_number"] = self._input("1")
        self.fields["case_number"].setMaximumWidth(90)
        top.addWidget(self.fields["case_number"])
        top.addWidget(self._field_label("Номер с форума"))
        self.fields["appeal_number"] = self._input("б/н")
        self.fields["appeal_number"].setMaximumWidth(140)
        top.addWidget(self.fields["appeal_number"])
        top.addStretch()
        # v61: главная кнопка формы — «Этап 1 / Этап 2 / Этап 3» внизу окна.
        # Эта кнопка остаётся отдельной: ею можно ПЕРЕпроверить обращение,
        # не сбивая цепочку этапа 2 (замечание пользователя: «с возможностью
        # вернуться на 1 этап»).
        self.intake_btn = QPushButton("Вернуться на этап 1")
        self.intake_btn.setToolTip(
            "Перепроверить обращение ИИ: принято к производству или отказано "
            "(возврат на этап 1)")
        self.intake_btn.clicked.connect(lambda _c=False: self._intake_check())
        top.addWidget(self.intake_btn)
        layout.addLayout(top)

        layout.addWidget(self._label("Обращение (вставить целиком)", "Section"))
        self.texts["appeal_raw"] = self._text_area()
        self.texts["appeal_raw"].setPlaceholderText(
            "От: гражданина штата Сан-Андреас Mark Skywalker\n"
            "В: Уважаемый Офис Генерального Прокурора штата Сан-Андреас\n"
            "1. Данные о заявителе: Mark Skywalker 152306\n"
            "2. Предмет обращения: …\n"
            "3. Сведения о нарушителе: Kyria Suspectzx 8332\n"
            "4. Дата и время события: 27.10 00:40\n"
            "5. Подробное описание ситуации: …\n"
            "8. Доказательства правонарушения: тайм-код 00:04 … 02:09 …")
        self.texts["appeal_raw"].textChanged.connect(self._on_appeal_changed)
        self.texts["appeal_raw"].textChanged.connect(self._refresh_ooc_hint)
        layout.addWidget(self.texts["appeal_raw"], 1)

        self.appeal_hint = self._label("", "Caption")
        self.appeal_hint.setWordWrap(True)
        layout.addWidget(self.appeal_hint)

        self.intake_hint = self._label("", "Caption")
        self.intake_hint.setWordWrap(True)
        self.intake_hint.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.intake_hint)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.accept_btn = QPushButton("Принять всё равно")
        self.accept_btn.setToolTip(
            "Прокурор не согласен с отказом ИИ: принять обращение к производству")
        self.accept_btn.clicked.connect(lambda _c=False: self._force_accept())
        row.addWidget(self.accept_btn)
        self.recheck_btn = QPushButton("Проверить ещё раз")
        self.recheck_btn.setToolTip(
            "Отправить ИИ повторную проверку обращения (перепроверка ответа)")
        self.recheck_btn.clicked.connect(lambda _c=False: self._intake_recheck())
        row.addWidget(self.recheck_btn)
        self.recheck_count = self._label("", "Caption")
        row.addWidget(self.recheck_count)
        row.addStretch()
        self.ooc_hint = self._label("", "Caption")
        self.ooc_hint.setWordWrap(True)
        row.addWidget(self.ooc_hint)
        layout.addLayout(row)
        return page

    # ------------------------------------------------ v62: вкладка «Цепочка этапов»

    def _build_chain_tab(self) -> QWidget:
        """Три этапа обращения: этап 1, подэтапы этапа 2, этап 3 (v62).

        Замечание пользователя: «„Цепочка этапов“ должна исполнять функционал
        „Постановление“ — там три этапа по новому ТЗ». Поэтому здесь:
        * этап 1 — три состояния (принятие / отказ / переадресация);
        * этап 2 — до трёх вариантов цепочки от ИИ; активный вариант
          раскладывается подэтапами 2.1, 2.2 …; подэтапы идут строго по
          порядку, порядок можно править вручную и согласовать с ИИ;
        * этап 3 — итог: вид завершения выбирает ИИ по смыслу всех этапов.
        Кнопка этапа одна (внизу окна) и называется по активному узлу;
        .png печатает отдельная кнопка «Сформировать» у каждого блока, а
        индикатор показывает, сформировано постановление или нет.
        """
        page = QWidget()
        page.setObjectName("Sheet")
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        body = QWidget()
        body.setObjectName("Sheet")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        self.chain_hint = self._label("", "Caption")
        self.chain_hint.setWordWrap(True)
        layout.addWidget(self.chain_hint)

        # ---------------------------------------------------------- этап 1
        layout.addWidget(self._label("Этап 1. Решение по обращению", "Section"))
        stage1 = QHBoxLayout()
        stage1.setSpacing(6)
        self.stage1_buttons = {}
        for state in ogp_stages.STAGE1_STATES:
            button = QPushButton(ogp_stages.stage1_label(state))
            button.setToolTip(ogp_stages.STAGE1_HINTS.get(state, ""))
            button.clicked.connect(
                lambda _c=False, value=state: self._set_stage1(value))
            self.stage1_buttons[state] = button
            stage1.addWidget(button)
        stage1.addStretch()
        layout.addLayout(stage1)
        self.stage1_basis = self._label("", "Caption")
        self.stage1_basis.setWordWrap(True)
        layout.addWidget(self.stage1_basis)
        row = QHBoxLayout()
        row.setSpacing(8)
        stage1_save = QVBoxLayout()
        stage1_save.setSpacing(2)
        self.stage1_save_caption = self._label(
            ogp_stages.save_caption("1"), "Caption")
        stage1_save.addWidget(self.stage1_save_caption)
        self.stage1_save = QPushButton("Сформировать")
        self.stage1_save.setToolTip(
            "Напечатать постановление этапа 1 (принятие / отказ / "
            "переадресация) в .png")
        self.stage1_save.clicked.connect(
            lambda _c=False: self._save_node_document("1"))
        stage1_save.addWidget(self.stage1_save)
        row.addLayout(stage1_save)
        self.stage1_png = self._label("", "Caption")
        self.stage1_png.setWordWrap(True)
        row.addWidget(self.stage1_png)
        row.addStretch()
        layout.addLayout(row)

        # ---------------------------------------------------------- этап 2
        layout.addWidget(self._label(
            "Этап 2. Цепочка постановлений по существу", "Section"))
        self.chain_pick = QComboBox()
        self.chain_pick.setToolTip(
            "Варианты цепочки от ИИ. Прогресс каждого варианта сохраняется: "
            "можно вернуться и продолжить")
        self.chain_pick.currentIndexChanged.connect(self._on_chain_picked)
        layout.addWidget(self.chain_pick)
        self.chain_why = self._label("", "Caption")
        self.chain_why.setWordWrap(True)
        layout.addWidget(self.chain_why)

        self.plan_list = QListWidget()
        self.plan_list.setMinimumHeight(150)
        self.plan_list.setToolTip(
            "Подэтапы активной цепочки. Подэтапы идут строго по порядку: "
            "следующий открывается после ответа ИИ по предыдущему.")
        self.plan_list.itemSelectionChanged.connect(
            lambda: self._on_plan_selected())
        layout.addWidget(self.plan_list, 1)

        self.step_hint = self._label("", "Caption")
        self.step_hint.setWordWrap(True)
        layout.addWidget(self.step_hint)

        order_row = QHBoxLayout()
        order_row.setSpacing(6)
        self.step_input = QLineEdit()
        self.step_input.setPlaceholderText("Свой подэтап: «О вызове на допрос свидетеля»")
        self.step_input.setMinimumHeight(28)
        order_row.addWidget(self.step_input, 1)
        self.step_add_btn = QPushButton("Добавить")
        self.step_add_btn.setToolTip("Добавить подэтап в свой порядок")
        self.step_add_btn.clicked.connect(lambda _c=False: self._add_own_step())
        order_row.addWidget(self.step_add_btn)
        self.step_del_btn = QPushButton("Убрать")
        self.step_del_btn.setToolTip("Убрать выбранный подэтап из своего порядка")
        self.step_del_btn.clicked.connect(lambda _c=False: self._remove_step())
        order_row.addWidget(self.step_del_btn)
        self.step_up_btn = QPushButton("Выше")
        self.step_up_btn.clicked.connect(lambda _c=False: self._move_step(-1))
        order_row.addWidget(self.step_up_btn)
        self.step_down_btn = QPushButton("Ниже")
        self.step_down_btn.clicked.connect(lambda _c=False: self._move_step(1))
        order_row.addWidget(self.step_down_btn)
        self.order_btn = QPushButton("Согласовать порядок")
        self.order_btn.setToolTip(
            "Отправить новый порядок ИИ: он пересоберёт оставшиеся подэтапы и "
            "подстроит этап 3. Выполненные подэтапы не меняются")
        self.order_btn.clicked.connect(lambda _c=False: self._send_custom_order())
        order_row.addWidget(self.order_btn)
        layout.addLayout(order_row)

        save2_row = QHBoxLayout()
        save2_row.setSpacing(8)
        step_save = QVBoxLayout()
        step_save.setSpacing(2)
        self.step_save_caption = self._label(ogp_stages.save_caption("2.1"),
                                             "Caption")
        step_save.addWidget(self.step_save_caption)
        self.step_save = QPushButton("Сформировать")
        self.step_save.setToolTip(
            "Напечатать постановление по выбранному подэтапу этапа 2 в .png")
        self.step_save.clicked.connect(
            lambda _c=False: self._save_current_step())
        step_save.addWidget(self.step_save)
        save2_row.addLayout(step_save)
        self.step_png = self._label("", "Caption")
        self.step_png.setWordWrap(True)
        save2_row.addWidget(self.step_png)
        save2_row.addStretch()
        layout.addLayout(save2_row)

        # ---------------------------------------------------------- этап 3
        layout.addWidget(self._label("Этап 3. Итог по обращению", "Section"))
        self.etap3_label = self._label("", "Caption")
        self.etap3_label.setWordWrap(True)
        layout.addWidget(self.etap3_label)
        save3_row = QHBoxLayout()
        save3_row.setSpacing(8)
        stage3_save = QVBoxLayout()
        stage3_save.setSpacing(2)
        self.stage3_save_caption = self._label(
            ogp_stages.save_caption("3"), "Caption")
        stage3_save.addWidget(self.stage3_save_caption)
        self.stage3_save = QPushButton("Сформировать")
        self.stage3_save.setToolTip(
            "Напечатать итоговое постановление этапа 3 в .png")
        self.stage3_save.clicked.connect(
            lambda _c=False: self._save_node_document("3"))
        stage3_save.addWidget(self.stage3_save)
        save3_row.addLayout(stage3_save)
        self.stage3_png = self._label("", "Caption")
        self.stage3_png.setWordWrap(True)
        save3_row.addWidget(self.stage3_png)
        save3_row.addStretch()
        layout.addLayout(save3_row)

        # v63 (п.3): финальная кнопка — «Закрыть обращение». Обращение
        # помечается отработанным, и браузер перезагружается (чистый чат).
        close_row = QHBoxLayout()
        close_row.setSpacing(8)
        close_row.addStretch()
        self.close_case_btn = QPushButton("Закрыть обращение")
        self.close_case_btn.setToolTip(
            "Обращение отработано: дело закрывается, браузер перезагружается "
            "с чистым чатом")
        self.close_case_btn.clicked.connect(
            lambda _c=False: self._close_case_requested())
        close_row.addWidget(self.close_case_btn)
        layout.addLayout(close_row)

        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        return page

    # --------------------------------------------------- v62: состояние цепочки

    def _refresh_chain_view(self) -> None:
        """Перерисовывает три этапа по состоянию дела (v62)."""
        if not hasattr(self, "plan_list"):
            return
        case = self.case
        state = ogp_stages.stage1_from_outcome(case.outcome)
        for key, button in getattr(self, "stage1_buttons", {}).items():
            chosen = state == key
            button.setObjectName("Primary" if chosen else "")
            button.style().unpolish(button)
            button.style().polish(button)
        if hasattr(self, "stage1_basis"):
            basis = str(case.intake_basis or "").strip()
            self.stage1_basis.setText(
                ("Решение ИИ: " + ogp_stages.stage1_label(state) +
                 (" · " + basis if basis else "")) if state else "")
        if hasattr(self, "chain_hint"):
            self.chain_hint.setText(self._chain_hint_text(state))
        self._refresh_chain_picker()
        self._refresh_plan_list()
        self._refresh_save_indicators()
        if hasattr(self, "etap3_label"):
            self.etap3_label.setText(self._stage3_hint(state))

    def _chain_hint_text(self, state: str) -> str:
        case = self.case
        if not state:
            return "Нажмите «Этап 1» — ИИ решит судьбу обращения."
        if ogp_stages.stage1_closes(state):
            return (f"Этап 1: {ogp_stages.stage1_label(state)} — дело закрыто, "
                    "этапы 2 и 3 не наступают. Решение можно изменить кнопками "
                    "выше, тогда цепочка вернётся.")
        if not case.work_chains and not case.work_plan:
            return "Обращение принято, но цепочки этапа 2 ещё нет — выполните этап 1 ещё раз."
        return ""

    def _refresh_chain_picker(self) -> None:
        """Список вариантов цепочки: буква, название и прогресс (v62)."""
        if not hasattr(self, "chain_pick"):
            return
        rows = ogp_stages.chain_progress(self.case)
        self.chain_pick.blockSignals(True)
        self.chain_pick.clear()
        for row in rows:
            self.chain_pick.addItem(
                f"{row['letter']}) {row['title']} — пройдено {row['done']} "
                f"из {row['total']}", row["letter"])
        index = max(0, [row["letter"] for row in rows].index(
            str(self.case.work_letter))) if rows and str(
                self.case.work_letter) in [row["letter"] for row in rows] else 0
        if rows:
            self.chain_pick.setCurrentIndex(index)
        self.chain_pick.setEnabled(bool(rows))
        self.chain_pick.blockSignals(False)
        if hasattr(self, "chain_why"):
            active = self.case.active_chain()
            self.chain_why.setText(str(active.get("why", "") or ""))

    def _refresh_plan_list(self) -> None:
        """Список подэтапов активной цепочки с отметками и индикатором PNG."""
        case = self.case
        self.plan_list.blockSignals(True)
        self.plan_list.clear()
        for step in case.work_plan:
            number = int(step.get("n") or 0)
            node = case.work_node(f"2.{number}")
            answered = bool(str(node.get("answer", "") or "").strip())
            mark = "✓" if answered and node.get("done") else "•"
            parts = [f"{mark} 2.{number}. {step.get('title', '')}"]
            if node.get("png"):
                parts.append("PNG сформирован")
            elif answered:
                parts.append("PNG не сформирован")
            if node.get("stale"):
                parts.append("ввод изменён — прогоните снова")
            text = "  ·  ".join(parts)
            if step.get("why"):
                text += f" — {step.get('why')}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, number)
            if node.get("stale"):
                item.setForeground(QBrush(QColor("#f59e0b")))
            elif answered:
                item.setForeground(QBrush(QColor("#34d399")))
            self.plan_list.addItem(item)
        self.plan_list.blockSignals(False)
        if hasattr(self, "step_hint"):
            self.step_hint.setText(self._step_hint_text())
        number = case.work_step or case.next_work_step()
        self._select_plan_row(number)

    def _step_hint_text(self) -> str:
        case = self.case
        if not case.work_plan:
            return "Цепочки подэтапов пока нет."
        number = case.next_work_step()
        if not number:
            return "Все подэтапы пройдены — можно выполнять этап 3."
        state = ogp_stages.node_state(case, f"2.{number}")
        return state.get("hint", "")

    def _stage3_hint(self, state: str) -> str:
        case = self.case
        if not state or ogp_stages.stage1_closes(state):
            return ""
        node = case.work_node("3")
        if node.get("answer"):
            return "Решение ИИ по этапу 3 получено — нажмите «Сформировать», чтобы напечатать .png."
        if ogp_stages.node_state(case, "3").get("can_run"):
            return "Этап 3 открыт: нажмите «Этап 3» — ИИ сам выберет вид завершения."
        return "Этап 3 откроется после прохождения всех подэтапов этапа 2."

    def _refresh_save_indicators(self) -> None:
        """Кнопки «Сформировать» и индикаторы PNG по каждому узлу (v62)."""
        case = self.case
        state = ogp_stages.stage1_from_outcome(case.outcome)
        self._set_save_state(
            getattr(self, "stage1_save", None),
            getattr(self, "stage1_png", None),
            ogp_stages.node_state(case, "1"),
            ogp_stages.save_caption("1"))
        number = int(case.work_step or case.next_work_step() or 0)
        node = case.work_node(f"2.{number}") if number else {}
        self._set_save_state(
            getattr(self, "step_save", None),
            getattr(self, "step_png", None),
            ogp_stages.node_state(case, f"2.{number}" if number else ""),
            ogp_stages.save_caption(f"2.{number}" if number else "2.1"))
        self._set_save_state(
            getattr(self, "stage3_save", None),
            getattr(self, "stage3_png", None),
            ogp_stages.node_state(case, "3"),
            ogp_stages.save_caption("3"))
        if hasattr(self, "step_save_caption") and number:
            self.step_save_caption.setText(ogp_stages.save_caption(f"2.{number}"))
        if hasattr(self, "order_btn"):
            self.order_btn.setEnabled(bool(case.work_chains or case.work_plan))
        if not state and hasattr(self, "step_save"):
            self.step_save.setEnabled(False)

    def _set_save_state(self, button, indicator, state: dict, caption: str) -> None:
        if button is None:
            return
        button.setEnabled(bool(state.get("can_save")))
        button.setToolTip(
            f"{caption}: " + (state.get("hint", "") or "постановление по ответу ИИ"))
        if indicator is not None:
            if state.get("png"):
                indicator.setText("PNG сформирован")
                indicator.setStyleSheet("color: #34d399; font-size: 11px;")
            elif state.get("answer"):
                indicator.setText("PNG не сформирован")
                indicator.setStyleSheet("color: #f59e0b; font-size: 11px;")
            elif state.get("stale"):
                indicator.setText("Ввод изменён — прогоните этап снова")
                indicator.setStyleSheet("color: #f59e0b; font-size: 11px;")
            else:
                indicator.setText("")
                indicator.setStyleSheet("color: #8f96bf; font-size: 11px;")

    def _select_plan_row(self, number: int) -> None:
        if not hasattr(self, "plan_list"):
            return
        for row in range(self.plan_list.count()):
            item = self.plan_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == number:
                self.plan_list.blockSignals(True)
                self.plan_list.setCurrentItem(item)
                self.plan_list.blockSignals(False)
                return

    def _on_plan_selected(self) -> None:
        """Выбран подэтап: он становится активным, кнопка — «Этап 2.N»."""
        item = self.plan_list.currentItem()
        if item is None:
            return
        number = int(item.data(Qt.ItemDataRole.UserRole) or 0)
        if not number:
            return
        self.case.work_step = number
        self.cases.save(self.case)
        self._sync_right_window()
        self._refresh_case_view()

    def _on_chain_picked(self, index: int) -> None:
        """Выбран другой вариант цепочки — прогресс прежнего сохраняется (v62)."""
        if index < 0 or not hasattr(self, "chain_pick"):
            return
        letter = self.chain_pick.itemData(index)
        if not letter:
            return
        if self.case.use_chain(str(letter)):
            self.cases.save(self.case)
            self._refresh_case_view()
            self._sync_right_window()

    def _set_stage1(self, state: str) -> None:
        """Прокурор выбирает решение этапа 1 вручную (v62)."""
        outcome = {ogp_stages.STATE_ACCEPT: ogp_chain.OUTCOME_ACCEPT,
                   ogp_stages.STATE_REFUSE: ogp_chain.OUTCOME_REFUSE,
                   ogp_stages.STATE_REDIRECT: ogp_chain.OUTCOME_REDIRECT}.get(state)
        if not outcome:
            return
        self.case.set_outcome(outcome)
        if state != ogp_stages.STATE_ACCEPT:
            self.case.work_plan = []
            self.case.work_chains = []
            self.case.work_step = 0
        self.cases.save(self.case)
        self._refresh_case_view()
        self._sync_right_window()
        self.status.setText(f"Этап 1: {ogp_stages.stage1_label(state)}")

    # ------------------------------------------- v62: ручной порядок подэтапов

    def _custom_titles(self) -> list:
        return [str(step.get("title", "")) for step in self.case.work_plan]

    def _add_own_step(self) -> None:
        title = self.step_input.text().strip()
        if not title:
            return
        self.case.add_work_step(title)
        self.case.set_custom_order(self._custom_titles())
        self.cases.save(self.case)
        self.step_input.clear()
        self.status.setText("Подэтап добавлен — нажмите «Согласовать порядок»")
        self._refresh_chain_view()

    def _remove_step(self) -> None:
        item = self.plan_list.currentItem()
        if item is None:
            return
        number = int(item.data(Qt.ItemDataRole.UserRole) or 0)
        if self.case.remove_work_step(number):
            self.case.set_custom_order(self._custom_titles())
            self.cases.save(self.case)
            self.status.setText("Подэтап убран — нажмите «Согласовать порядок»")
            self._refresh_chain_view()

    def _move_step(self, delta: int) -> None:
        """Переставить выбранный подэтап выше/ниже вручную (v62)."""
        item = self.plan_list.currentItem()
        if item is None:
            return
        number = int(item.data(Qt.ItemDataRole.UserRole) or 0)
        plan = [dict(step) for step in self.case.work_plan]
        index = next((pos for pos, step in enumerate(plan)
                      if int(step.get("n") or 0) == number), -1)
        target = index + int(delta)
        if index < 0 or target < 0 or target >= len(plan):
            return
        plan[index], plan[target] = plan[target], plan[index]
        for position, step in enumerate(plan, start=1):
            step["n"] = position
        chain = self.case.active_chain()
        if chain:
            chain["steps"] = [dict(step) for step in plan]
        self.case.work_plan = plan
        self.case.set_custom_order([str(step.get("title", "")) for step in plan])
        self.cases.save(self.case)
        self.status.setText("Порядок изменён — нажмите «Согласовать порядок»")
        self._refresh_chain_view()
        self._select_plan_row(target + 1)

    def _send_custom_order(self) -> None:
        """Отправка нового порядка в ИИ на калибровку (v62)."""
        self._collect_case()
        if not self.case.work_plan:
            return
        self.case.set_custom_order(self._custom_titles())
        doc = self._collect()
        try:
            folder = cases.stage_folder(
                self._documents_root(), doc.get("doc_number") or self.case.number,
                2, cases.STAGE_MEASURE, case_number=self.case.number)
            request_path = folder / "Заявка (порядок цепочки).txt"
            request_path.write_text(
                cases.reorder_request(self.case, self._prosecutor_name()),
                encoding="utf-8")
            appeal_path = ai_request.write_appeal_file(
                folder, str(self.case.appeal_raw or ""))
            record = pending_send.build_record(
                kind="document",
                request_text=request_path.read_text(encoding="utf-8"),
                message_text=ai_request.document_message(
                    request_path.name, appeal_path.name),
                appeal_path=str(appeal_path),
                appeal_text=str(self.case.appeal_raw or ""),
                source_path=str(request_path),
                case_number=str(self.case.number),
                stage_kind="__reorder__",
                stage_index=2,
                doc_type=doc.get("doc_type"),
                document_kind="gov",
                title=f"Порядок цепочки №{self.case.number}")
            pending_send.save(self._documents_root(), record)
            self.cases.save(self.case)
        except Exception as exc:
            diag.exception("ui.gov.order.pending")
            QMessageBox.warning(self, "Ошибка",
                                f"Не удалось отправить порядок: {exc}")
            return
        # v63: калибровка порядка идёт в текущем диалоге — ИИ должен видеть
        # свои ответы по пройденным подэтапам, иначе новый порядок их «забудет».
        self.send_in_chat.emit(record, str(request_path))
        self._mark_request_sent("Порядок цепочки")

    # ------------------------------------------- v62: окно «Обстоятельства»

    def _build_work_window(self) -> QDialog:
        """Отдельное окно «Обстоятельства» слева от формы (v62).

        Пользователь: «теперь справа от главного окна… для каждого этапа будет
        своя „Обстоятельства“». В окне выбирается этап/подэтап и видны два поля:
        * поле 1 — ввод прокурора (правится; при изменении помечает ответ ИИ
          устаревшим и блокирует переход к следующему подэтапу);
        * поле 2 — ответ ИИ (только чтение) в том виде, как он встанет в
          документ: шапка, УСТАНОВИЛ, ПОСТАНОВИЛ.
        Окно не модальное: прокурор работает с формой и видит обстоятельства.
        """
        window = QDialog(self)
        window.setWindowTitle("Обстоятельства")
        window.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowStaysOnTopHint
        )
        window.setStyleSheet(_dark_style())
        window.setMinimumSize(520, 600)
        layout = QVBoxLayout(window)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)
        head = QHBoxLayout()
        head.addWidget(self._label("Обстоятельства", "Title"))
        head.addStretch()
        self.facts_close_btn = QPushButton("Скрыть")
        self.facts_close_btn.setToolTip(
            "Скрыть окно (вернуть — кнопкой «Обстоятельства» в форме)")
        self.facts_close_btn.clicked.connect(lambda _c=False: window.hide())
        head.addWidget(self.facts_close_btn)
        layout.addLayout(head)

        self.facts_node = QComboBox()
        self.facts_node.setToolTip("Этап или подэтап, чьи обстоятельства смотреть")
        self.facts_node.currentIndexChanged.connect(lambda _i: self._on_facts_node())
        layout.addWidget(self.facts_node)

        layout.addWidget(self._label("Что стало известно (ввод прокурора)", "Section"))
        self.facts_input = self._text_area(
            "Что установлено по этому этапу: показания, записи, документы", 120)
        self.facts_input.textChanged.connect(self._on_facts_input_changed)
        layout.addWidget(self.facts_input, 2)

        self.facts_status = self._label("", "Caption")
        self.facts_status.setWordWrap(True)
        layout.addWidget(self.facts_status)

        layout.addWidget(self._label("Ответ ИИ (только чтение)", "Section"))
        self.facts_answer = self._text_area("", 160)
        self.facts_answer.setReadOnly(True)
        layout.addWidget(self.facts_answer, 3)

        self.facts_save_btn = QPushButton("Сохранить обстоятельства")
        self.facts_save_btn.setToolTip(
            "Записать ввод прокурора в дело (затем прогоните этап через ИИ)")
        self.facts_save_btn.clicked.connect(lambda _c=False: self._save_facts())
        layout.addWidget(self.facts_save_btn)
        return window

    def _sync_right_window(self) -> None:
        """Обновляет окно обстоятельств по выбранному узлу (v62)."""
        window = getattr(self, "facts_window", None)
        if window is None:
            return
        self._refresh_facts_nodes()
        self._on_facts_node()

    def _refresh_facts_nodes(self) -> None:
        if not hasattr(self, "facts_node"):
            return
        case = self.case
        current = self.facts_node.currentData()
        self.facts_node.blockSignals(True)
        self.facts_node.clear()
        state = ogp_stages.stage1_from_outcome(case.outcome)
        self.facts_node.addItem("Этап 1 — решение по обращению", "1")
        for chain in case.work_chains or []:
            letter = str(chain.get("letter", ""))
            if letter != str(case.work_letter):
                continue
            for index, step in enumerate(chain.get("steps") or [], start=1):
                self.facts_node.addItem(
                    f"Этап 2.{index} — {step.get('title', '')}",
                    f"2.{index}")
        if state and not ogp_stages.stage1_closes(state):
            self.facts_node.addItem("Этап 3 — итог по обращению", "3")
        if current:
            position = self.facts_node.findData(current)
            if position >= 0:
                self.facts_node.setCurrentIndex(position)
        self.facts_node.blockSignals(False)

    def _on_facts_node(self) -> None:
        """Показывает поле 1 и поле 2 выбранного этапа (v62)."""
        kind = self.facts_node.currentData() if hasattr(self, "facts_node") else ""
        kind = str(kind or "1")
        node = self.case.work_node(kind)
        self.facts_input.blockSignals(True)
        self.facts_input.setPlainText(str(node.get("input", "") or ""))
        self.facts_input.blockSignals(False)
        self.facts_answer.setPlainText(
            self._pretty_node_answer(kind, str(node.get("answer", "") or "")))
        self._refresh_facts_status(kind, node)
        # Окно синхронизируется с формой: выбранный узел становится активным.
        parsed = ogp_stages.parse_node(kind)
        if self.case.work_chains and parsed["etap"] == 2 and parsed["step"]:
            if int(self.case.work_step or 0) != parsed["step"]:
                self.case.work_step = parsed["step"]
                self.cases.save(self.case)
                self._refresh_case_view()

    def _refresh_facts_status(self, kind: str, node: dict) -> None:
        if not hasattr(self, "facts_status"):
            return
        if node.get("stale"):
            text = "Ввод изменён после ответа ИИ — прогоните этот этап снова."
            color = "#f59e0b"
        elif node.get("answer"):
            text = ("Ответ ИИ получен" +
                    ("; PNG сформирован" if node.get("png")
                     else "; PNG не сформирован"))
            color = "#34d399"
        else:
            state = ogp_stages.node_state(self.case, kind)
            text = state.get("hint", "")
            color = "#8f96bf"
        self.facts_status.setText(text)
        self.facts_status.setStyleSheet(f"color: {color}; font-size: 11px;")

    def _pretty_node_answer(self, kind: str, answer: str) -> str:
        """Ответ ИИ в виде документа (v62)."""
        if not answer:
            return ""
        doc = None
        try:
            doc = gdata.parse_gov_answer(answer)
        except Exception:
            diag.exception("ui.gov.pretty_answer")
        return ogp_stages.pretty_answer(doc, answer)

    def _on_facts_input_changed(self) -> None:
        """Правка поля 1: сразу в дело, ответ ИИ помечается устаревшим (v62)."""
        if not hasattr(self, "facts_input") or self.facts_input.signalsBlocked():
            return
        kind = str(self.facts_node.currentData() or "1") \
            if hasattr(self, "facts_node") else "1"
        if self.case.set_node_input(kind, self.facts_input.toPlainText()):
            self.cases.save(self.case)
            self._refresh_facts_status(kind, self.case.work_node(kind))
            self._refresh_case_view()

    def _save_facts(self) -> None:
        kind = str(self.facts_node.currentData() or "1") \
            if hasattr(self, "facts_node") else "1"
        self.case.set_node_input(kind, self.facts_input.toPlainText())
        self.cases.save(self.case)
        self.status.setText("Обстоятельства сохранены")
        self._refresh_case_view()

    def _toggle_facts_window(self) -> None:
        """Показать/скрыть окно обстоятельств (v62)."""
        window = getattr(self, "facts_window", None)
        if window is None:
            self.facts_window = self._build_work_window()
            window = self.facts_window
        if window.isVisible():
            window.hide()
            return
        self._sync_right_window()
        self._place_facts_window()
        window.show()
        window.raise_()

    def _place_facts_window(self) -> None:
        """Ставит окно обстоятельств слева от формы (v62)."""
        window = getattr(self, "facts_window", None)
        if window is None:
            return
        geometry = self.frameGeometry()
        screen = self.screen().availableGeometry() if self.screen() else None
        width = max(460, geometry.width() // 2)
        window.resize(width, max(560, geometry.height()))
        left = geometry.left() - window.width() - 12
        if screen is not None and left < screen.left():
            left = geometry.right() + 12
            if screen is not None and left + window.width() > screen.right():
                left = max(screen.left(), geometry.left())
        window.move(left, geometry.top())

    def _prosecutor_name(self) -> str:
        widget = self.fields.get("author_name") if hasattr(self, "fields") else None
        return widget.text().strip() if widget is not None else ""

    # ------------------------------------------------------ вкладка «История»

    def _build_history_tab(self) -> QWidget:
        page = QWidget()
        page.setObjectName("Sheet")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        layout.addWidget(self._label(
            "Сохранённые постановления: «Загрузить в форму» открывает запись, "
            "«Удалить» убирает выбранную, «Очистить историю» — все.", "Caption"))
        self.history_list = QListWidget()
        self.history_list.itemDoubleClicked.connect(lambda _i: self._load_history())
        layout.addWidget(self.history_list, 1)
        row = QHBoxLayout()
        refresh = QPushButton("Обновить")
        refresh.clicked.connect(lambda _c=False: self._refresh_history())
        self.history_delete_btn = QPushButton("Удалить")
        self.history_delete_btn.setToolTip("Удалить выбранное постановление из истории")
        self.history_delete_btn.clicked.connect(
            lambda _c=False: self._delete_history())
        self.history_clear_btn = QPushButton("Очистить историю")
        self.history_clear_btn.setToolTip("Удалить все записи истории")
        self.history_clear_btn.clicked.connect(
            lambda _c=False: self._clear_history())
        load = QPushButton("Загрузить в форму")
        load.setObjectName("Primary")
        load.clicked.connect(lambda _c=False: self._load_history())
        row.addWidget(refresh)
        row.addWidget(self.history_delete_btn)
        row.addWidget(self.history_clear_btn)
        row.addStretch()
        row.addWidget(load)
        layout.addLayout(row)
        self._refresh_history()
        return page

    # -------------------------------------------------- этап и вид решения (v39–v42)

    def _fill_types(self, stage: str) -> None:
        """v62.3: список «Вид решения» из формы убран вместе с вкладкой.

        Замечание пользователя: «„Цепочка этапов“ должна исполнять функционал
        „Постановление“». Вкладок «Постановление» и «Обстоятельства» в v62 нет,
        комбобоксов этапа и вида решения форма больше не создаёт, а этот метод
        остался от v43 и обращался к удалённому комбобоксу — из-за этого
        падало «Новое обращение» (лог 20261125-194436-20848-ebcda6:
        `AttributeError` в `new_case` → `_fill_types`). Метод сохранён как
        точка совместимости: список видов решений теперь показывает заявку
        ИИ, а вид решения берётся из ответа по узлу цепочки.
        """
        return

    def _on_stage_changed(self) -> None:
        """v62.3: этап документа берётся из узла цепочки, а не из комбобокса.

        Комбобокса «Этап» в форме больше нет (вкладка «Постановление» удалена
        в v62), поэтому смена этапа — это выбор узла в «Цепочке этапов».
        Метод оставлен, чтобы старые вызовы (в том числе из `main.py` и
        подокна обстоятельств) не падали с `AttributeError`.
        """
        self._on_stage_kind_changed()

    def _on_type_changed(self, text: str) -> None:
        """v62.3: «Вид решения» больше не выбирается вручную.

        Значение приходит из ответа ИИ по узлу (`ogp_stages.node_state`), форма
        хранит его в `self.document`. Ничего не делаем, если вид решения
        пришёл непустым: он уже записан в документ при разборе ответа.
        """
        text = (text or "").strip()
        if text:
            self._set_field("doc_type", text)

    # ------------------------------------------------------ дело: состояние, этапы

    def _stage_kind(self) -> str:
        """Вид этапа дела по активному узлу цепочки (v62.3).

        Раньше этап брался из комбобокса «Этап» вкладки «Постановление»
        (v43, v49). В v62 этой вкладки нет — этап определяется узлом:
        «1» — первичное решение, «2.N» — санкция по существу, «3» — итог.
        """
        return cases.stage_kind_for_node(self._active_node_kind())

    def _stage_index(self) -> int:
        """Номер этапа для файлов — постоянный для вида этапа (v43).

        Раньше номер считался по количеству выполненных этапов, поэтому после
        «Проверки ИИ» и «Сформировать» этап 1 превращался в этап 2.
        """
        return self.case.stage_number(self._stage_kind())

    def _select_stage(self, kind: str, keep_user_choice: bool = False) -> None:
        """v62.3: выбор этапа = выбор узла цепочки, комбобокса в форме нет.

        Оставлено для совместимости со старыми вызовами (`new_case`,
        `open_case`, `main.py`): запоминаем вид этапа в деле и перерисовываем
        «Цепочку этапов».
        """
        current = self._stage_kind()
        if keep_user_choice and current and current != kind:
            return
        self.case.set_stage_kind(kind)
        self._refresh_chain_view()

    def _refresh_stage_hint(self) -> None:
        """v62: подсказка этапа убрана — длинных пояснений в форме нет."""
        return

    def _on_stage_kind_changed(self) -> None:
        """Этап сменился: запоминаем его в деле и обновляем вид (v62.3).

        Раньше здесь подстраивался комбобокс «Этап» вкладки «Постановление».
        Вкладки больше нет, поэтому этап хранится в деле (`stage_kind`), а
        единственное место выбора этапа — узлы «Цепочки этапов».
        """
        kind = self._stage_kind()
        self.case.set_stage_kind(kind)
        self.cases.save(self.case)
        self._refresh_stage_hint()
        self._refresh_case_view()

    def _add_stage(self) -> None:
        """Добавляет выбранный этап в цепочку дела (v49: вкладка «Постановление»)."""
        kind = self._stage_kind()
        self.case.add_stage(kind)
        self.cases.save(self.case)
        self._refresh_case_view()
        self.status.setText(f"Этап добавлен: {cases.stage_title(kind)}")

    def _refresh_case_view(self) -> None:
        """Подписи дела: номер, состояние, доступность полей и этапов (v42)."""
        if hasattr(self, "case_title"):
            self.case_title.setText(f"Обращение №{self.case.number or '—'}")
        if hasattr(self, "status_badge"):
            self.status_badge.setText(self.case.status_label())
            self.status_badge.setStyleSheet(
                f"color: {self.case.status_color()}; font-size: 12px; font-weight: 700;")
        if hasattr(self, "fields") and "case_number" in self.fields:
            self.fields["case_number"].setText(str(self.case.number or ""))
            self.fields["appeal_number"].setText(str(self.case.appeal_number or ""))
        # v62 (замечание пользователя): длинные подсказки-абзацы из формы
        # убраны — под полями остаются только короткие статусы.
        for name in ("accept_btn", "recheck_btn"):
            button = getattr(self, name, None)
            if button is not None:
                button.setVisible(self.case.refused() or self.case.status in
                                  (cases.STATUS_NEW, cases.STATUS_CLOSED))
        # v61: доступность и подпись главной кнопки целиком определяет
        # `_refresh_main_action()` — прежняя проверка «Проверка ИИ сделана»
        # больше не нужна (она и была причиной двух кнопок).
        self._refresh_stage_hint()
        # v60: после смены исхода этапа 1 перерисовываем цепочку — у отказа
        # этапы 2 и 3 не наступают, у принятия появляется план шагов.
        self._refresh_chain_view()
        # v61: подпись главной кнопки («Этап 1 / Этап 2 / Этап 3») зависит от
        # того же состояния дела.
        self._refresh_main_action()

    def _on_appeal_changed(self) -> None:
        """Подсказка у поля «Обращение» (v37, v42, v49).

        v49 (замечание пользователя): «убрать локальные условия распознавания
        реквизитов — например, фразу „Распознано: заявитель …“. Все данные
        определяет ИИ: по „Проверке ИИ“ он отдаёт ключевые слова, парсим их из
        ответа и подставляем в программе; пользователь может менять реквизиты
        местами, что ломает фильтры».

        Поэтому здесь больше нет разбора текста обращения: показываем только
        то, что ИИ уже назвал ключевыми словами, либо просим выполнить
        «Проверку ИИ».

        v63 (п.1 выгрузки `Постановление.7z`): «Вставил Обращение — пытаюсь
        нажать „Этап 1“, а она недоступна». Причина была в том, что текст
        обращения записывался в дело ТОЛЬКО по этому сигналу, а доступность
        главной кнопки считалась по делу: вставил текст и сразу нажал — дело
        ещё пустое, кнопка выключена. Теперь текст пишется в дело здесь же и
        сразу пересчитывается доступность кнопки.
        """
        self.case.appeal_raw = self.texts["appeal_raw"].toPlainText()
        raw = self.case.appeal_raw
        self._refresh_main_action()
        if not hasattr(self, "appeal_hint"):
            return
        if not raw.strip():
            self.appeal_hint.setText(
                "Вставьте обращение целиком и нажмите «Этап 1»: реквизиты "
                "определит ИИ.")
            self.appeal_hint.setStyleSheet("color: #8f96bf; font-size: 11px;")
            return
        found = self._keyword_summary()
        if found:
            self.appeal_hint.setText(
                "Реквизиты из ответа ИИ: " + "; ".join(found) + ".")
            self.appeal_hint.setStyleSheet("color: #34d399; font-size: 11px;")
            return
        self.appeal_hint.setText(
            "Реквизиты определит ИИ: нажмите «Этап 1» — заявитель, "
            "нарушитель, дата события и предмет придут ключевыми словами в "
            "ответе.")
        self.appeal_hint.setStyleSheet("color: #8f96bf; font-size: 11px;")

    def _keyword_summary(self) -> list:
        """Короткая сводка реквизитов, названных ИИ (v49)."""
        fields = dict(getattr(self.case, "intake_fields", {}) or {})
        found = []
        applicant = self._clean(fields.get("applicant_name", ""))
        if applicant:
            ident = self._clean(fields.get("applicant_id", ""))
            found.append(f"заявитель {applicant}{' ' + ident if ident else ''}")
        subject = self._clean(fields.get("subject_name", ""))
        if subject:
            found.append(f"нарушитель {subject}")
        when = self._clean(fields.get("event_date", ""))
        if when:
            found.append(f"событие {when}")
        return found

    # ------------------------------------------------------------- проверка ИИ

    def _intake_check(self) -> None:
        """«Проверка ИИ»: отправляет обращение и ждёт служебный блок (v42)."""
        raw = self.texts["appeal_raw"].toPlainText().strip()
        if not raw:
            QMessageBox.information(self, "Обращение",
                                    "Вставьте текст обращения целиком.")
            return
        self.case.appeal_raw = raw
        number = self.fields["appeal_number"].text().strip()
        if number:
            self.case.appeal_number = number
        self.cases.save(self.case)
        self.intake_pending = True
        self.status.setText("Проверка обращения: перезапуск с чистым чатом…")
        text = cases.intake_request(self.case, self.fields["author_name"].text().strip())
        # v43.2: заявка НЕ уходит сразу. Клиент перезапускается, чат
        # поднимается заново (без старых записей и ответов), и уже после этого
        # запрос отправляется — так решил пользователь.
        folder = self.cases.folder(self.case.number)
        try:
            request_path = folder / "Проверка обращения (заявка).txt"
            request_path.write_text(text, encoding="utf-8")
            # v50 (замечание пользователя): текст обращения экспортируется в
            # отдельный файл «Обращение.txt» и прикладывается к чату; в поле
            # запроса остаётся только короткая служебная фраза.
            appeal_path = ai_request.write_appeal_file(
                folder, str(self.case.appeal_raw or ""))
            record = pending_send.build_record(
                kind="intake", request_text=text,
                message_text=ai_request.intake_message(appeal_path.name),
                appeal_path=str(appeal_path),
                appeal_text=str(self.case.appeal_raw or ""),
                source_path=str(request_path),
                case_number=str(self.case.number),
                stage_kind=cases.STAGE_INTAKE,
                stage_index=self.case.stage_number(cases.STAGE_INTAKE),
                document_kind="gov",
                title=f"Проверка обращения №{self.case.number}")
            pending_send.save(self._documents_root(), record)
        except Exception:
            diag.exception("ui.gov.intake.pending")
            QMessageBox.warning(
                self, "Не удалось сохранить заявку",
                "Заявка на проверку обращения не сохранена, отправка отменена.")
            self.intake_pending = False
            return
        self.intake_hint.setText(
            "Заявка сохранена. Чат перезапустится с чистым диалогом, приложит "
            "«Обращение.txt» и файл заявки и сам отправит короткое сообщение — "
            "ответ придёт служебным блоком «ПРОВЕРКА ОБРАЩЕНИЯ», а это окно "
            "обновится по нему.")
        self.intake_hint.setStyleSheet("color: #7c83ff; font-size: 11px;")
        self.restart_requested.emit(record, str(request_path))
        # v49 (замечание пользователя): «кнопки „Проверка ИИ“, „Сформировать“
        # и схожие не должны закрывать окно „Постановление Обращение №“ —
        # форма должна обновляться по отправленному запросу и полученному
        # ответу ИИ». Раньше здесь стоял `self.accept()`: окно исчезало, и
        # прокурор не видел ни заявки, ни ответа. Теперь окно остаётся
        # открытым и ждёт ответ (`intake_pending`).
        self._mark_request_sent("Проверка ИИ")

    def _mark_request_sent(self, what: str) -> None:
        """Отмечает в форме, что заявка ушла и ждём ответ ИИ (v49).

        Окно остаётся открытым (раньше `self.accept()` его закрывал), поэтому
        пользователь видит, что запрос отправлен, и не нажимает кнопку второй
        раз. Снимается отметка ответом ИИ (`note_answer_received`).
        """
        self.waiting_for_answer = str(what or "")
        self.status.setText(
            f"{what}: заявка отправлена. Окно останется открытым — оно "
            "обновится по ответу ИИ.")
        if hasattr(self, "form_btn"):
            self.form_btn.setEnabled(False)
            self.form_btn.setToolTip(
                "Ждём ответ ИИ: после него окно обновится, и этап можно "
                "будет выполнить снова")
        if hasattr(self, "intake_btn"):
            self.intake_btn.setToolTip(
                "Ждём ответ ИИ: окно обновится автоматически")
        if hasattr(self, "intake_hint") and what == "Постановление":
            self.intake_hint.setText(
                "Заявка отправлена. Окно не закрывается: ответ ИИ обновит "
                "«Обстоятельства» и цепочку этапов.")
            self.intake_hint.setStyleSheet("color: #7c83ff; font-size: 11px;")

    def note_answer_received(self, text: str = "") -> None:
        """Ответ ИИ получен: форма снова готова к работе (v49).

        Вызывается из main при разборе ответа (`_apply_gov_intake` и
        `_apply_gov_answer`). Окно при этом НЕ закрывается и НЕ перезапускается:
        обновляются только поля, этап и статус.
        """
        self.waiting_for_answer = ""
        if text and hasattr(self, "intake_hint"):
            self.intake_hint.setTextFormat(Qt.TextFormat.PlainText)
        # v61: после ответа ИИ подпись кнопки и её доступность пересчитывает
        # ogp_flow (этап 1 → этап 2 → этап 3).
        self._refresh_main_action()
        self.status.setText("Ответ ИИ получен — форма обновлена")

    def _intake_recheck(self) -> None:
        """Повторная проверка: ИИ анализирует обращение ещё раз (v42)."""
        if not self.texts["appeal_raw"].toPlainText().strip():
            return
        self.case.intake_checks = int(getattr(self.case, "intake_checks", 0) or 0)
        self._intake_check()
        self.recheck_count.setText(
            "Отправлена повторная проверка: ИИ перечитает обращение.")

    def _force_accept(self) -> None:
        """Прокурор не согласен с отказом ИИ и принимает обращение сам (v42)."""
        if not self.case.appeal_raw.strip():
            return
        self.case.apply_intake({"state": cases.INTAKE_ACCEPTED,
                                "basis": "решение прокурора вопреки ответу ИИ",
                                "raw": self.case.intake_answer}, force_accept=True)
        self.cases.save(self.case)
        self._refresh_case_view()
        self.intake_hint.setText(
            "Обращение принято решением прокурора (ИИ отказал). "
            "Основание отказа ИИ сохранено в деле.")
        self.intake_hint.setStyleSheet("color: #34d399; font-size: 11px;")
        self.tabs.setCurrentIndex(1)

    def apply_intake_answer(self, text: str) -> bool:
        """Вызывается из main, когда ответ ИИ на проверку пришёл (v42).

        Возвращает True, если состояние дела изменилось. Блок не распознан —
        состояние не трогаем: пользователь увидит подсказку и сможет нажать
        «Проверить ещё раз».
        """
        self.intake_pending = False
        result = cases.parse_intake_answer(text)
        if not result.get("ok"):
            self.intake_hint.setText(
                "В ответе ИИ нет служебного блока «ПРОВЕРКА ОБРАЩЕНИЯ» — "
                "состояние дела не изменено. Нажмите «Проверить ещё раз».")
            self.intake_hint.setStyleSheet("color: #f59e0b; font-size: 11px;")
            return False
        self.case.apply_intake(result)
        # v60: служебное слово ИИ переводится в исход этапа 1 (пять вариантов
        # блок-схемы), а отдельный блок «ПЛАН ЭТАПА 2» — в цепочку шагов.
        outcome = {
            cases.INTAKE_ACCEPTED: ogp_chain.OUTCOME_ACCEPT,
            cases.INTAKE_DEFECTS: ogp_chain.OUTCOME_DEFECTS,
            cases.INTAKE_REFUSED: ogp_chain.OUTCOME_REFUSE,
            cases.INTAKE_REDIRECTED: ogp_chain.OUTCOME_REDIRECT,
            cases.INTAKE_TERMINATED: ogp_chain.OUTCOME_STOP,
        }.get(result.get("state", ""))
        if outcome:
            previous = self.case.outcome
            self.case.set_outcome(outcome)
            # План шагов ОБНОВЛЯЕМ только у принятого обращения: при отказе
            # или прекращении цепочка этапа 2 не имеет смысла (ТЗ, п. 2.1).
            steps = ogp_chain.parse_plan(text)
            if outcome == ogp_chain.OUTCOME_ACCEPT:
                added = self.case.apply_plan(steps)
                if added:
                    self.status.setText(f"План этапа 2: шагов {added}")
            elif previous == ogp_chain.OUTCOME_ACCEPT:
                self.case.work_plan = []
                self.case.work_step = 0
        self.cases.save(self.case)
        # v49: окно не закрывается — обновляем подсказку реквизитов по
        # ключевым словам ответа ИИ.
        self._on_appeal_changed()
        if result["state"] == cases.INTAKE_ACCEPTED:
            self.intake_hint.setText(
                f"ИИ: обращение принято к производству. Основание: "
                f"{result.get('basis') or '—'}. Дело в работе — переходите к "
                "«Обстоятельствам».")
            self.intake_hint.setStyleSheet("color: #34d399; font-size: 11px;")
            # v43: вкладку открываем, но этап НЕ переключаем — прокурор сам
            # решает, каким документом продолжить.
            self.tabs.setCurrentIndex(1)
        else:
            self.intake_hint.setText(
                f"ИИ: {result['state']}. Основание: {result.get('basis') or '—'}. "
                "Можно проверить ещё раз или принять обращение вопреки ответу ИИ.")
            self.intake_hint.setStyleSheet("color: #f59e0b; font-size: 11px;")
        self.note_answer_received()
        self._refresh_case_view()
        self._refresh_chain_view()
        return True

    # -------------------------------------------------------------- открыть дело

    def _open_case_dialog(self) -> None:
        """«Открыть обращение №X»: тот же список дел, что и на старте (v43)."""
        dialog = GovCasesDialog(self.cases, parent=self,
                                title="Открыть обращение")
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if dialog.new_requested:
            self.new_case()
            return
        if dialog.selected_case is not None:
            self.open_case(dialog.selected_case)

    def new_case(self) -> None:
        """Новое обращение: чистое дело, пустое поле и сброс подсказок (v43)."""
        self.case = cases.GovCase(number=self.cases.next_number())
        self.cases.save(self.case)
        self.texts["appeal_raw"].blockSignals(True)
        self.texts["appeal_raw"].clear()
        self.texts["appeal_raw"].blockSignals(False)
        for key in ("facts_video", "facts_for", "facts_against", "proposal",
                    "own_decision"):
            widget = self.texts.get(key)
            if widget is not None:
                widget.blockSignals(True)
                widget.clear()
                widget.blockSignals(False)
        self.fields["case_number"].setText(str(self.case.number or ""))
        self.fields["appeal_number"].clear()
        self.intake_hint.setText(
            "Вставьте обращение и нажмите «Проверка ИИ»: состояние дела "
            "обновится по ответу ИИ.")
        self.intake_hint.setStyleSheet("color: #8f96bf; font-size: 11px;")
        self.recheck_count.clear()
        self._fill_types(gdata.GOV_STAGE_PRIMARY)
        self._select_stage(cases.STAGE_INTAKE)
        self._on_appeal_changed()
        self._refresh_case_view()
        self.status.setText(f"Новое обращение №{self.case.number}")
        self.tabs.setCurrentIndex(0)

    def apply_answer_texts(self, doc: gdata.PostanovlenieProkurora | None = None) -> None:
        """Ответ ИИ → блоки «Обстоятельства» формы (v56).

        Замечание пользователя: «можешь её заполнять данными ИИ после
        „Сформировать“, но всё, что я пишу в Обстоятельства сам, попасть не
        должно… в .png идёт материал, который получен от ИИ».

        Поэтому после ответа ИИ блоки формы заполняются ТЕКСТОМ ИИ:

        * «а) Что видно на видеофиксации» ← раздел «УСТАНОВИЛ» из ответа;
        * «г) Решение прокурора» ← пункты «ПОСТАНОВИЛ» из ответа;
        * «б) Доводы за / против» и «в) Предлагаемое наказание» не трогаются:
          ИИ не делит свой ответ на эти части.

        Что именно записал ИИ, запоминается в деле (`ai_facts`): если
        пользователь поправит блок, в следующий запрос уйдёт только его
        правка, а не текст ИИ повторно.
        """
        if doc is None:
            return
        written = {}
        established = str(doc.get("established") or "").strip()
        if established:
            widget = self.texts.get("facts_video")
            if widget is not None:
                widget.blockSignals(True)
                widget.setPlainText(established)
                widget.blockSignals(False)
            written["facts_video"] = established
        points = [str(point).strip() for point in (doc.points or []) if str(point).strip()]
        if points:
            decision = "\n".join(f"{i}. {point}" for i, point in enumerate(points, 1))
            widget = self.texts.get("own_decision")
            if widget is not None:
                widget.blockSignals(True)
                widget.setPlainText(decision)
                widget.blockSignals(False)
            written["own_decision"] = decision
        if written:
            for key, value in written.items():
                setattr(self.case, key, value)
            self.case.remember_ai_facts(written)
            try:
                self.cases.save(self.case)
            except Exception:
                diag.exception("ui.gov.save_case_facts")
            diag.event("gov.answer.facts_written", keys=sorted(written))

    def _clear_facts(self) -> None:
        """Кнопка «Очистить обстоятельства» (v56)."""
        for key in cases.GovCase.FACTS_KEYS:
            widget = self.texts.get(key)
            if widget is not None:
                widget.blockSignals(True)
                widget.setPlainText("")
                widget.blockSignals(False)
        self.case.clear_facts()
        try:
            self.cases.save(self.case)
        except Exception:
            diag.exception("ui.gov.clear_facts")
        # v62.3: подсказки-абзацы из формы убраны, `work_hint` больше нет
        # (поле проверялось через `hasattr`, поэтому вреда не было, но и
        # смысла держать мёртвую ссылку нет).
        diag.event("gov.facts.cleared")
        self._refresh_case_view()

    def refresh_document(self, doc: gdata.PostanovlenieProkurora | None = None) -> None:
        """Показывает в форме постановление, собранное из ответа ИИ (v49).

        Замечание пользователя: «окно „Постановление Обращение №“ не должно
        закрываться по „Проверке ИИ“ и „Сформировать“ — форма должна
        обновляться по отправленному запросу и полученному ответу ИИ». Поэтому
        после ответа ИИ форма остаётся открытой: поля «Постановления»
        заполняются значениями из ответа, этап и статус перечитываются.
        """
        if doc is not None:
            try:
                self.document = gdata.PostanovlenieProkurora.from_dict(doc.to_dict())
            except Exception:
                diag.exception("ui.gov.refresh_document")
            else:
                for key in ("doc_type", "doc_number", "doc_date", "author_name",
                            "author_rank", "author_id", "city", "violation_act",
                            "violation_clause", "legal_basis", "penalty"):
                    widget = self.fields.get(key)
                    value = self.document.get(key)
                    if widget is not None and value:
                        widget.setText(value)
                legal = self.texts.get("legal_basis")
                if legal is not None and self.document.get("legal_basis"):
                    legal.blockSignals(True)
                    legal.setPlainText(self.document.get("legal_basis"))
                    legal.blockSignals(False)
        self.note_answer_received()
        self._refresh_case_view()

    def refresh_case(self, case: cases.GovCase | None = None) -> None:
        """Перечитывает дело после ответа ИИ, не сбивая вкладку и этап (v43).

        Нужно, когда ответ на «Проверку ИИ» приходит при открытой форме: статус
        и кнопки обновляются, а выбранный пользователем этап и текущая вкладка
        остаются на месте (замечание про «этап 1 → этап 2»).
        """
        latest = case
        if latest is None:
            latest = self.cases.load(str(self.case.number or ""))
        if latest is None:
            return
        self.case = latest
        self.fields["case_number"].setText(str(latest.number or ""))
        self.fields["appeal_number"].setText(str(latest.appeal_number or ""))
        if latest.intake_answer:
            self.intake_hint.setText(
                f"Решение ИИ по обращению: {latest.status_label()}. Основание: "
                f"{latest.intake_basis or '—'}.")
            self.intake_hint.setStyleSheet("color: #8f96bf; font-size: 11px;")
        # v49: если форма ждала ответ на «Проверку ИИ» — он получен, форма
        # снова готова к работе. Окно при этом остаётся открытым.
        if self.waiting_for_answer == "Проверка ИИ":
            self.note_answer_received()
        self._refresh_case_view()
        # v63 (п.3): «если „В процессе“ — то есть решение „принятие“ — переход
        # в поле „Цепочка этапов“ (автопереход)». После ответа этапа 1 форма
        # сама открывает вкладку цепочки: там видны состояние этапа 1 и три
        # варианта цепочки этапа 2. При отказе и переадресации остаёмся на
        # вкладке «Обращение»: дальше по делу делать нечего.
        self._goto_chain_after_intake()

    def _goto_chain_after_intake(self) -> None:
        """Автопереход на «Цепочку этапов» после принятия обращения (v63)."""
        state = ogp_stages.stage1_from_outcome(self.case.outcome)
        if not state or ogp_stages.stage1_closes(state):
            return
        if self.case.intake_pending:
            return
        if hasattr(self, "tabs") and self.tabs.currentIndex() != 1:
            self.tabs.setCurrentIndex(1)
        self.status.setText(
            "Этап 1: принятие — выберите вариант цепочки и нажмите «Этап 2.1»")

    def open_case(self, case: cases.GovCase) -> None:
        """Загружает дело в форму: обращение, состояние, обстоятельства (v42)."""
        self.case = case
        self.cases.save(case)
        self.texts["appeal_raw"].blockSignals(True)
        self.texts["appeal_raw"].setPlainText(case.appeal_raw)
        self.texts["appeal_raw"].blockSignals(False)
        self.fields["case_number"].setText(str(case.number or ""))
        self.fields["appeal_number"].setText(str(case.appeal_number or ""))
        for key in ("facts_video", "facts_for", "facts_against", "proposal",
                    "own_decision"):
            widget = self.texts.get(key)
            if widget is not None:
                widget.blockSignals(True)
                widget.setPlainText(getattr(case, key, "") or "")
                widget.blockSignals(False)
        if case.intake_answer:
            self.intake_hint.setText(
                f"Решение ИИ по обращению: {case.status_label()}. Основание: "
                f"{case.intake_basis or '—'}.")
            self.intake_hint.setStyleSheet("color: #8f96bf; font-size: 11px;")
        self._on_appeal_changed()
        # v43: этап выбирается один раз — при открытии дела, и дальше не
        # меняется сам (замечание про «этап 1 превращался в этап 2»).
        # v43.2: по делу со статусом «Отказано» открывается этап 1 — документ
        # об отказе это первичное решение, и его нужно напечатать в PNG.
        self._select_stage(cases.primary_stage_kind(case))
        self._refresh_case_view()
        self.status.setText(f"Открыто обращение №{case.number}")
        self.tabs.setCurrentIndex(0)

    # ------------------------------------------------------------------ данные

    def _collect_case(self) -> None:
        """Переносит поля формы в дело (v42, v49).

        v49: реквизиты обращения (заявитель, нарушитель, дата события, предмет)
        НЕ разбираются из текста обращения — их даёт ИИ ключевыми словами.
        Поэтому проверка ключевых слов выполняется здесь, а не в локальных
        фильтрах: если пользователь переставил реквизиты местами или ИИ
        прислал их в другом порядке, дело всё равно получит верные значения.
        """
        self.case.appeal_raw = self.texts["appeal_raw"].toPlainText().strip()
        number = self.fields["appeal_number"].text().strip()
        self.case.appeal_number = number
        for key in ("facts_video", "facts_for", "facts_against", "proposal",
                    "own_decision"):
            widget = self.texts.get(key)
            if widget is not None:
                setattr(self.case, key, widget.toPlainText().strip())
        self.case.stage_kind = self._stage_kind()
        self._refresh_case_keywords()
        if not self.case.number:
            self.case.number = self.cases.next_number()

    def _refresh_case_keywords(self) -> None:
        """Перечитывает ключевые слова из ответа ИИ на «Проверку ИИ» (v49).

        Ответ уже сохранён в деле (`intake_answer`). Если пользователь повторил
        проверку и ИИ уточнил реквизиты, дело получит новые значения — при
        этом без локального разбора текста обращения.
        """
        if getattr(self, "case", None) is None:
            return
        answer = str(getattr(self.case, "intake_answer", "") or "")
        if not answer.strip():
            return
        try:
            parsed = cases.parse_intake_answer(answer)
        except Exception:
            diag.exception("ui.gov.keywords")
            return
        keywords = (parsed or {}).get("keywords") or {}
        if keywords:
            self.case.apply_keywords(keywords)

    def _fill_defaults(self) -> None:
        """Автозаполнение из Шаблона активного персонажа (v37)."""
        from config import document_defaults
        defaults = document_defaults(self.cfg)
        doc = gdata.PostanovlenieProkurora()
        doc.author_name = defaults["author_name"]
        doc.author_rank = defaults["author_rank"] or "Прокурор"
        doc.author_id = defaults["author_id"]
        doc.city = defaults["city"]
        self.set_document(doc)
        self._suggest_number()

    def _doc_prefix(self) -> str:
        from config import document_defaults
        return document_defaults(self.cfg).get("gov_doc_prefix") or "AG"

    def _suggest_number(self) -> None:
        number = self.document.auto_number(self.history.used_numbers(),
                                           prefix=self._doc_prefix())
        self.fields["doc_number"].setText(number)

    def _set_field(self, key: str, value) -> None:
        self.document.set(key, value)

    def _refresh_ooc_hint(self) -> None:
        """Предупреждает о внеигровых терминах прямо при вводе (v36)."""
        if not hasattr(self, "ooc_hint"):
            return
        parts = [w.toPlainText() for w in self.texts.values()]
        parts += [w.text() for w in self.fields.values() if hasattr(w, "text")]
        found = pdata.ooc_terms(" ".join(parts))
        if not found:
            self.ooc_hint.setText("Внеигровых терминов (IC/OOC) нет.")
            self.ooc_hint.setStyleSheet("color: #34d399; font-size: 11px;")
            return
        self.ooc_hint.setStyleSheet("color: #f59e0b; font-size: 11px;")
        self.ooc_hint.setText(
            "Найдены внеигровые термины: " + ", ".join(found) +
            ". В документе их быть не должно.")

    # ------------------------------------------------- папка сохранения (v28)

    def _documents_root(self) -> Path:
        try:
            from config import postanovlenie_dir
            return postanovlenie_dir(self.cfg)
        except Exception:
            diag.exception("ui.postanovlenie.root")
            return self.base_dir / "postanovleniya"

    def _current_folder(self) -> Path:
        return self._documents_root()

    def _current_folder_text(self) -> str:
        # v62: длинный текст про путь сохранения убран (замечание
        # пользователя) — остался только заголовок «Сохранение» нал кнопками,
        # а полный путь виден в подсказке кнопки «Папка…».
        return ""

    def _choose_folder(self) -> None:
        start = str(self._current_folder())
        chosen = QFileDialog.getExistingDirectory(
            self, "Куда сохранять постановления", start)
        if not chosen:
            return
        self.cfg["postanovlenie_dir"] = chosen
        try:
            from config import save_config
            save_config(self.cfg)
        except Exception:
            diag.exception("ui.postanovlenie.save_folder")
        self.folder_label.setText(self._current_folder_text())
        self.status.setText(f"Папка сохранения: {chosen}")

    def _open_folder(self) -> None:
        folder = self._current_folder()
        try:
            import os
            import subprocess
            if os.name == "nt":
                os.startfile(str(folder))            # noqa: S606
            elif sys_platform_is_mac():
                subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except Exception:
            diag.exception("ui.postanovlenie.open_folder")

    # ---------------------------------------------------------------- сборка

    def _collect(self) -> gdata.PostanovlenieProkurora:
        """Документ текущего этапа. Обстоятельства — из четырёх частей дела."""
        self._collect_case()
        doc = gdata.PostanovlenieProkurora.from_dict(self.document.to_dict())
        for key, widget in self.fields.items():
            if key in ("case_number", "appeal_number"):
                continue
            if widget is not None and hasattr(widget, "text"):
                doc.set(key, widget.text().strip())
        # v62.3: комбобоксов этапа и вида решения в форме нет — этап берётся
        # из активного узла цепочки, вид решения — из ответа ИИ (документа).
        doc.doc_stage = cases.gov_stage_for_kind(self._stage_kind())
        doc.doc_type = (str(doc.get("doc_type") or "").strip()
                        or gdata.auto_type_for(doc.doc_stage))
        doc.city = doc.city or gdata.GOV_CITY or "г. Лос-Сантос"
        for key, widget in self.texts.items():
            if key == "appeal_raw" or key in (
                    "facts_video", "facts_for", "facts_against", "proposal",
                    "own_decision"):
                continue
            doc.set(key, widget.toPlainText().strip())
        # «Обстоятельства» прокурора (v56). Замечание пользователя: «это то,
        # что даётся ИИ для анализа, а уже ИИ, получая „Обстоятельства“,
        # анализирует и даёт информацию по Постановлению… все что я пишу в
        # Обстоятельства сам и исправляю, попасть не должно».
        #
        # Поэтому блоки формы в документ НЕ копируются: они уходят только в
        # заявку (`cases.stage_request` → `own_facts_text`) и служат входом для
        # ИИ. В «УСТАНОВИЛ» печатается исключительно ответ ИИ (`established`);
        # `situation`/`prosecutor_facts` заполняются из ответа ИИ уже после его
        # получения (см. `apply_answer_texts`), а не из блоков формы.
        if not cases.stage_meta(self._stage_kind())["needs_facts"]:
            doc.prosecutor_facts = ""
        doc.set("situation", "")
        # Дело: прокурор видит, к какому обращению относится документ, а имя
        # файла получает этап — PNG прошлых этапов не заменяются (v42).
        doc.appeal_raw = self.case.appeal_raw
        doc.case_number = str(self.case.number or "")
        doc.case_stage_kind = self._stage_kind()
        if self.case.intake_basis:
            doc.preamble_basis = doc.get("preamble_basis")
        # v49: реквизиты постановления — из ключевых слов ответа ИИ, а не из
        # локального разбора текста обращения (заявитель, нарушитель, дата
        # события, предмет, нормы). Пользователь может переставить реквизиты
        # местами — это больше не ломает фильтры.
        doc.apply_keywords(self.case.intake_fields)
        if not doc.doc_date:
            doc.doc_date = pdata.today_ru()
        return doc

    def set_document(self, doc: gdata.PostanovlenieProkurora) -> None:
        self.document = gdata.PostanovlenieProkurora.from_dict(doc.to_dict())
        for key, widget in self.fields.items():
            if widget is not None and hasattr(widget, "setText"):
                widget.setText(doc.get(key))
        # v62.3: этап и вид решения больше не выставляются в комбобоксах —
        # их нет в форме. Вид решения из загруженного документа сохраняем в
        # самом документе, а этап определяет узел цепочки.
        stage = doc.stage() if hasattr(doc, "stage") else gdata.GOV_STAGE_PRIMARY
        self._on_type_changed(doc.get("doc_type") or gdata.auto_type_for(stage))
        for key, widget in self.texts.items():
            if key in ("appeal_raw", "facts_video", "facts_for", "facts_against",
                       "proposal", "own_decision"):
                continue
            widget.setPlainText(doc.get(key))
        if "appeal_raw" in self.texts:
            self.texts["appeal_raw"].blockSignals(True)
            self.texts["appeal_raw"].setPlainText(doc.appeal_raw_text())
            self.texts["appeal_raw"].blockSignals(False)
            self._on_appeal_changed()
        self.signature_hint.setText(
            f"Подпись в документе: {pdata.initials_signature(doc.author_name) or '—'}")
        self._refresh_ooc_hint()
        self.status.setText("Данные загружены — проверьте и нажмите «Сформировать»")

    # ----------------------------------------------------------------- история

    def _refresh_history(self) -> None:
        self.history_list.clear()
        for label in self.history.labels():
            self.history_list.addItem(label)
        empty = not self.history.items()
        if empty:
            self.history_list.addItem(
                "История пуста — сформированных постановлений ещё нет")
        self.history_list.setEnabled(not empty)
        for name in ("history_delete_btn", "history_clear_btn"):
            button = getattr(self, name, None)
            if button is not None:
                button.setEnabled(not empty)

    def _history_index(self) -> int:
        if not hasattr(self, "history_list"):
            return -1
        return self.history_list.currentRow()

    def _delete_history(self) -> None:
        index = self._history_index()
        if index < 0:
            QMessageBox.information(self, "Удаление",
                                    "Выберите постановление в списке истории.")
            return
        label = self.history.label_at(index) or "выбранную запись"
        confirm = QMessageBox.question(
            self, "Удалить постановление?",
            f"Удалить из истории запись «{label}»?\n\n"
            "Сам бланк (PNG и тексты в папке документа) останется на диске.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if confirm != QMessageBox.StandardButton.Yes:
            return
        if self.history.delete_index(index):
            self.status.setText("Запись удалена из истории")
        else:
            QMessageBox.warning(self, "Ошибка",
                                "Не удалось удалить запись истории.")
        self._refresh_history()

    def _clear_history(self) -> None:
        if not self.history.items():
            QMessageBox.information(self, "История", "История уже пуста.")
            return
        confirm = QMessageBox.question(
            self, "Очистить историю?",
            "Удалить ВСЕ сохранённые постановления из истории?\n\n"
            "Готовые бланки в папках документов останутся на диске.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if confirm != QMessageBox.StandardButton.Yes:
            return
        removed = self.history.delete_all()
        self.status.setText(f"История очищена: удалено записей — {removed}")
        self._refresh_history()

    def _load_history(self) -> None:
        """Открывает запись истории в форме (v38, v41)."""
        index = self._history_index()
        doc = None
        if index >= 0:
            try:
                doc = self.history.load_index(index)
            except Exception:
                diag.exception("ui.gov.history_load")
                doc = None
        if doc is None:
            if index >= 0:
                QMessageBox.warning(
                    self, "Запись не открывается",
                    "Не удалось прочитать выбранное постановление из истории. "
                    "Выберите другую запись или сформируйте документ заново.")
            return
        try:
            self.set_document(doc)
        except Exception:
            diag.exception("ui.gov.history_set_document")
            QMessageBox.warning(
                self, "Запись не открывается",
                "Запись повреждена или сохранена другой версией программы.")
            return
        self.tabs.setCurrentIndex(2)

    # -------------------------------------------------------------- действия

    def _form(self):
        """Совместимость: главная кнопка теперь отправляет узел цепочки (v62).

        Метод оставлен, потому что его имя встречается в старых тестах и в
        истории: он просто делегирует активный узел в `_run_main_action`.
        """
        self._run_main_action()

    # --------------------------------------------------------- перетаскивание

    def showEvent(self, event):
        """Окно не должно открываться за краем экрана (v39.1).

        v43: если форма открыта без конкретного дела, первым делом показываем
        список обращений и их состояний — работа всегда начинается с выбора
        обращения или создания нового.
        """
        super().showEvent(event)
        if getattr(self, "ask_case_on_show", False):
            self.ask_case_on_show = False
            try:
                self._open_case_dialog()
            except Exception:
                diag.exception("ui.gov.start_cases")
        try:
            screen = self.screen() or QApplication.primaryScreen()
            if screen is None:
                return
            area = screen.availableGeometry()
            width = min(self.width(), max(area.width() - 40, 900))
            height = min(self.height(), max(area.height() - 40, 640))
            if (width, height) != (self.width(), self.height()):
                self.resize(width, height)
            x = area.x() + max(0, (area.width() - width) // 2)
            y = area.y() + max(0, (area.height() - height) // 2)
            self.move(x, y)
        except Exception:
            diag.exception("ui.postanovlenie.fit_screen")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            child = self.childAt(event.position().toPoint())
            if not isinstance(child, (QLineEdit, QPlainTextEdit, QComboBox)):
                self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton and self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        super().mouseReleaseEvent(event)


def sys_platform_is_mac() -> bool:
    import sys
    return sys.platform == "darwin"
