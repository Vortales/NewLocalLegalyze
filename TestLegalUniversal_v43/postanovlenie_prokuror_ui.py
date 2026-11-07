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
   * PNG каждого этапа получает своё имя («AG-1 — эт.1 принятие») и свою
     папку, поэтому первый документ не затирается следующим.
"""

from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QTextOption
from PyQt6.QtWidgets import (
    QAbstractScrollArea, QApplication, QComboBox, QDialog, QFileDialog,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QTabWidget,
    QVBoxLayout, QWidget,
)

import diagnostics as diag
import doc_render as render
import postanovlenie_data as pdata
import postanovlenie_prokuror_data as gdata
import gov_case as cases
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
        self.png_paths: list = []
        self._drag_pos = None
        #: Ответ ИИ на проверку обращения ещё не пришёл.
        self.intake_pending = False
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
        self.status_badge = self._label("Новое", "Caption")
        self.status_badge.setStyleSheet("color: #8f96bf; font-size: 12px; font-weight: 700;")
        head.addWidget(self.status_badge)
        head.addStretch()
        self.status = self._label("", "Caption")
        head.addWidget(self.status)
        root.addLayout(head)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_appeal_tab(), "Обращение")
        self.tabs.addTab(self._build_work_tab(), "Обстоятельства")
        self.tabs.addTab(self._build_document_tab(), "Постановление")
        self.tabs.addTab(self._build_history_tab(), "История")
        root.addWidget(self.tabs, 1)

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
        buttons.addStretch()
        self.form_btn = QPushButton("Сформировать")
        self.form_btn.setObjectName("Primary")
        self.form_btn.setToolTip(
            "Сначала выполните «Проверку ИИ» на вкладке «Обращение»")
        self.form_btn.setEnabled(False)
        self.form_btn.clicked.connect(lambda _c=False: self._form())
        buttons.addWidget(self.form_btn)
        root.addLayout(buttons)

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
        self.intake_btn = QPushButton("Проверка ИИ")
        self.intake_btn.setObjectName("Primary")
        self.intake_btn.setToolTip(
            "Отправить обращение ИИ: принято к производству или отказано")
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

    # ---------------------------------------------------- вкладка «Обстоятельства»

    def _build_work_tab(self) -> QWidget:
        page = QWidget()
        page.setObjectName("Sheet")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        self.work_hint = self._label("", "Caption")
        self.work_hint.setWordWrap(True)
        layout.addWidget(self.work_hint)

        grid = QHBoxLayout()
        grid.setSpacing(10)

        left = QVBoxLayout()
        left.setSpacing(4)
        left.addWidget(self._label("a) Что видно на видеофиксации", "Section"))
        self.texts["facts_video"] = self._text_area(
            "00:04 — сотрудник с ОПЗ 8332 требует остановиться; "
            "02:09 — открывает огонь…", height=130)
        self.texts["facts_video"].textChanged.connect(self._refresh_ooc_hint)
        left.addWidget(self.texts["facts_video"], 2)

        arguments = QHBoxLayout()
        arguments.setSpacing(8)
        arg_for = QVBoxLayout()
        arg_for.setSpacing(4)
        arg_for.addWidget(self._label("б) Доводы за", "Field"))
        self.texts["facts_for"] = self._text_area("что подтверждает обращение", height=90)
        self.texts["facts_for"].textChanged.connect(self._refresh_ooc_hint)
        arg_for.addWidget(self.texts["facts_for"], 1)
        arguments.addLayout(arg_for)
        arg_against = QVBoxLayout()
        arg_against.setSpacing(4)
        arg_against.addWidget(self._label("б) Доводы против", "Field"))
        self.texts["facts_against"] = self._text_area("что говорит против", height=90)
        self.texts["facts_against"].textChanged.connect(self._refresh_ooc_hint)
        arg_against.addWidget(self.texts["facts_against"], 1)
        arguments.addLayout(arg_against)
        left.addLayout(arguments, 2)
        grid.addLayout(left, 1)

        right = QVBoxLayout()
        right.setSpacing(4)
        right.addWidget(self._label("в) Предлагаемое наказание", "Section"))
        self.texts["proposal"] = self._text_area(
            "прокурор полагает возможным: штраф 20 000 $ …", height=90)
        self.texts["proposal"].textChanged.connect(self._refresh_ooc_hint)
        right.addWidget(self.texts["proposal"], 1)
        right.addWidget(self._label("г) Решение прокурора", "Section"))
        self.texts["own_decision"] = self._text_area(
            "привлечь к административной ответственности …", height=110)
        self.texts["own_decision"].textChanged.connect(self._refresh_ooc_hint)
        right.addWidget(self.texts["own_decision"], 2)
        hint = self._label(
            "ИИ рассмотрит предложение и решение прокурора, но примет "
            "собственное решение по существу и обоснует его нормой.", "Caption")
        hint.setWordWrap(True)
        right.addWidget(hint)
        grid.addLayout(right, 1)
        layout.addLayout(grid, 1)

        steps = QHBoxLayout()
        steps.setSpacing(6)
        steps.addWidget(self._field_label("Этап"))
        self.cb_stage_kind = QComboBox()
        for kind in cases.DEFAULT_STAGE_PLAN + cases.EXTRA_STAGES:
            self.cb_stage_kind.addItem(cases.stage_title(kind), kind)
        self.cb_stage_kind.currentIndexChanged.connect(
            lambda _i: self._on_stage_kind_changed())
        steps.addWidget(self.cb_stage_kind, 1)
        add_stage = QPushButton("+ этап")
        add_stage.setToolTip("Добавить дополнительный этап в цепочку дела")
        add_stage.clicked.connect(lambda _c=False: self._add_stage())
        steps.addWidget(add_stage)
        layout.addLayout(steps)

        self.stage_hint = self._label("", "Caption")
        self.stage_hint.setWordWrap(True)
        layout.addWidget(self.stage_hint)

        self.stage_list = QListWidget()
        self.stage_list.setMaximumHeight(92)
        layout.addWidget(self.stage_list)
        return page

    # -------------------------------------------------- вкладка «Постановление»

    def _build_document_tab(self) -> QWidget:
        page = QWidget()
        page.setObjectName("Sheet")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)
        layout.addWidget(self._label(
            "Реквизиты постановления. Вид решения по умолчанию определяет ИИ; "
            "конкретный вид — только дополнительная опция.", "Caption"))

        columns = QHBoxLayout()
        columns.setSpacing(14)
        layout.addLayout(columns, 1)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)
        left.setMinimumWidth(460)
        left.setMaximumWidth(500)
        columns.addWidget(left, 0)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)
        right.setMinimumWidth(480)
        columns.addWidget(right, 1)
        columns.setStretch(0, 0)
        columns.setStretch(1, 1)

        left_layout.addWidget(self._label("Постановление", "Section"))
        doc_form = self._compact(QFormLayout())

        self.cb_stage = QComboBox()
        for stage_key in (gdata.GOV_STAGE_PRIMARY, gdata.GOV_STAGE_FINAL):
            self.cb_stage.addItem(gdata.GOV_STAGE_SHORT[stage_key], stage_key)
        self.cb_stage.setMinimumWidth(200)
        self.cb_stage.setToolTip(
            "Этап постановления определяется этапом дела; здесь можно уточнить")
        self.cb_stage.currentIndexChanged.connect(lambda _i: self._on_stage_changed())
        doc_form.addRow(self._field_label("Этап"), self.cb_stage)

        self.cb_type = QComboBox()
        self.cb_type.setEditable(True)
        self.cb_type.setMinimumWidth(200)
        self._fill_types(gdata.GOV_STAGE_PRIMARY)
        self.cb_type.currentTextChanged.connect(lambda text: self._on_type_changed(text))
        doc_form.addRow(self._field_label("Вид решения"), self.cb_type)
        left_layout.addLayout(doc_form)

        self.fields["doc_number"] = self._input("AG-1")
        self.fields["doc_date"] = self._input("06.11.2026")
        number_row = QHBoxLayout()
        number_row.setSpacing(6)
        number_row.addWidget(self.fields["doc_number"], 2)
        number_row.addWidget(self.fields["doc_date"], 1)
        suggestion = QPushButton("Следующий №")
        suggestion.setFixedWidth(112)
        suggestion.clicked.connect(lambda _c=False: self._suggest_number())
        number_row.addWidget(suggestion)
        wrap = QWidget()
        wrap.setLayout(number_row)
        number_row.setContentsMargins(0, 0, 0, 0)
        number_form = self._compact(QFormLayout())
        number_form.addRow(self._field_label("Номер / дата"), wrap)
        left_layout.addLayout(number_form)

        left_layout.addWidget(self._label("Кто выносит постановление", "Section"))
        who = self._compact(QFormLayout())
        self.fields["author_name"] = self._input("Mekyla Barbosov")
        self.fields["author_rank"] = self._input("Прокурор")
        self.fields["author_id"] = self._input("15902")
        who.addRow(self._field_label("ФИО"), self.fields["author_name"])
        who.addRow(self._field_label("Должность"), self.fields["author_rank"])
        who.addRow(self._field_label("Удостоверение №"), self.fields["author_id"])
        self.signature_hint = self._label("Подпись: —", "Caption")
        who.addRow(self._label(""), self.signature_hint)
        self.fields["author_name"].textChanged.connect(
            lambda text: self.signature_hint.setText(
                f"Подпись в документе: {pdata.initials_signature(text) or '—'}"))
        left_layout.addLayout(who)

        right_layout.addWidget(self._label("Правовая часть (заполняет ИИ)", "Section"))
        right_layout.addWidget(self._label(
            "По умолчанию все поля ниже пусты: норму, основание и решение "
            "определяет ИИ. Заполняйте только чтобы уточнить.", "Caption"))
        self.extra_toggle = QPushButton("Уточнить вручную (необязательно)")
        self.extra_toggle.setCheckable(True)
        self.extra_toggle.setChecked(False)
        self.extra_toggle.clicked.connect(lambda _c=False: self._toggle_extra())
        right_layout.addWidget(self.extra_toggle)

        self.extra_panel = QWidget()
        extra_form = self._compact(QFormLayout())
        self.extra_panel.setLayout(extra_form)
        self.fields["violation_act"] = self._input("Устав / закон / кодекс")
        self.fields["violation_clause"] = self._input("например: статья 17.6 УК")
        self.fields["penalty"] = self._input("решение: проверка / штраф / суд")
        self.texts["legal_basis"] = self._text_area(height=52)
        self.texts["legal_basis"].textChanged.connect(
            lambda: self._set_field("legal_basis", self.texts["legal_basis"].toPlainText()))
        self.texts["legal_basis"].textChanged.connect(self._refresh_ooc_hint)
        self.texts["extra"] = self._text_area(height=44)
        self.texts["extra"].textChanged.connect(
            lambda: self._set_field("extra", self.texts["extra"].toPlainText()))
        self.texts["extra"].textChanged.connect(self._refresh_ooc_hint)
        extra_form.addRow(self._field_label("Нарушенный акт"), self.fields["violation_act"])
        extra_form.addRow(self._field_label("Статьи / пункты"), self.fields["violation_clause"])
        extra_form.addRow(self._field_label("Решение"), self.fields["penalty"])
        extra_form.addRow(self._field_label("Правовое основание"), self.texts["legal_basis"])
        extra_form.addRow(self._field_label("Дополнительно"), self.texts["extra"])
        self.extra_panel.setVisible(False)
        right_layout.addWidget(self.extra_panel)

        right_layout.addWidget(self._label("Сохранение", "Section"))
        folder_row = QHBoxLayout()
        folder_row.setSpacing(6)
        self.folder_label = self._label(self._current_folder_text(), "Caption")
        self.folder_label.setWordWrap(True)
        choose_folder = QPushButton("Выбрать папку…")
        choose_folder.clicked.connect(lambda _c=False: self._choose_folder())
        open_folder = QPushButton("Открыть")
        open_folder.clicked.connect(lambda _c=False: self._open_folder())
        folder_row.addWidget(self.folder_label, 1)
        folder_row.addWidget(choose_folder)
        folder_row.addWidget(open_folder)
        right_layout.addLayout(folder_row)
        hint_folder = self._label(
            "PNG этапа: «<номер> — эт.N <этап>» в папке документа; "
            "документы разных этапов не заменяют друг друга.", "Caption")
        hint_folder.setWordWrap(True)
        right_layout.addWidget(hint_folder)
        right_layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setSizeAdjustPolicy(
            QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored)
        scroll.setWidget(page)
        return scroll

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
        """Список видов решений: первым — «Определит ИИ по обращению» (v43).

        Замечание: «В Постановление → Вид решения → всегда был по умолчанию
        "Определит ИИ по обращению"». Поэтому первый пункт списка — всегда это
        значение (`gdata.DEFAULT_DOC_TYPE`), независимо от этапа; конкретные
        виды и «по обстоятельствам» остаются дополнительными пунктами.
        """
        self.cb_type.blockSignals(True)
        current = self.cb_type.currentText().strip()
        self.cb_type.clear()
        types = gdata.type_choices(stage)
        self.cb_type.addItems(types)
        if current in types and gdata.is_auto_type(current):
            self.cb_type.setCurrentText(current)
        else:
            index = types.index(gdata.DEFAULT_DOC_TYPE) if \
                gdata.DEFAULT_DOC_TYPE in types else 0
            self.cb_type.setCurrentIndex(index)
        self.cb_type.blockSignals(False)

    def _on_stage_changed(self) -> None:
        if not hasattr(self, "cb_stage"):
            return
        stage = self.cb_stage.currentData() or gdata.GOV_STAGE_PRIMARY
        self._fill_types(stage)
        self._set_field("doc_stage", stage)
        self.cb_stage.setToolTip(gdata.GOV_STAGE_TITLES[stage])
        self._on_type_changed(self.cb_type.currentText())

    def _on_type_changed(self, text: str) -> None:
        text = (text or "").strip()
        stage_now = (self.cb_stage.currentData() if hasattr(self, "cb_stage") else
                     gdata.GOV_STAGE_PRIMARY) or gdata.GOV_STAGE_PRIMARY
        self._set_field("doc_type", text or gdata.auto_type_for(stage_now))
        if hasattr(self, "cb_type"):
            self.cb_type.setToolTip(text)
            self.cb_type.setMinimumContentsLength(min(30, max(16, len(text))))

    def _toggle_extra(self) -> None:
        visible = bool(self.extra_toggle.isChecked())
        if hasattr(self, "extra_panel"):
            self.extra_panel.setVisible(visible)

    # ------------------------------------------------------ дело: состояние, этапы

    def _stage_kind(self) -> str:
        """Этап, выбранный пользователем. Сам не меняется (v43)."""
        if hasattr(self, "cb_stage_kind") and self.cb_stage_kind.currentData():
            return self.cb_stage_kind.currentData()
        return cases.STAGE_INTAKE

    def _stage_index(self) -> int:
        """Номер этапа для файлов — постоянный для вида этапа (v43).

        Раньше номер считался по количеству выполненных этапов, поэтому после
        «Проверки ИИ» и «Сформировать» этап 1 превращался в этап 2.
        """
        return self.case.stage_number(self._stage_kind())

    def _select_stage(self, kind: str, keep_user_choice: bool = False) -> None:
        """Ставит этап в списке, не сбивая выбор пользователя (v43)."""
        if not hasattr(self, "cb_stage_kind"):
            return
        index = self.cb_stage_kind.findData(kind)
        if index < 0:
            return
        current = self._stage_kind()
        if keep_user_choice and current and current != kind:
            return
        if index == self.cb_stage_kind.currentIndex():
            return
        self.cb_stage_kind.blockSignals(True)
        self.cb_stage_kind.setCurrentIndex(index)
        self.cb_stage_kind.blockSignals(False)
        self._refresh_stage_hint()

    def _refresh_stage_hint(self) -> None:
        if not hasattr(self, "stage_hint"):
            return
        kind = self._stage_kind()
        meta = cases.stage_meta(kind)
        lines = [meta["guide"]]
        if meta["needs_facts"]:
            lines.append("Нужны «Обстоятельства» прокурора.")
        else:
            lines.append("Обстоятельства на этом этапе необязательны.")
        if kind == cases.STAGE_MEASURE and not self.case.accepted():
            lines.append("Этап откроется, когда обращение принято к производству.")
        # v43: выбранный этап НЕ меняется сам — показываем только, какой этап
        # по делу следующий, чтобы прокурор видел это, не теряя свой выбор.
        if self.case.intake_checked():
            next_kind = self.case.open_stage_kind()
            if next_kind != kind:
                lines.append(
                    f"По делу следующий этап — «{cases.stage_title(next_kind)}» "
                    f"(эт.{self.case.stage_number(next_kind)}); выбранный этап "
                    "не меняется автоматически.")
        self.stage_hint.setText(" ".join(lines))
        self.stage_hint.setStyleSheet("color: #8f96bf; font-size: 11px;")

    def _on_stage_kind_changed(self) -> None:
        """Пользователь сам выбрал этап: синхронизируем этап документа (v43).

        Иначе в «Постановлении» оставался этап 1 при формировании документа
        этапа 2 — и PNG попадал не туда.
        """
        kind = self._stage_kind()
        # v43: выбранный этап запоминаем в деле — при следующем открытии
        # форма покажет его же, а не «следующий по цепочке».
        self.case.set_stage_kind(kind)
        self.cases.save(self.case)
        if hasattr(self, "cb_stage"):
            stage = (gdata.GOV_STAGE_PRIMARY
                     if kind in (cases.STAGE_INTAKE, cases.STAGE_REDIRECT)
                     else gdata.GOV_STAGE_FINAL)
            index = self.cb_stage.findData(stage)
            if index >= 0 and index != self.cb_stage.currentIndex():
                self.cb_stage.setCurrentIndex(index)   # вызовет _on_stage_changed
        self._refresh_stage_hint()
        self._refresh_case_view()

    def _add_stage(self) -> None:
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
        accepted = self.case.accepted()
        for key in ("facts_video", "facts_for", "facts_against", "proposal",
                    "own_decision"):
            widget = getattr(self, "texts", {}).get(key)
            if widget is not None:
                widget.setReadOnly(not accepted)
        if hasattr(self, "work_hint"):
            if accepted:
                self.work_hint.setText(
                    "Обращение принято к производству: опишите, что видно на "
                    "видеофиксации, доводы за и против, предложите наказание и "
                    "изложите своё решение.")
                self.work_hint.setStyleSheet("color: #34d399; font-size: 11px;")
            elif self.case.refused():
                self.work_hint.setText(
                    "В принятии обращения отказано. Можно проверить ещё раз, "
                    "принять обращение вопреки ответу ИИ («Принять всё равно») "
                    "или закрыть дело.")
                self.work_hint.setStyleSheet("color: #f59e0b; font-size: 11px;")
            else:
                self.work_hint.setText(
                    "Сначала вставьте обращение и нажмите «Проверка ИИ»: "
                    "обстоятельства открываются после принятия обращения.")
                self.work_hint.setStyleSheet("color: #8f96bf; font-size: 11px;")
        for name in ("accept_btn", "recheck_btn"):
            button = getattr(self, name, None)
            if button is not None:
                button.setVisible(self.case.refused() or self.case.status in
                                  (cases.STATUS_NEW, cases.STATUS_CLOSED))
        if hasattr(self, "stage_list"):
            self.stage_list.clear()
            for kind in self.case.plan():
                stage = self.case.stage_by_kind(kind)
                mark = "✓" if stage.get("done") else "•"
                when = f" — {stage.get('when')}" if stage.get("when") else ""
                self.stage_list.addItem(
                    f"{mark} {cases.stage_title(kind)}{when}")
        if hasattr(self, "form_btn"):
            # v43: пока «Проверка ИИ» не сделана, формировать документ нельзя.
            checked = self.case.intake_checked()
            self.form_btn.setEnabled(checked)
            self.form_btn.setToolTip(
                "Отправить заявку на текущий этап в чат: PNG соберётся из "
                "ответа ИИ" if checked else
                "Сначала выполните «Проверку ИИ» на вкладке «Обращение»")
        self._refresh_stage_hint()

    def _on_appeal_changed(self) -> None:
        """Показывает, что удалось распознать в обращении (v37, v42)."""
        if not hasattr(self, "appeal_hint"):
            return
        self.case.appeal_raw = self.texts["appeal_raw"].toPlainText()
        raw = self.case.appeal_raw
        if not raw.strip():
            self.appeal_hint.setText(
                "Вставьте обращение целиком: из него берутся заявитель, "
                "нарушитель, предмет и дата события.")
            self.appeal_hint.setStyleSheet("color: #8f96bf; font-size: 11px;")
            return
        parsed = gdata.parse_appeal_text(raw)
        found = []
        if parsed.get("applicant_name"):
            ident = f" {parsed['applicant_id']}" if parsed.get("applicant_id") else ""
            found.append(f"заявитель {parsed['applicant_name']}{ident}")
        if parsed.get("violator"):
            found.append(f"нарушитель {parsed['violator']}")
        if parsed.get("event_date"):
            found.append(f"событие {parsed['event_date']}")
        missing = []
        if not parsed.get("description"):
            missing.append("нет пункта «Подробное описание ситуации»")
        text = ("Распознано: " + "; ".join(found) + ".") if found else \
            "Реквизиты не распознаны — проверьте, что вставили обращение по форме."
        if missing:
            text += " " + " ".join(missing) + "."
        self.appeal_hint.setText(text)
        color = "#f59e0b" if (missing or not found) else "#34d399"
        self.appeal_hint.setStyleSheet(f"color: {color}; font-size: 11px;")

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
            record = pending_send.build_record(
                kind="intake", request_text=text,
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
            "Заявка сохранена. Клиент перезапустится с чистым чатом и сам "
            "отправит проверку обращения — ответ придёт служебным блоком "
            "«ПРОВЕРКА ОБРАЩЕНИЯ».")
        self.intake_hint.setStyleSheet("color: #7c83ff; font-size: 11px;")
        self.restart_requested.emit(record, str(request_path))
        self.accept()

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
        self.cases.save(self.case)
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
        self._refresh_case_view()
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
        self._refresh_case_view()

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
        """Переносит поля формы в дело (v42)."""
        self.case.appeal_raw = self.texts["appeal_raw"].toPlainText().strip()
        number = self.fields["appeal_number"].text().strip()
        self.case.appeal_number = number
        for key in ("facts_video", "facts_for", "facts_against", "proposal",
                    "own_decision"):
            widget = self.texts.get(key)
            if widget is not None:
                setattr(self.case, key, widget.toPlainText().strip())
        self.case.stage_kind = self._stage_kind()
        if not self.case.number:
            self.case.number = self.cases.next_number()

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
        return f"PNG сохраняется сюда: {self._documents_root()}"

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
        doc.doc_stage = (self.cb_stage.currentData()
                         if hasattr(self, "cb_stage") else "") or gdata.GOV_STAGE_PRIMARY
        doc.doc_type = self.cb_type.currentText().strip() or doc.doc_type
        doc.city = doc.city or gdata.GOV_CITY or "г. Лос-Сантос"
        for key, widget in self.texts.items():
            if key == "appeal_raw" or key in (
                    "facts_video", "facts_for", "facts_against", "proposal",
                    "own_decision"):
                continue
            doc.set(key, widget.toPlainText().strip())
        # «Обстоятельства» прокурора: видеофиксация, доводы, предложение и
        # решение — одним текстом для «УСТАНОВИЛ» (v42). v43: на этапах, где
        # обстоятельства не нужны (принятие, переадресация, прекращение), в
        # документ они не попадают — иначе решение по форме обращения
        # превращалось в решение по существу.
        kind_now = self._stage_kind()
        if cases.stage_meta(kind_now)["needs_facts"] and self.case.has_facts():
            doc.prosecutor_facts = self.case.prosecutor_facts()
            doc.set("situation", doc.prosecutor_facts)
        else:
            doc.prosecutor_facts = ""
        # Дело: прокурор видит, к какому обращению относится документ, а имя
        # файла получает этап — PNG прошлых этапов не заменяются (v42).
        doc.appeal_raw = self.case.appeal_raw
        doc.case_number = str(self.case.number or "")
        doc.case_stage_kind = self._stage_kind()
        if self.case.intake_basis:
            doc.preamble_basis = doc.get("preamble_basis")
        doc.apply_appeal(doc.appeal_raw)
        if not doc.doc_date:
            doc.doc_date = pdata.today_ru()
        return doc

    def set_document(self, doc: gdata.PostanovlenieProkurora) -> None:
        self.document = gdata.PostanovlenieProkurora.from_dict(doc.to_dict())
        for key, widget in self.fields.items():
            if widget is not None and hasattr(widget, "setText"):
                widget.setText(doc.get(key))
        if hasattr(self, "cb_type"):
            stage = doc.stage() if hasattr(doc, "stage") else gdata.GOV_STAGE_PRIMARY
            if hasattr(self, "cb_stage"):
                self.cb_stage.blockSignals(True)
                self.cb_stage.setCurrentIndex(
                    0 if stage == gdata.GOV_STAGE_PRIMARY else 1)
                self.cb_stage.blockSignals(False)
                self._fill_types(stage)
            doc_type = doc.get("doc_type") or gdata.auto_type_for(stage)
            self.cb_type.setCurrentText(doc_type)
            self._on_type_changed(doc_type)
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
        """Текущий этап: заявка в чат → ответ ИИ → PNG этого этапа."""
        self._collect_case()
        kind = self._stage_kind()
        meta = cases.stage_meta(kind)
        doc = self._collect()
        problems = []
        if not self.case.intake_checked():
            problems.append("Выполните «Проверку ИИ» на вкладке «Обращение»")
        if not self.case.appeal_raw.strip():
            problems.append("Обращение")
        if meta["needs_facts"] and not self.case.has_facts():
            problems.append(meta["needs_facts_label"] or "Обстоятельства")
        # v43.2: отказ в принятии — тоже документ, который печатается в PNG.
        # Раньше форма отправляла такие дела на этап 2 и требовала принятия.
        block = cases.stage_block_reason(self.case, kind)
        if block:
            problems.append(block)
        if problems:
            QMessageBox.information(
                self, "Не хватает данных",
                "Заполните обязательные поля:\n• " + "\n• ".join(problems))
            return
        try:
            # v43: номер этапа постоянный для его вида (принятие — 1, санкция — 2),
            # а не «сколько выполнено + 1»: иначе PNG этапа 1 уходил в «эт.2».
            stage_index = self._stage_index()
            folder = cases.stage_folder(
                self._documents_root(), doc.get("doc_number") or self.case.number,
                stage_index, kind)
            chat_txt = folder / "Заявка (отправлено в чат).txt"
            if self.case.refused() and not self.case.accepted():
                # v43.2: документ об отказе — свой текст заявки (без санкции).
                request_text = cases.refusal_intake_request(self.case, doc)
            else:
                request_text = cases.stage_request(self.case, kind, doc)
            chat_txt.write_text(request_text, encoding="utf-8")
            self.chat_txt_path = str(chat_txt)
            self.png_paths = []
            (folder / "Заявка-поля.json").write_text(
                json.dumps({"когда": doc.history_payload().get("human_created"),
                            "дело": self.case.number,
                            "этап": stage_index,
                            "вид этапа": kind,
                            "поля": doc.to_dict()},
                           ensure_ascii=False, indent=2),
                encoding="utf-8")
            self.history.save(doc)
            self.cases.save(self.case)
            self._refresh_history()
        except Exception as exc:
            diag.exception("ui.postanovlenie.form")
            QMessageBox.warning(self, "Ошибка",
                                f"Не удалось сформировать документ: {exc}")
            return
        self.document = doc
        # v43.2: заявка уходит в чат только ПОСЛЕ перезапуска клиента —
        # пользователь просил перезапуск, чтобы чат был чистым (без старых
        # записей и ответов ИИ).
        try:
            record = pending_send.build_record(
                kind="document",
                request_text=Path(self.chat_txt_path).read_text(encoding="utf-8"),
                source_path=self.chat_txt_path,
                case_number=str(self.case.number),
                stage_kind=kind,
                stage_index=self._stage_index(),
                doc_type=doc.get("doc_type"),
                document_kind="gov",
                title=pending_send.describe({"case_number": self.case.number,
                                             "stage_index": self._stage_index(),
                                             "doc_type": doc.get("doc_type")}))
            pending_send.save(self._documents_root(), record)
        except Exception:
            diag.exception("ui.gov.form.pending")
            QMessageBox.warning(
                self, "Не удалось сохранить заявку",
                "Заявка не сохранена для отправки после перезапуска. "
                "Отправка отменена — сформируйте документ заново.")
            return
        self.formed.emit(doc, self.chat_txt_path, self.png_paths)
        self.restart_requested.emit(record, str(self.chat_txt_path))
        self.accept()

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
