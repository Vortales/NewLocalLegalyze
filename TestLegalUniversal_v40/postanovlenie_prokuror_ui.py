"""postanovlenie_prokuror_ui.py — форма постановления Прокурора (v36).

Что изменилось после замечаний к v27:

* **Фон формы** — как в остальных окнах клиента (`BASE_QSS`): раньше на
  светлом/прозрачном фоне подписи сливались и их не было видно.
* **Компактные поля**: высокие строки «съедали» окно, теперь форма
  помещается на экран ноутбука.
* **«Нарушение и взыскание» — необязательно**: если пользователь заполняет
  только «Обстоятельства», текст постановления и правовую часть формирует
  ИИ (он же и исправляет формулировки).
* **Папка сохранения PNG** выбирается прямо в форме и запоминается в
  `config.json` (`postanovlenie_dir`).
* Подпись рисуется рукописным шрифтом (Caveat в `data/fonts`) — см. `doc_render`.
"""

from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QTextOption
from PyQt6.QtWidgets import (
    QAbstractScrollArea, QApplication, QComboBox, QDialog, QFileDialog,
    QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QMessageBox, QPlainTextEdit,
    QPushButton, QScrollArea, QSizePolicy, QTabWidget, QVBoxLayout, QWidget,
)

import diagnostics as diag
import doc_render as render
import postanovlenie_data as pdata
import postanovlenie_prokuror_data as gdata


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


class PostanovlenieProkuroraDialog(QDialog):
    """Форма постановления Прокурора: заявка в чат -> PNG из ответа ИИ."""

    #: Документ сформирован: (Postanovlenie, путь к .txt для чата, [png...]).
    formed = pyqtSignal(object, str, object)

    def __init__(self, cfg, base_dir=None, history=None, assets=None,
                 regulation=None, parent=None):
        super().__init__(parent)
        self.cfg = cfg or {}
        self.base_dir = Path(base_dir) if base_dir else Path(__file__).resolve().parent
        self.assets = assets or render.DocAssets(self.base_dir)
        self.regulation = regulation
        self.history = history or pdata.DocumentHistory(
            (Path(base_dir) if base_dir else Path(__file__).resolve().parent) / "postanovleniya")
        self.document = gdata.PostanovlenieProkurora()
        self.chat_txt_path = ""
        self.png_paths: list = []
        self._drag_pos = None

        self.setWindowTitle("Документы — Постановление Прокурора")
        # v39.1: «не все объекты влазят». При 980×780 левая колонка с
        # подсказками и полями «Должность»/«Вид решения» упиралась в границы, а
        # правая колонка уходила за край. Минимум теперь рассчитан на две
        # колонки с запасом, а высота — чтобы обе текстовые области читались.
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
        # v39.1: «Куратор отдела IAD …» обрезался левой рамкой поля. Поле
        # должно быть достаточно широким даже в узкой колонке, а длинный
        # текст — прокручиваться внутри, а не упираться в границу.
        field.setMinimumWidth(190)
        field.setSizePolicy(QSizePolicy.Policy.Expanding,
                            QSizePolicy.Policy.Fixed)
        field.setClearButtonEnabled(False)
        return field

    def _text_area(self, placeholder: str = "", height: int = 160) -> QPlainTextEdit:
        """Текстовое поле с переносом по словам и без «уезжания» вправо (v38).

        Замечание: «внутри Постановления для Прокурора «Обстоятельства» и
        «Обращение» не помещаются корректно, уезжает на право». Причина —
        QPlainTextEdit по умолчанию НЕ переносит строки: длинные строки
        обращения уходили за правый край, а поле растягивало форму вширь.
        Теперь включён wrap, горизонтальная прокрутка выключена, а минимальная
        ширина ограничена, чтобы колонка не расширялась под текст.
        """
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
        head.addStretch()
        self.status = self._label("Заполните поля и нажмите «Сформировать»", "Caption")
        head.addWidget(self.status)
        root.addLayout(head)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_form_tab(), "Данные")
        self.tabs.addTab(self._build_history_tab(), "История")
        self.tabs.addTab(self._build_preview_tab(), "Предпросмотр")
        root.addWidget(self.tabs, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(lambda _c=False: self.reject())
        buttons.addWidget(close_btn)
        buttons.addStretch()
        preview_btn = QPushButton("Предпросмотр")
        preview_btn.clicked.connect(lambda _c=False: self._make_preview())
        buttons.addWidget(preview_btn)
        save_btn = QPushButton("Сохранить без отправки")
        save_btn.clicked.connect(lambda _c=False: self._save_only())
        buttons.addWidget(save_btn)
        form_btn = QPushButton("Сформировать")
        form_btn.setObjectName("Primary")
        form_btn.clicked.connect(lambda _c=False: self._form())
        buttons.addWidget(form_btn)
        root.addLayout(buttons)

    def _build_form_tab(self) -> QWidget:
        """Форма постановления Прокурора (v37).

        Замечание пользователя: «Обращения всегда однотипны по форме, его
        нужно вводить в поле целиком — в нём уже есть и заявитель, и
        нарушитель, и предмет обращения. Поэтому поля "Обращение" и
        "Нарушитель событие" нужно убрать: пусть ИИ сам читает обращение и
        ловит суть. Обстоятельства пусть пишет прокурор, посмотрев
        видеофиксацию с тайм-кодами».

        Итог: слева — реквизиты постановления (кто, когда, какой вид решения),
        справа — ДВА текстовых поля: «Обращение» (вставляется целиком) и
        «Обстоятельства» (что прокурор увидел на видеофиксации). Всё
        остальное — заявитель, нарушитель, предмет, дата события, тайм-коды —
        ИИ достаёт из текста обращения.
        """
        page = QWidget()
        page.setObjectName("Sheet")
        outer = QVBoxLayout(page)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(6)

        self.fields: dict = {}
        self.texts: dict = {}

        # v39.1: инструкция была на три строки и вместе с двумя подсказками под
        # списками съедала высоту — «не все объекты влазят». Оставлено главное.
        hint_top = self._label(
            "Обращение — целиком (ИИ найдёт заявителя, нарушителя, предмет). "
            "Обстоятельства — что видно на видеофиксации, с тайм-кодами.", "Caption")
        hint_top.setWordWrap(True)
        outer.addWidget(hint_top)

        columns = QHBoxLayout()
        columns.setSpacing(14)
        outer.addLayout(columns, 1)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)
        # v38: реквизиты занимают ровно свою ширину, всё «лишнее» место
        # забирают текстовые поля — раньше правая колонка уезжала за край.
        # v39.1: было 360 px — этого не хватало ни «Куратор отдела IAD …», ни
        # «О завершении проверки по обращению и передаче материалов в…».
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

        # ── постановление ────────────────────────────────────────────────
        left_layout.addWidget(self._label("Постановление", "Section"))
        doc_form = self._compact(QFormLayout())

        # v39: «Вид решения» — из ДВУХ частей (замечание пользователя):
        #   1) первичное решение по обращению (принять / отказать /
        #      переадресовать / прекратить) — по Регламенту, ст. 11–16, 18, 25;
        #   2) итоговое решение по существу — вид санкции (административная
        #      ответственность, уголовное дело, суд, меры реагирования) —
        #      по Регламенту, ст. 23 и Закону об ОГП, ст. 9, 33.
        # Вид санкции ИИ определяет сам по «Обстоятельствам», если оставить
        # в списке «Определит ИИ по обстоятельствам».
        # v39.1: полные подписи этапа не влезали в список и обрывались на
        # «(до 72 час» — в поле короткая подпись, полная — в подсказке.
        self.cb_stage = QComboBox()
        for stage_key in (gdata.GOV_STAGE_PRIMARY, gdata.GOV_STAGE_FINAL):
            self.cb_stage.addItem(gdata.GOV_STAGE_SHORT[stage_key], stage_key)
            self.cb_stage.setItemData(
                self.cb_stage.count() - 1,
                gdata.GOV_STAGE_TITLES[stage_key], Qt.ItemDataRole.ToolTipRole)
        self.cb_stage.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.cb_stage.setMinimumContentsLength(18)
        self.cb_stage.setToolTip(gdata.GOV_STAGE_TITLES[gdata.GOV_STAGE_PRIMARY])
        self.cb_stage.setMinimumWidth(200)
        self.cb_stage.currentIndexChanged.connect(
            lambda _i: self._on_stage_changed())
        doc_form.addRow(self._field_label("Этап"), self.cb_stage)

        self.cb_type = QComboBox()
        self.cb_type.setEditable(True)
        self.cb_type.setMinimumWidth(200)
        self._fill_types(gdata.GOV_STAGE_PRIMARY)
        self.cb_type.currentTextChanged.connect(
            lambda text: self._on_type_changed(text))
        doc_form.addRow(self._field_label("Вид решения"), self.cb_type)

        # Пояснение к этапу и виду: одна переносимая подпись вместо двух
        # строк, которые наезжали друг на друга и обрезались (v39.1).
        self.stage_hint = self._label("", "Caption")
        self.stage_hint.setWordWrap(True)
        self.stage_hint.setMinimumWidth(320)
        self.stage_hint.setTextFormat(Qt.TextFormat.PlainText)
        self.stage_hint.setSizePolicy(QSizePolicy.Policy.Preferred,
                                      QSizePolicy.Policy.Minimum)
        left_layout.addWidget(self.stage_hint)
        self.type_hint = self.stage_hint
        self._on_stage_changed()

        self.fields["doc_number"] = self._input("AG-937")
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
        doc_form.addRow(self._field_label("Номер / дата"), wrap)
        left_layout.addLayout(doc_form)

        # ── кто выносит ──────────────────────────────────────────────────
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

        # ── обращение: ОДНО поле целиком (v37) ───────────────────────────
        right_layout.addWidget(self._label("Обращение (вставить целиком)", "Section"))
        appeal_hint = self._label(
            "Копировать как есть: «От:», «В:», пункты 1–10, тайм-коды, подпись.",
            "Caption")
        appeal_hint.setWordWrap(True)
        right_layout.addWidget(appeal_hint)
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
        right_layout.addWidget(self.texts["appeal_raw"], 3)

        self.appeal_hint = self._label("", "Caption")
        self.appeal_hint.setWordWrap(True)
        right_layout.addWidget(self.appeal_hint)

        # ── обстоятельства: что прокурор увидел на видеофиксации ─────────
        right_layout.addWidget(self._label("Обстоятельства (что видно на видеофиксации)", "Section"))
        facts_hint = self._label(
            "Что и когда видно; тайм-коды и номера — дословно.", "Caption")
        facts_hint.setWordWrap(True)
        right_layout.addWidget(facts_hint)
        self.texts["situation"] = self._text_area(height=140)
        # v39.1: служебное «Необязательно…» прямо в placeholder
        # читалось как часть вставленных данных. Пояснение — отдельной
        # подписью под полем, а в поле только образец тайм-кодов.
        self.texts["situation"].setPlaceholderText(
            "00:04 — сотрудник с ОПЗ 8332 требует остановиться; "
            "02:09 — открывает огонь…")
        self.texts["situation"].textChanged.connect(self._refresh_ooc_hint)
        right_layout.addWidget(self.texts["situation"], 2)
        optional_hint = self._label(
            "Необязательно: пусто — обстоятельства ИИ соберёт из обращения.",
            "Caption")
        optional_hint.setWordWrap(True)
        right_layout.addWidget(optional_hint)

        # v36 (промт 1.19 п. 19): IC/OOC-термины не должны попадать в документ.
        self.ooc_hint = self._label("", "Caption")
        self.ooc_hint.setWordWrap(True)
        right_layout.addWidget(self.ooc_hint)

        # ── правовая часть: определяет ИИ, прокурор может уточнить ──────
        self.extra_toggle = QPushButton("Правовая часть и «Дополнительно» (необязательно)")
        self.extra_toggle.setCheckable(True)
        self.extra_toggle.setChecked(False)
        self.extra_toggle.setToolTip(
            "Обычно эти поля не нужны: норму, основание и решение определяет ИИ "
            "по обращению и обстоятельствам")
        self.extra_toggle.clicked.connect(lambda _c=False: self._toggle_extra())
        right_layout.addWidget(self.extra_toggle)

        self.extra_panel = QWidget()
        extra_form = self._compact(QFormLayout())
        self.extra_panel.setLayout(extra_form)
        self.fields["violation_act"] = self._input("Устав / закон / кодекс")
        self.fields["violation_clause"] = self._input("например: статья 17.6 УК")
        self.fields["penalty"] = self._input("решение: проверка / штраф / передача в суд")
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

        # ── папка сохранения ─────────────────────────────────────────────
        outer.addWidget(self._label("Сохранение", "Section"))
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
        outer.addLayout(folder_row)
        hint_folder = self._label(
            "После ответа ИИ здесь появится <номер>.png, собранный из ответа ИИ. "
            "Рядом — папка документа: заявка, ИИ-ответ.txt, ИИ-документ.txt и копия PNG.",
            "Caption")
        hint_folder.setWordWrap(True)
        outer.addWidget(hint_folder)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        # v38 (замечание «уезжает на право»): область прокрутки не должна
        # подстраиваться под ширину содержимого — иначе длинные строки
        # обращения растягивали форму и правый край уходил за окно.
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setSizeAdjustPolicy(
            QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored)
        scroll.setWidget(page)
        return scroll

    #: Подсказки этапов. Одна строка на этап: раньше две подписи подряд
    #: обрезались и наезжали друг на друга (v39.1).
    STAGE_HINTS = {
        gdata.GOV_STAGE_PRIMARY: (
            "Первичное решение (до 24 ч, ст. 12): принять, отказать по ст. 16, "
            "переадресовать по ст. 18–19 или прекратить по ст. 25. Санкция не "
            "назначается."),
        gdata.GOV_STAGE_FINAL: (
            "Итоговое решение (до 72 ч, ст. 23): вид санкции по "
            "«Обстоятельствам» и доказательствам."),
    }

    def _fill_types(self, stage: str) -> None:
        """Заполняет список видов решений для этапа (v39, v39.1).

        «Определит ИИ по обстоятельствам» стоит первым на ЛЮБОМ этапе: все
        четыре первичных решения выводятся из самого обращения, поэтому их
        тоже можно доверить ИИ (замечание пользователя).
        """
        self.cb_type.blockSignals(True)
        current = self.cb_type.currentText().strip()
        self.cb_type.clear()
        types = [gdata.GOV_DOC_TYPE_AUTO] + list(gdata.stage_types(stage))
        self.cb_type.addItems(types)
        if current in types:
            self.cb_type.setCurrentText(current)
        else:
            self.cb_type.setCurrentIndex(0)
        self.cb_type.blockSignals(False)

    def _on_stage_changed(self) -> None:
        """Смена этапа: пересобирает виды решений и подсказку (v39)."""
        if not hasattr(self, "cb_stage"):
            return
        stage = self.cb_stage.currentData() or gdata.GOV_STAGE_PRIMARY
        self._fill_types(stage)
        self._set_field("doc_stage", stage)
        if hasattr(self, "cb_stage"):
            # Полная формулировка этапа с нормами — в подсказке списка.
            self.cb_stage.setToolTip(gdata.GOV_STAGE_TITLES[stage])
        self._on_type_changed(self.cb_type.currentText())

    def _on_type_changed(self, text: str) -> None:
        """Вид решения выбран: подсказываем, чего не хватает для этапа (v39)."""
        text = (text or "").strip()
        self._set_field("doc_type", text or gdata.GOV_DOC_TYPE_AUTO)
        # Длинный вид решения не должен обрываться в поле: полный текст — в
        # подсказке, ширина списка подстраивается под содержимое (v39.1).
        if hasattr(self, "cb_type"):
            self.cb_type.setToolTip(text)
            self.cb_type.setMinimumContentsLength(min(30, max(16, len(text))))
        if not hasattr(self, "stage_hint"):
            return
        if text and text != gdata.GOV_DOC_TYPE_AUTO:
            stage = gdata.gov_stage(text)
            self.cb_stage.blockSignals(True)
            self.cb_stage.setCurrentIndex(0 if stage == gdata.GOV_STAGE_PRIMARY else 1)
            self.cb_stage.blockSignals(False)
        stage = self.cb_stage.currentData() or gdata.GOV_STAGE_PRIMARY
        if text == gdata.GOV_DOC_TYPE_AUTO:
            what = ("первичное решение по тексту обращения" if stage ==
                    gdata.GOV_STAGE_PRIMARY else
                    "вид санкции по «Обстоятельствам» и доказательствам")
            note = f"Определит ИИ: {what}."
        elif stage == gdata.GOV_STAGE_PRIMARY:
            note = "Нужен только текст обращения: проверяется форма и ст. 16."
        else:
            note = ("Нужны «Обстоятельства» (видно на видеофиксации) и норма "
                    "ответственности — санкция должна на что-то опираться.")
        self.stage_hint.setText(self.STAGE_HINTS[stage] + " " + note)
        self.stage_hint.setStyleSheet("color: #8f96bf; font-size: 11px;")

    def _toggle_extra(self) -> None:
        """Показывает/скрывает необязательную правовую часть."""
        visible = bool(self.extra_toggle.isChecked())
        if hasattr(self, "extra_panel"):
            self.extra_panel.setVisible(visible)

    def _on_appeal_changed(self) -> None:
        """Разбирает вставленное обращение и показывает, что нашлось (v37)."""
        if not hasattr(self, "appeal_hint"):
            return
        raw = self.texts["appeal_raw"].toPlainText()
        if not raw.strip():
            # v39.1: на скриншоте поле было ЗАПОЛНЕНО, а надпись «Поле пустое»
            # горела — сигнал приходил от программной очистки при загрузке
            # документа. Теперь пустое поле — это нейтральная подсказка, а
            # предупреждение (жёлтым) появляется только при реальной попытке
            # сформировать документ без обращения.
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
            ident = f" {parsed['violator_id']}" if parsed.get("violator_id") else ""
            org = f" ({parsed['violator_department']})" \
                if parsed.get("violator_department") else ""
            found.append(f"нарушитель {parsed['violator']}{ident}{org}")
        if parsed.get("appeal_subject"):
            subject = parsed["appeal_subject"]
            subject = subject[:60] + ("…" if len(subject) > 60 else "")
            found.append(f"предмет: {subject}")
        if parsed.get("event_date"):
            found.append(f"событие {parsed['event_date']}")
        missing = []
        if not parsed.get("description"):
            missing.append("нет пункта «Подробное описание ситуации» — опишите "
                           "обстоятельства сами")
        text = ("Распознано: " + "; ".join(found) + ".") if found else \
            "Реквизиты не распознаны — проверьте, что вставили обращение по форме."
        if missing:
            text += " " + " ".join(missing) + "."
        self.appeal_hint.setText(text)
        color = "#f59e0b" if (missing or not found) else "#34d399"
        self.appeal_hint.setStyleSheet(f"color: {color}; font-size: 11px;")

    def _build_history_tab(self) -> QWidget:
        page = QWidget()
        page.setObjectName("Sheet")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        layout.addWidget(self._label(
            "Сохранённые постановления — выберите запись и нажмите «Загрузить», "
            "чтобы поправить старое заполнение вместо повторного ввода. "
            "«Удалить» убирает выбранную запись, «Очистить историю» — все.", "Caption"))
        self.history_list = QListWidget()
        self.history_list.itemDoubleClicked.connect(lambda _i: self._load_history())
        layout.addWidget(self.history_list, 1)
        row = QHBoxLayout()
        refresh = QPushButton("Обновить")
        refresh.clicked.connect(lambda _c=False: self._refresh_history())
        # v38: удаление выбранного постановления из истории.
        self.history_delete_btn = QPushButton("Удалить")
        self.history_delete_btn.setToolTip(
            "Удалить выбранное постановление из истории")
        self.history_delete_btn.clicked.connect(
            lambda _c=False: self._delete_history())
        self.history_clear_btn = QPushButton("Очистить историю")
        self.history_clear_btn.setToolTip(
            "Удалить все сохранённые постановления из истории")
        self.history_clear_btn.clicked.connect(
            lambda _c=False: self._clear_history())
        delete = self.history_delete_btn
        clear = self.history_clear_btn
        load = QPushButton("Загрузить в форму")
        load.setObjectName("Primary")
        load.clicked.connect(lambda _c=False: self._load_history())
        row.addWidget(refresh)
        row.addWidget(delete)
        row.addWidget(clear)
        row.addStretch()
        row.addWidget(load)
        layout.addLayout(row)
        self._refresh_history()
        return page

    # ------------------------------------------------- удаление из истории (v38)

    def _history_index(self) -> int:
        """Выбранная запись истории (-1, если ничего не выбрано)."""
        if not hasattr(self, "history_list"):
            return -1
        return self.history_list.currentRow()

    def _delete_history(self) -> None:
        """Удаляет выбранное постановление из истории (v38).

        Замечание: «в «История» добавь возможность удалять «Постановления»».
        Удаляется запись истории — снимок полей; папка готового документа с
        PNG и текстами остаётся на диске (её могли уже отправить в чат).
        """
        index = self._history_index()
        if index < 0:
            QMessageBox.information(
                self, "Удаление",
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
        """Очищает историю постановлений целиком (v38)."""
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

    def _build_preview_tab(self) -> QWidget:
        page = QWidget()
        page.setObjectName("Sheet")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        self.preview_label = QLabel("Предпросмотра ещё нет — нажмите «Предпросмотр».")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setStyleSheet(
            "background: #ffffff; color: #444a66; border: 1px solid #262a4a;")
        self.preview_label.setMinimumHeight(360)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.preview_label)
        layout.addWidget(scroll, 1)
        self.preview_hint = self._label("", "Caption")
        layout.addWidget(self.preview_hint)
        return page

    # --------------------------------------------------------------- данные

    def _fill_defaults(self) -> None:
        """Автозаполнение из Шаблона активного персонажа (v37).

        Отдельных «полей для бланка» больше нет: имя, удостоверение и
        должность берутся из основных полей персонажа, город всегда
        Лос-Сантос.
        """
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
        """Предупреждает о внеигровых терминах прямо при вводе (v36, промт 1.19)."""
        if not hasattr(self, "ooc_hint"):
            return
        parts = [self.texts["situation"].toPlainText(),
                 self.texts["appeal_raw"].toPlainText(),
                 self.texts["legal_basis"].toPlainText(),
                 self.texts["extra"].toPlainText()]
        parts += [w.text() for w in self.fields.values() if hasattr(w, "text")]
        found = pdata.ooc_terms(" ".join(parts))
        if not found:
            self.ooc_hint.setText("Внеигровых терминов (IC/OOC) в тексте нет.")
            self.ooc_hint.setStyleSheet("color: #34d399; font-size: 11px;")
            return
        self.ooc_hint.setStyleSheet("color: #f59e0b; font-size: 11px;")
        self.ooc_hint.setText(
            "Найдены внеигровые термины: " + ", ".join(found) +
            ". В официальном документе их быть не должно — ИИ перепишет, "
            "но лучше сразу заменить: «в отсутствие законных и фактических оснований».")

    # ------------------------------------------------- папка сохранения (v28)

    def _documents_root(self) -> Path:
        """Корень папки постановлений — один и тот же для формы и для клиента.

        v30.2: это ВЫБРАННАЯ пользователем папка (config.postanovlenie_dir),
        по умолчанию `postanovleniya` рядом с программой. Раньше PNG писались
        сразу из формы, а ответ ИИ — в `%APPDATA%\Legalyze`, и «ИИ-документ»
        не попадал ни в выбранную папку, ни в PNG.
        """
        try:
            from config import postanovlenie_dir
            return postanovlenie_dir(self.cfg)
        except Exception:
            diag.exception("ui.postanovlenie.root")
            return self.base_dir / "postanovleniya"

    def _current_folder(self) -> Path:
        """Папка готовых постановлений — та же, куда пишется ответ ИИ."""
        return self._documents_root()

    def _current_folder_text(self) -> str:
        return f"PNG сохраняется сюда: {self._documents_root()}"

    def _choose_folder(self) -> None:
        start = str(self._current_folder())
        chosen = QFileDialog.getExistingDirectory(self, "Куда сохранять постановления", start)
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

    def _collect(self) -> gdata.PostanovlenieProkurora:
        """Собирает документ. Обращение разбирается в реквизиты (v37).

        Заявитель, нарушитель, предмет, дата события, тайм-коды и подпись
        достаются из текста обращения, поэтому в форме для них нет полей.
        """
        doc = gdata.PostanovlenieProkurora.from_dict(self.document.to_dict())
        for key, widget in self.fields.items():
            if widget is not None and hasattr(widget, "text"):
                doc.set(key, widget.text().strip())
        # v39: этап и вид решения. «Определит ИИ» сохраняем как есть — в заявке
        # это уходит требованием определить санкцию по обстоятельствам.
        doc.doc_stage = (self.cb_stage.currentData()
                         if hasattr(self, "cb_stage") else "") or gdata.GOV_STAGE_PRIMARY
        doc.doc_type = self.cb_type.currentText().strip() or doc.doc_type
        doc.city = doc.city or gdata.GOV_CITY or "г. Лос-Сантос"
        raw_appeal = self.texts["appeal_raw"].toPlainText().strip()
        for key, widget in self.texts.items():
            if key == "appeal_raw":
                continue
            doc.set(key, widget.toPlainText().strip())
        # v37: поле «Обстоятельства» — собственное описание прокурора по
        # видеофиксации. Текст заявителя из обращения живёт отдельно
        # (`appeal_situation`), иначе «УСТАНОВИЛ» дублировал весь пересказ.
        doc.prosecutor_facts = doc.get("situation")
        doc.apply_appeal(raw_appeal)
        if not doc.doc_date:
            doc.doc_date = pdata.today_ru()
        return doc

    def set_document(self, doc: gdata.PostanovlenieProkurora) -> None:
        self.document = gdata.PostanovlenieProkurora.from_dict(doc.to_dict())
        for key, widget in self.fields.items():
            if widget is not None and hasattr(widget, "setText"):
                widget.setText(doc.get(key))
        if hasattr(self, "cb_type"):
            # v39: сначала этап (от него зависит список видов решений), затем
            # сам вид — иначе загруженный из истории «О привлечении к
            # административной ответственности» терялся в первичном списке.
            stage = doc.stage() if hasattr(doc, "stage") else gdata.GOV_STAGE_PRIMARY
            if hasattr(self, "cb_stage"):
                self.cb_stage.blockSignals(True)
                self.cb_stage.setCurrentIndex(
                    0 if stage == gdata.GOV_STAGE_PRIMARY else 1)
                self.cb_stage.blockSignals(False)
                self._fill_types(stage)
            doc_type = doc.get("doc_type") or gdata.stage_types(stage)[0]
            self.cb_type.setCurrentText(doc_type)
            self._on_type_changed(doc_type)
        situation = doc.get("situation") or doc.prosecutor_facts
        for key, widget in self.texts.items():
            if key == "appeal_raw":
                continue
            widget.setPlainText(situation if key == "situation" else doc.get(key))
        if hasattr(self, "texts") and "appeal_raw" in self.texts:
            self.texts["appeal_raw"].setPlainText(doc.appeal_raw_text())
            if hasattr(self, "appeal_hint"):
                self._on_appeal_changed()
        self.signature_hint.setText(
            f"Подпись в документе: {pdata.initials_signature(doc.author_name) or '—'}")
        self._refresh_ooc_hint()
        self.status.setText("Данные загружены — проверьте и нажмите «Сформировать»")

    def _refresh_history(self) -> None:
        self.history_list.clear()
        for label in self.history.labels():
            self.history_list.addItem(label)
        # v38: при пустой истории в списке стоит подсказка, а не запись, —
        # удалять из неё нечего, поэтому «Удалить»/«Очистить» не активны.
        empty = not self.history.items()
        if empty:
            self.history_list.addItem("История пуста — сформированных постановлений ещё нет")
        self.history_list.setEnabled(not empty)
        for name in ("history_delete_btn", "history_clear_btn"):
            button = getattr(self, name, None)
            if button is not None:
                button.setEnabled(not empty)

    def _load_history(self) -> None:
        index = self.history_list.currentRow()
        doc = self.history.load_index(index) if index >= 0 else None
        if doc is None:
            return
        self.set_document(doc)
        self.tabs.setCurrentIndex(0)

    # -------------------------------------------------------------- действия

    def _make_preview(self):
        doc = self._collect()
        missing = doc.missing_fields()
        if missing:
            QMessageBox.information(
                self, "Не хватает данных",
                "Заполните обязательные поля:\n• " + "\n• ".join(missing))
            return None
        try:
            pages = render.render_document(doc, self.assets)
        except Exception as exc:
            diag.exception("ui.postanovlenie.preview")
            QMessageBox.warning(self, "Ошибка", f"Не удалось построить документ: {exc}")
            return None
        # v30.2: предпросмотр — во временную папку системы, чтобы рядом с
        # программой/на рабочем столе не оставалось «лишних файлов».
        import tempfile
        folder = Path(tempfile.gettempdir()) / "legalyze-preview"
        paths = render.save_pages(pages, folder, f"preview-{doc.doc_number or 'doc'}")
        self.png_paths = paths
        from PyQt6.QtGui import QPixmap
        pixmap = QPixmap(str(paths[0]))
        if not pixmap.isNull():
            self.preview_label.setPixmap(pixmap.scaledToWidth(
                560, Qt.TransformationMode.SmoothTransformation))
        self.preview_hint.setText(
            f"Готово: {len(paths)} стр. · " + " · ".join(p.name for p in paths))
        self.tabs.setCurrentIndex(2)
        self.status.setText("Предпросмотр готов")
        return paths

    def _save_only(self):
        doc = self._collect()
        if doc.missing_fields():
            QMessageBox.information(self, "Не хватает данных",
                                    "Заполните обязательные поля")
            return
        self.history.save(doc)
        self._refresh_history()
        QMessageBox.information(self, "Сохранено",
                                "Постановление добавлено в историю. Отправка в чат не выполнялась.")
        self.accept()

    def _form(self):
        doc = self._collect()
        # v28: «нарушение и взыскание» можно не заполнять — их определит ИИ
        # из обстоятельств. Обязательны только реквизиты и обстоятельства.
        required_missing = [name for name in doc.missing_fields()
                            if name not in ("Нарушенный акт", "Статьи / пункты",
                                            "Наказание / решение")]
        if not doc.get("appeal_raw") and not doc.get("situation"):
            required_missing.append("Обращение")
        if required_missing:
            QMessageBox.information(
                self, "Не хватает данных",
                "Заполните обязательные поля:\n• " + "\n• ".join(required_missing))
            return
        try:
            # v30.2 (замечание): на «Сформировать» PNG из формы БОЛЬШЕ НЕ ПЕЧАТАЕТСЯ.
            # Бланк собирается только из ответа ИИ (ИИ-документ.txt -> PNG), иначе
            # на экране оказывались данные конструктора, а не постановление ИИ.
            # Здесь готовим только заявку для чата и папку выдачи.
            # v30.2 (замечание «лишние файлы на рабочем столе»): заявка НЕ
            # плодится отдельными копиями — ровно один файл в папке выдачи
            # `postanovleniya/<номер>/`, он же прикрепляется к чату.
            out = pdata.doc_folder(self._documents_root(), doc.get("doc_number"))
            chat_txt = out / "Заявка (отправлено в чат).txt"
            chat_txt.write_text(doc.to_chat_txt(), encoding="utf-8")
            self.chat_txt_path = str(chat_txt)
            self.png_paths = []
            (out / "Заявка-поля.json").write_text(
                json.dumps({"когда": doc.history_payload().get("human_created"),
                            "поля": doc.to_dict()}, ensure_ascii=False, indent=2),
                encoding="utf-8")
            self.history.save(doc)
            self._refresh_history()
        except Exception as exc:
            diag.exception("ui.postanovlenie.form")
            QMessageBox.warning(self, "Ошибка", f"Не удалось сформировать документ: {exc}")
            return
        self.document = doc
        self.formed.emit(doc, self.chat_txt_path, self.png_paths)
        self.accept()

    # --------------------------------------------------------- перетаскивание

    def showEvent(self, event):
        """Окно не должно открываться за краем экрана (v39.1).

        Замечание: «у левого края экрана видны обрезанные буквы» — форма
        1240×880 на маленьком экране вылезала за границы. Прижимаем размер к
        доступной области экрана и центрируем внутри неё.
        """
        super().showEvent(event)
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
