"""postanovlenie_ui.py — форма-конструктор постановления (v27).

Окно сделано в том же стиле, что «Шаблон» и «Промт»: тёмная карточка,
перетаскивание мышью, кнопки внизу. Это первая из будущих форм
(жалобы/иски/обращения), поэтому название окна универсальное:
«Документы» → «Постановление».

Что умеет форма:
* собирает данные постановления по полям (`postanovlenie_data.DOC_FIELDS`);
* автоматически подставляет ФИО/должность из Шаблон.txt;
* сама предлагает следующий номер документа и дату;
* хранит историю: «История» → выбрать запись → «Загрузить» → править;
* предпросмотр: собрать и сохранить PNG прямо из формы (без чата);
* «Сформировать» — то, что требует задача: .txt в чат + рендер PNG.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QFont, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QTabWidget, QVBoxLayout, QWidget,
    QListWidget,
)

import diagnostics as diag
import doc_render as render
import postanovlenie_data as pdata


class PostanovlenieDialog(QDialog):
    """Форма постановления. Возвращает `document` и `chat_txt` после «Сформировать»."""

    #: Документ сформирован: (Postanovlenie, путь к .txt для чата, [png...]).
    formed = pyqtSignal(object, str, object)

    def __init__(self, cfg, base_dir=None, history=None, assets=None,
                 regulation=None, parent=None):
        super().__init__(parent)
        self.cfg = cfg or {}
        self.base_dir = Path(base_dir) if base_dir else Path(__file__).resolve().parent
        self.assets = assets or render.DocAssets(self.base_dir)
        self.regulation = regulation
        self.history = history or pdata.DocumentHistory(self.base_dir / "postanovleniya")
        self.document = pdata.Postanovlenie()
        self.chat_txt_path = ""
        self.png_paths: list = []
        self._drag_pos = None

        self.setWindowTitle("Документы — Постановление")
        self.setMinimumSize(760, 720)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Dialog
        )
        self.setModal(True)
        self.setStyleSheet(self._style())
        self._build()
        self._fill_defaults()

    # ------------------------------------------------------------------ стиль

    @staticmethod
    def _style() -> str:
        return """
        QDialog { background: #151926; }
        QLabel { color: #dfe4f3; font-size: 12px; }
        QLabel#Title { color: #7c83ff; font-size: 18px; font-weight: 700; }
        QLabel#Caption { color: #8b93ab; font-size: 11px; }
        QLabel#Section { color: #aab2cc; font-size: 12px; font-weight: 700; }
        QLineEdit, QPlainTextEdit, QComboBox {
            background: #1d2233; color: #e8ecf8; border: 1px solid #2c3348;
            border-radius: 7px; padding: 7px 9px; font-size: 12px;
            selection-background-color: #3b4a8f;
        }
        QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus { border-color: #7c83ff; }
        QComboBox QAbstractItemView {
            background: #1d2233; color: #e8ecf8; border: 1px solid #2c3348;
            selection-background-color: #3b4a8f;
        }
        QPushButton {
            background: rgba(255,255,255,0.05); color: #dfe4f3;
            border: 1px solid #2c3348; border-radius: 7px;
            padding: 8px 14px; font-size: 12px; font-weight: 600;
        }
        QPushButton:hover { background: rgba(124,131,255,0.22); border-color: #7c83ff; }
        QPushButton#Primary { background: rgba(124,131,255,0.9); color: #0d1020; }
        QPushButton#Primary:hover { background: #9aa1ff; }
        QPushButton#Primary:disabled { background: #2c3348; color: #6d7488; }
        QTabWidget::pane { border: 1px solid #2c3348; border-radius: 8px; }
        QTabBar::tab {
            background: #1a1f2e; color: #98a0b8; padding: 8px 16px;
            border: 1px solid #2c3348; border-bottom: none;
            border-top-left-radius: 7px; border-top-right-radius: 7px;
        }
        QTabBar::tab:selected { background: #232a3d; color: #dfe4f3; }
        QScrollArea { border: none; }
        QListWidget {
            background: #1a1f2e; color: #dfe4f3; border: 1px solid #2c3348;
            border-radius: 7px; font-size: 12px;
        }
        QListWidget::item:selected { background: #3b4a8f; }
        """

    def _label(self, text: str, kind: str = "") -> QLabel:
        label = QLabel(text)
        if kind:
            label.setObjectName(kind)
        return label

    def _input(self, placeholder: str = "") -> QLineEdit:
        field = QLineEdit()
        field.setPlaceholderText(placeholder)
        field.setMinimumHeight(34)
        return field

    # ------------------------------------------------------------- построение

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

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
        self.btn_close = QPushButton("Закрыть")
        self.btn_close.clicked.connect(lambda _c=False: self.reject())
        buttons.addWidget(self.btn_close)
        buttons.addStretch()
        self.btn_preview = QPushButton("Предпросмотр")
        self.btn_preview.clicked.connect(lambda _c=False: self._make_preview())
        buttons.addWidget(self.btn_preview)
        self.btn_save = QPushButton("Сохранить без отправки")
        self.btn_save.clicked.connect(lambda _c=False: self._save_only())
        buttons.addWidget(self.btn_save)
        self.btn_form = QPushButton("Сформировать")
        self.btn_form.setObjectName("Primary")
        self.btn_form.clicked.connect(lambda _c=False: self._form())
        buttons.addWidget(self.btn_form)
        root.addLayout(buttons)

    def _build_form_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(8)

        self.fields: dict = {}
        self.texts: dict = {}

        # ── документ ──────────────────────────────────────────────────────
        outer.addWidget(self._label("Документ", "Section"))
        doc_form = QFormLayout()
        doc_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        doc_form.setHorizontalSpacing(12)
        doc_form.setVerticalSpacing(8)

        self.cb_type = QComboBox()
        self.cb_type.addItems(pdata.DOC_TYPES)
        self.cb_type.setEditable(True)
        self.cb_type.currentTextChanged.connect(
            lambda text: self._set_field("doc_type", text))
        doc_form.addRow(self._label("Вид"), self.cb_type)

        self.fields["doc_number"] = self._input("LSCSD-IAD-141")
        self.fields["doc_date"] = self._input("06.11.2026")
        number_row = QHBoxLayout()
        number_row.addWidget(self.fields["doc_number"], 2)
        number_row.addWidget(self.fields["doc_date"], 1)
        suggestion = QPushButton("Следующий №")
        suggestion.setFixedWidth(120)
        suggestion.clicked.connect(lambda _c=False: self._suggest_number())
        number_row.addWidget(suggestion)
        wrap = QWidget()
        wrap.setLayout(number_row)
        number_row.setContentsMargins(0, 0, 0, 0)
        doc_form.addRow(self._label("Номер / дата"), wrap)

        self.fields["vedomstvo"] = self._input("LOS SANTOS COUNTY SHERIFF'S DEPARTMENT")
        self.fields["podrazdelenie"] = self._input("INTERNAL AFFAIRS DIVISION")
        doc_form.addRow(self._label("Ведомство"), self.fields["vedomstvo"])
        doc_form.addRow(self._label("Подразделение"), self.fields["podrazdelenie"])
        outer.addLayout(doc_form)

        # ── кто выносит / в отношении кого ────────────────────────────────
        outer.addWidget(self._label("Кто выносит постановление", "Section"))
        who = QFormLayout()
        who.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        who.setHorizontalSpacing(12)
        who.setVerticalSpacing(8)
        self.fields["author_name"] = self._input("Kirill Goldens")
        self.fields["author_rank"] = self._input("Major (Майор IAD)")
        who.addRow(self._label("ФИО"), self.fields["author_name"])
        who.addRow(self._label("Должность"), self.fields["author_rank"])
        self.signature_hint = self._label("Подпись: —", "Caption")
        who.addRow(self._label(""), self.signature_hint)
        self.fields["author_name"].textChanged.connect(
            lambda text: self.signature_hint.setText(
                f"Подпись в документе: {pdata.initials_signature(text) or '—'}"))
        outer.addLayout(who)

        outer.addWidget(self._label("В отношении кого", "Section"))
        subject = QFormLayout()
        subject.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        subject.setHorizontalSpacing(12)
        subject.setVerticalSpacing(8)
        self.fields["subject_name"] = self._input("Qorick Mythic")
        self.fields["subject_id"] = self._input("166759")
        self.fields["subject_rank"] = self._input("Cadet (необязательно)")
        self.fields["subject_department"] = self._input("LSCSD / Patrol (необязательно)")
        subject.addRow(self._label("ФИО"), self.fields["subject_name"])
        subject.addRow(self._label("Удостоверение №"), self.fields["subject_id"])
        subject.addRow(self._label("Звание"), self.fields["subject_rank"])
        subject.addRow(self._label("Отдел"), self.fields["subject_department"])
        outer.addLayout(subject)

        # ── нарушение и взыскание ─────────────────────────────────────────
        outer.addWidget(self._label("Нарушение и взыскание", "Section"))
        violation = QFormLayout()
        violation.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        violation.setHorizontalSpacing(12)
        violation.setVerticalSpacing(8)
        self.fields["violation_act"] = self._input(
            "Внутреннего устава Los Santos County Sheriff Department")
        # В уставе LSCSD нумерация пунктов вида «3.1», «4.1», «10.6» — без
        # деления на статьи и части, поэтому подсказка сразу в этом формате.
        self.fields["violation_clause"] = self._input("пункт 3.1 устава")
        self.cb_penalty = QComboBox()
        self.cb_penalty.setEditable(True)
        penalties = (self.regulation.penalties() if self.regulation else None) or pdata.PENALTIES
        self.cb_penalty.addItems(list(penalties))
        self.cb_penalty.currentTextChanged.connect(lambda text: self._set_field("penalty", text))
        violation.addRow(self._label("Нарушенный акт"), self.fields["violation_act"])
        violation.addRow(self._label("Пункт / статья"), self.fields["violation_clause"])
        violation.addRow(self._label("Взыскание"), self.cb_penalty)
        self.clause_hint = self._label("", "Caption")
        self.clause_hint.setWordWrap(True)
        violation.addRow(self._label(""), self.clause_hint)
        self.fields["violation_clause"].textChanged.connect(self._check_clause)
        outer.addLayout(violation)

        # ── обстоятельства ───────────────────────────────────────────────
        outer.addWidget(self._label("Обстоятельства", "Section"))
        self.texts["situation"] = QPlainTextEdit()
        self.texts["situation"].setPlaceholderText(
            "Кратко: что произошло, когда, при каких обстоятельствах.")
        self.texts["situation"].setFixedHeight(96)
        outer.addWidget(self.texts["situation"])

        self.texts["evidence"] = QPlainTextEdit()
        self.texts["evidence"].setPlaceholderText(
            "Доказательства: рапорт, запись бодикамеры, показания (необязательно).")
        self.texts["evidence"].setFixedHeight(64)
        outer.addWidget(self.texts["evidence"])

        self.texts["extra"] = QPlainTextEdit()
        self.texts["extra"].setPlaceholderText(
            "Дополнительно (необязательно): особые указания, сроки, примечания.")
        self.texts["extra"].setFixedHeight(56)
        outer.addWidget(self.texts["extra"])

        outer.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        return scroll

    def _build_history_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        layout.addWidget(self._label(
            "Сохранённые постановления — выберите запись и нажмите «Загрузить», "
            "чтобы поправить старое заполнение вместо повторного ввода.", "Caption"))
        self.history_list = QListWidget()
        self.history_list.itemDoubleClicked.connect(lambda _i: self._load_history())
        layout.addWidget(self.history_list, 1)
        row = QHBoxLayout()
        refresh = QPushButton("Обновить")
        refresh.clicked.connect(lambda _c=False: self._refresh_history())
        load = QPushButton("Загрузить в форму")
        load.setObjectName("Primary")
        load.clicked.connect(lambda _c=False: self._load_history())
        row.addWidget(refresh)
        row.addStretch()
        row.addWidget(load)
        layout.addLayout(row)
        self._refresh_history()
        return page

    def _build_preview_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        self.preview_label = QLabel("Предпросмотра ещё нет — нажмите «Предпросмотр».")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setStyleSheet("background: #0f1320; border: 1px solid #2c3348;")
        self.preview_label.setMinimumHeight(420)
        self.preview_label.setScaledContents(False)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.preview_label)
        layout.addWidget(scroll, 1)
        self.preview_hint = self._label("", "Caption")
        layout.addWidget(self.preview_hint)
        return page

    # --------------------------------------------------------------- данные

    def _fill_defaults(self) -> None:
        """Автозаполнение из Шаблон.txt: ФИО, звание/должность, ведомство, префикс.

        Задача v27 требует минимум повторного ввода: что уже есть в шаблоне —
        подставляется, остальное пользователь дописывает в форме.
        """
        template = self.cfg.get("template") or {}
        doc = pdata.Postanovlenie()
        doc.author_name = str(template.get("Name", "") or "").strip()
        rank_title = str(template.get("RankTitle", "") or "").strip()
        position = str(template.get("PositionFull", "") or "").strip()
        job = str(template.get("JobTitle", "") or "").strip()
        joined = " — ".join(x for x in (rank_title, position or job) if x)
        doc.author_rank = joined or str(template.get("Rang", "") or "").strip()
        if str(template.get("Authority", "") or "").strip():
            doc.vedomstvo = str(template["Authority"]).strip()
        if self.regulation and self.regulation.penalties():
            doc.penalty = self.regulation.penalties()[0] if self.regulation.penalties() else ""
        self.set_document(doc)
        self._suggest_number()

    def _doc_prefix(self) -> str:
        template = self.cfg.get("template") or {}
        prefix = str(template.get("DocPrefix", "") or "").strip()
        return prefix or "LSCSD-IAD"

    def _suggest_number(self) -> None:
        number = self.document.auto_number(self.history.used_numbers(), prefix=self._doc_prefix())
        self.fields["doc_number"].setText(number)

    def _set_field(self, key: str, value) -> None:
        self.fields.setdefault(key, None)
        self.document.set(key, value)

    def _check_clause(self, text: str) -> None:
        if not self.regulation:
            return
        found = self.regulation.clause_text(text)
        if found:
            self.clause_hint.setStyleSheet("color: #63d29b; font-size: 11px;")
            self.clause_hint.setText("Найдено в уставе: " + found[:180].replace("\n", " "))
        elif str(text).strip():
            self.clause_hint.setStyleSheet("color: #f0b45c; font-size: 11px;")
            self.clause_hint.setText(
                "В загруженном уставе такой пункт не найден — проверьте формулировку.")

    def _collect(self) -> pdata.Postanovlenie:
        doc = pdata.Postanovlenie.from_dict(self.document.to_dict())
        for key, widget in self.fields.items():
            if widget is not None and hasattr(widget, "text"):
                doc.set(key, widget.text().strip())
        doc.doc_type = self.cb_type.currentText().strip() or doc.doc_type
        doc.penalty = self.cb_penalty.currentText().strip() or doc.penalty
        for key, widget in self.texts.items():
            doc.set(key, widget.toPlainText().strip())
        if not doc.doc_date:
            doc.doc_date = pdata.today_ru()
        return doc

    def set_document(self, doc: pdata.Postanovlenie) -> None:
        self.document = pdata.Postanovlenie.from_dict(doc.to_dict())
        for key, widget in self.fields.items():
            if widget is not None and hasattr(widget, "setText"):
                widget.setText(doc.get(key))
        if hasattr(self, "cb_type"):
            self.cb_type.setCurrentText(doc.get("doc_type") or pdata.DOC_TYPES[0])
        if hasattr(self, "cb_penalty"):
            self.cb_penalty.setCurrentText(doc.get("penalty"))
        for key, widget in self.texts.items():
            widget.setPlainText(doc.get(key))
        self.signature_hint.setText(
            f"Подпись в документе: {pdata.initials_signature(doc.author_name) or '—'}")
        self._check_clause(doc.get("violation_clause"))
        self.status.setText("Данные загружены — проверьте и нажмите «Сформировать»")

    def _refresh_history(self) -> None:
        self.history_list.clear()
        for label in self.history.labels():
            self.history_list.addItem(label)
        if not self.history_list.count():
            self.history_list.addItem("История пуста — сформированных постановлений ещё нет")
            self.history_list.setEnabled(False)
        else:
            self.history_list.setEnabled(True)

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
        folder = self.base_dir / "postanovleniya" / "preview"
        paths = render.save_pages(pages, folder, f"preview-{doc.doc_number or 'doc'}")
        self.png_paths = paths
        pixmap = QPixmap(str(paths[0]))
        if not pixmap.isNull():
            self.preview_label.setPixmap(pixmap.scaledToWidth(
                620, Qt.TransformationMode.SmoothTransformation))
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
        missing = doc.missing_fields()
        if missing:
            QMessageBox.information(
                self, "Не хватает данных",
                "Заполните обязательные поля:\n• " + "\n• ".join(missing))
            return
        try:
            pages = render.render_document(doc, self.assets)
            folder = self.base_dir / "postanovleniya" / "png"
            self.png_paths = render.save_pages(
                pages, folder, doc.doc_number or "postanovlenie")
            chat_txt = self.base_dir / "postanovleniya" / "chat" / doc.chat_txt_name()
            chat_txt.parent.mkdir(parents=True, exist_ok=True)
            chat_txt.write_text(doc.to_chat_txt(), encoding="utf-8")
            self.chat_txt_path = str(chat_txt)
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
