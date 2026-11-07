"""postanovlenie_ui.py — форма-конструктор постановления (v28).

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
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QTabWidget,
    QVBoxLayout, QWidget,
)

import diagnostics as diag
import doc_render as render
import postanovlenie_data as pdata


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
        self.history = history or pdata.DocumentHistory(
            (Path(base_dir) if base_dir else Path(__file__).resolve().parent) / "postanovleniya")
        self.document = pdata.Postanovlenie()
        self.chat_txt_path = ""
        self.png_paths: list = []
        self._drag_pos = None

        self.setWindowTitle("Документы — Постановление")
        self.setMinimumSize(720, 640)
        self.resize(780, 720)
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
        return field

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
        page = QWidget()
        page.setObjectName("Sheet")
        outer = QVBoxLayout(page)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(6)

        self.fields: dict = {}
        self.texts: dict = {}

        # ── документ ──────────────────────────────────────────────────────
        outer.addWidget(self._label("Документ", "Section"))
        doc_form = self._compact(QFormLayout())

        self.cb_type = QComboBox()
        self.cb_type.addItems(pdata.DOC_TYPES)
        self.cb_type.setEditable(True)
        self.cb_type.currentTextChanged.connect(
            lambda text: self._set_field("doc_type", text))
        doc_form.addRow(self._field_label("Вид"), self.cb_type)

        self.fields["doc_number"] = self._input("LSCSD-IAD-141")
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

        self.fields["vedomstvo"] = self._input("LOS SANTOS COUNTY SHERIFF'S DEPARTMENT")
        self.fields["podrazdelenie"] = self._input("INTERNAL AFFAIRS DIVISION")
        doc_form.addRow(self._field_label("Ведомство"), self.fields["vedomstvo"])
        doc_form.addRow(self._field_label("Подразделение"), self.fields["podrazdelenie"])
        outer.addLayout(doc_form)

        # ── кто выносит ───────────────────────────────────────────────────
        outer.addWidget(self._label("Кто выносит постановление", "Section"))
        who = self._compact(QFormLayout())
        self.fields["author_name"] = self._input("Marcus Nomad")
        self.fields["author_rank"] = self._input("Куратор отдела IAD (Assistent sheriff)")
        who.addRow(self._field_label("ФИО"), self.fields["author_name"])
        who.addRow(self._field_label("Должность"), self.fields["author_rank"])
        self.signature_hint = self._label("Подпись: —", "Caption")
        who.addRow(self._label(""), self.signature_hint)
        self.fields["author_name"].textChanged.connect(
            lambda text: self.signature_hint.setText(
                f"Подпись в документе: {pdata.initials_signature(text) or '—'}"))
        outer.addLayout(who)

        # ── в отношении кого ──────────────────────────────────────────────
        outer.addWidget(self._label("В отношении кого", "Section"))
        subject = self._compact(QFormLayout())
        self.fields["subject_name"] = self._input("Aliya Nomad")
        self.fields["subject_id"] = self._input("131485")
        self.fields["subject_rank"] = self._input("необязательно")
        self.fields["subject_department"] = self._input("необязательно")
        subject.addRow(self._field_label("ФИО"), self.fields["subject_name"])
        subject.addRow(self._field_label("Удостоверение №"), self.fields["subject_id"])
        subject.addRow(self._field_label("Звание"), self.fields["subject_rank"])
        subject.addRow(self._field_label("Отдел"), self.fields["subject_department"])
        outer.addLayout(subject)

        # ── обстоятельства (главное поле) ─────────────────────────────────
        outer.addWidget(self._label("Обстоятельства", "Section"))
        hint = self._label(
            "Опишите ситуацию своими словами — ИИ сам приведёт формулировки, "
            "должности и правовую часть к официальному виду.", "Caption")
        hint.setWordWrap(True)
        outer.addWidget(hint)
        self.texts["situation"] = QPlainTextEdit()
        self.texts["situation"].setPlaceholderText(
            "Что произошло, когда, при каких обстоятельствах, кто нарушил и что именно.")
        self.texts["situation"].setMinimumHeight(110)
        self.texts["situation"].textChanged.connect(self._update_penalty_hint)
        outer.addWidget(self.texts["situation"])
        # v33: ориентир по взысканию — не всё сводится к выговору.
        self.penalty_hint = self._label("", "Caption")
        self.penalty_hint.setWordWrap(True)
        outer.addWidget(self.penalty_hint)

        # ── нарушение и взыскание: НЕОБЯЗАТЕЛЬНО (v28) ────────────────────
        optional_head = QHBoxLayout()
        optional_head.addWidget(self._label("Нарушение и взыскание (необязательно)", "Section"))
        optional_head.addStretch()
        self.violation_hint = self._label(
            "Не заполняйте — ИИ подберёт взыскание по тяжести проступка и нормы",
            "Caption")
        optional_head.addWidget(self.violation_hint)
        outer.addLayout(optional_head)

        violation = self._compact(QFormLayout())
        self.fields["violation_act"] = self._input("Устав Los Santos County Sheriff Department")
        self.fields["violation_clause"] = self._input("например: 3.1 (если знаете пункт)")
        self.cb_penalty = QComboBox()
        self.cb_penalty.setEditable(True)
        penalties = (self.regulation.penalties() if self.regulation else None) or pdata.PENALTIES
        self.cb_penalty.addItem("")  # пусто = «пусть решит ИИ»
        self.cb_penalty.addItems(list(penalties))
        self.cb_penalty.currentTextChanged.connect(lambda text: self._set_field("penalty", text))
        violation.addRow(self._field_label("Нарушенный акт"), self.fields["violation_act"])
        violation.addRow(self._field_label("Пункт / статья"), self.fields["violation_clause"])
        # v33: основание шире устава — УК, процессуальные кодексы, иные акты.
        self.texts["legal_basis"] = QPlainTextEdit()
        self.texts["legal_basis"].setPlaceholderText(
            "Другие нормы «в части касающейся»: статьи УК, процессуального "
            "кодекса, КоАП, иных актов (необязательно — ИИ подберёт сам).")
        self.texts["legal_basis"].setFixedHeight(56)
        self.texts["legal_basis"].textChanged.connect(
            lambda: self._set_field("legal_basis", self.texts["legal_basis"].toPlainText()))
        violation.addRow(self._field_label("Нормативное основание"),
                         self.texts["legal_basis"])
        violation.addRow(self._field_label("Взыскание"), self.cb_penalty)
        self.clause_hint = self._label("", "Caption")
        self.clause_hint.setWordWrap(True)
        violation.addRow(self._label(""), self.clause_hint)
        self.fields["violation_clause"].textChanged.connect(self._check_clause)
        outer.addLayout(violation)

        # ── куда сохранять PNG ────────────────────────────────────────────
        outer.addWidget(self._label("Сохранение", "Section"))
        folder_row = QHBoxLayout()
        folder_row.setSpacing(6)
        self.folder_label = self._label(self._current_folder_text(), "Caption")
        self.folder_label.setWordWrap(True)
        # v31: пользователь должен видеть, где окажется PNG и откуда он берётся.
        hint_folder = self._label(
            "После ответа ИИ здесь появится <номер>.png, собранный из ответа ИИ. "
            "Рядом — папка документа: заявка, ИИ-ответ.txt и копия PNG.", "Caption")
        hint_folder.setWordWrap(True)
        choose_folder = QPushButton("Выбрать папку…")
        choose_folder.clicked.connect(lambda _c=False: self._choose_folder())
        open_folder = QPushButton("Открыть")
        open_folder.clicked.connect(lambda _c=False: self._open_folder())
        folder_row.addWidget(self.folder_label, 1)
        folder_row.addWidget(choose_folder)
        folder_row.addWidget(open_folder)
        outer.addLayout(folder_row)
        outer.addWidget(hint_folder)

        outer.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        return scroll

    def _build_history_tab(self) -> QWidget:
        page = QWidget()
        page.setObjectName("Sheet")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 10, 12, 10)
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

        Отдельных «полей для бланка» больше нет: ведомство — это фракция,
        подразделение — отдел, должность — «Должность» из шаблона (она же
        звание), удостоверение — ID.
        """
        from config import document_defaults
        defaults = document_defaults(self.cfg)
        doc = pdata.Postanovlenie()
        doc.author_name = defaults["author_name"]
        doc.author_rank = defaults["author_rank"] or defaults["rank"]
        if defaults["vedomstvo"]:
            doc.vedomstvo = defaults["vedomstvo"]
        if defaults["division"]:
            doc.podrazdelenie = defaults["division"]
        self.set_document(doc)
        self._suggest_number()

    def _doc_prefix(self) -> str:
        from config import document_defaults
        return document_defaults(self.cfg).get("doc_prefix") or "LSCSD-IAD"

    def _suggest_number(self) -> None:
        number = self.document.auto_number(self.history.used_numbers(), prefix=self._doc_prefix())
        self.fields["doc_number"].setText(number)

    def _update_penalty_hint(self) -> None:
        """Ориентир по взысканию по тексту обстоятельств (v33)."""
        try:
            text = self.texts["situation"].toPlainText()
            suggested = pdata.suggest_penalty(text)
        except Exception:
            suggested = ""
        if not suggested:
            self.penalty_hint.setText("")
            return
        self.penalty_hint.setText(
            f"По обстоятельствам соразмерно взыскание: {suggested}. "
            "Можно выбрать другое — или оставить пустым, тогда решит ИИ.")

    def _set_field(self, key: str, value) -> None:
        self.document.set(key, value)

    def _check_clause(self, text: str) -> None:
        if not self.regulation or not str(text).strip():
            self.clause_hint.setText("")
            return
        found = self.regulation.clause_text(text)
        # v34: показываем вилку взысканий из устава по введённым пунктам.
        allowed = pdata.allowed_penalties_for(text, self.regulation)
        if allowed:
            hint = "Устав за эти пункты допускает: " + ", ".join(allowed) + "."
            if found:
                hint += "  Найдено в уставе: " + found[:150].replace("\n", " ")
            self.clause_hint.setStyleSheet("color: #34d399; font-size: 11px;")
            self.clause_hint.setText(hint)
            return
        if found:
            self.clause_hint.setStyleSheet("color: #34d399; font-size: 11px;")
            self.clause_hint.setText("Найдено в уставе: " + found[:180].replace("\n", " "))
        else:
            self.clause_hint.setStyleSheet("color: #f59e0b; font-size: 11px;")
            self.clause_hint.setText(
                "В загруженном уставе такой пункт не найден — проверьте формулировку.")

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

    def _collect(self) -> pdata.Postanovlenie:
        doc = pdata.Postanovlenie.from_dict(self.document.to_dict())
        for key, widget in self.fields.items():
            if widget is not None and hasattr(widget, "text"):
                doc.set(key, widget.text().strip())
        doc.doc_type = self.cb_type.currentText().strip() or doc.doc_type
        doc.penalty = self.cb_penalty.currentText().strip()
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
                            if name not in ("Нарушенный акт", "Пункт нарушения", "Взыскание")]
        if not doc.get("situation"):
            required_missing.append("Обстоятельства")
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
