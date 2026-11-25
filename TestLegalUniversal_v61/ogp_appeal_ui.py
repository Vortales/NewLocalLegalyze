"""ogp_appeal_ui.py — окно «Документы → Обращение в ОГП» (v60).

Задача пользователя: описать ситуацию и доказательства, приложить ссылки —
и получить готовое обращение в Офис Генерального прокурора в виде BB-кода.

Окно держит тот же принцип, что и постановления (v56–v59): **всё, что написал
оператор, — это вход для ИИ**. Текст обращения пишет только ИИ; окно
показывает его ответ, проверяет обязательные пункты формы (ч. 4 ст. 6
Регламента подачи обращений) и отдаёт текст в буфер обмена.

Постоянные реквизиты (телефон, почта, Discord, адрес) подставляются из
«Шаблон» — замечание пользователя: «а если в Шаблон не заполнено, то уже
требовать вручную».
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QTextOption
from PyQt6.QtWidgets import (
    QAbstractScrollArea, QApplication, QComboBox, QDialog, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget,
)

import diagnostics as diag
import ogp_appeal as appeal_mod
from postanovlenie_prokuror_ui import _dark_style


class OgpAppealDialog(QDialog):
    """Анкета обращения в ОГП → заявка в чат → готовый BB-код."""

    #: Заявка готова к отправке в чат: (текст заявки, короткое имя).
    request_ready = pyqtSignal(str, str)
    #: Готовое обращение сохранили в файл: (путь).
    saved = pyqtSignal(str)

    def __init__(self, cfg, template=None, documents_root=None, parent=None):
        super().__init__(parent)
        self.cfg = cfg or {}
        self.template = dict(template or {})
        self.documents_root = Path(documents_root) if documents_root else None
        self.fields = {}
        self.texts = {}
        self.subject_value = ""
        self.request_text = ""
        self.generated = ""
        self._drag_pos = None

        self.setWindowTitle("Документы — Обращение в ОГП")
        self.setMinimumSize(1020, 700)
        self.resize(1180, 860)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Dialog
        )
        self.setModal(True)
        self.setStyleSheet(_dark_style())
        self._build()
        self.prefill_from_template()

    # ------------------------------------------------------------------ утиль

    def _label(self, text: str, kind: str = "") -> QLabel:
        label = QLabel(text)
        if kind:
            label.setObjectName(kind)
        return label

    def _input(self, placeholder: str = "") -> QLineEdit:
        field = QLineEdit()
        field.setPlaceholderText(placeholder)
        field.setMinimumHeight(28)
        field.setSizePolicy(QSizePolicy.Policy.Expanding,
                            QSizePolicy.Policy.Fixed)
        return field

    def _text_area(self, placeholder: str = "", height: int = 90) -> QPlainTextEdit:
        area = QPlainTextEdit()
        area.setPlaceholderText(placeholder)
        area.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        area.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setSizeAdjustPolicy(
            QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored)
        area.setMinimumHeight(height)
        area.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        return area

    # ------------------------------------------------------------- построение

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(self._label("Обращение в Офис Генерального прокурора", "Title"))
        top.addStretch()
        close = QPushButton("Закрыть")
        close.clicked.connect(lambda _c=False: self.reject())
        top.addWidget(close)
        root.addLayout(top)

        hint = self._label(
            "Заполните данные — в чат уйдёт заявка, а ИИ вернёт готовое "
            "обращение по форме части 4 статьи 6 Регламента подачи обращений. "
            "Текст документа пишет ИИ: ваши поля — только вход для него.", "Caption")
        hint.setWordWrap(True)
        root.addWidget(hint)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        page = QWidget()
        page.setObjectName("Sheet")
        columns = QHBoxLayout(page)
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(14)

        columns.addLayout(self._build_left(), 1)
        columns.addLayout(self._build_right(), 1)
        scroll.setWidget(page)
        root.addWidget(scroll, 1)

        self.status = self._label("", "Caption")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.build_btn = QPushButton("Сформировать обращение")
        self.build_btn.setObjectName("Primary")
        self.build_btn.setToolTip(
            "Отправить данные в чат с ИИ и получить готовый текст обращения")
        self.build_btn.clicked.connect(lambda _c=False: self.request_appeal())
        bottom.addWidget(self.build_btn)
        self.copy_btn = QPushButton("Копировать BB-код")
        self.copy_btn.clicked.connect(lambda _c=False: self.copy_result())
        bottom.addWidget(self.copy_btn)
        self.save_btn = QPushButton("Сохранить в файл")
        self.save_btn.clicked.connect(lambda _c=False: self.save_result())
        bottom.addWidget(self.save_btn)
        bottom.addStretch()
        self.missing_hint = self._label("", "Caption")
        self.missing_hint.setWordWrap(True)
        bottom.addWidget(self.missing_hint)
        root.addLayout(bottom)

        self._refresh_buttons()

    def _build_left(self):
        left = QVBoxLayout()
        left.setSpacing(5)
        left.addWidget(self._label("1. Заявитель", "Section"))

        left.addWidget(self._label("Имя и фамилия", "Field"))
        self.fields["applicant"] = self._input("Max Ferstapaninov")
        self.fields["applicant"].textChanged.connect(
            lambda _t: self._refresh_subject())
        left.addWidget(self.fields["applicant"])

        row = QHBoxLayout()
        row.setSpacing(8)
        id_box = QVBoxLayout()
        id_box.addWidget(self._label("ID-карта", "Field"))
        self.fields["applicant_id"] = self._input("54011")
        id_box.addWidget(self.fields["applicant_id"])
        row.addLayout(id_box)
        phone_box = QVBoxLayout()
        phone_box.addWidget(self._label("Телефон (из «Шаблон»)", "Field"))
        self.fields["phone"] = self._input("16473399")
        phone_box.addWidget(self.fields["phone"])
        row.addLayout(phone_box)
        left.addLayout(row)

        row2 = QHBoxLayout()
        row2.setSpacing(8)
        mail_box = QVBoxLayout()
        mail_box.addWidget(self._label("Электронная почта (из «Шаблон»)", "Field"))
        self.fields["email"] = self._input("pofibuem@ds.com")
        mail_box.addWidget(self.fields["email"])
        row2.addLayout(mail_box)
        disc_box = QVBoxLayout()
        disc_box.addWidget(self._label("Discord (необязательно)", "Field"))
        self.fields["discord"] = self._input("ник в Discord")
        disc_box.addWidget(self.fields["discord"])
        row2.addLayout(disc_box)
        left.addLayout(row2)

        left.addWidget(self._label("Представитель (если есть)", "Field"))
        self.fields["representative"] = self._input(
            "Имя Фамилия, ID — иначе оставьте пустым")
        left.addWidget(self.fields["representative"])

        left.addWidget(self._label("2. Предмет обращения (ст. 5 Регламента)", "Section"))
        self.fields["category"] = QComboBox()
        self.fields["category"].addItem("— выберите формулировку —")
        for item in appeal_mod.CATEGORIES:
            self.fields["category"].addItem(item)
        left.addWidget(self.fields["category"])

        left.addWidget(self._label("3. Сведения о нарушителе", "Section"))
        self.fields["offender"] = self._input("Имя Фамилия (можно не заполнять)")
        left.addWidget(self.fields["offender"])
        row3 = QHBoxLayout()
        row3.setSpacing(8)
        ord_box = QVBoxLayout()
        ord_box.addWidget(self._label("Идентификационный знак", "Field"))
        self.fields["offender_id"] = self._input("97849")
        ord_box.addWidget(self.fields["offender_id"])
        row3.addLayout(ord_box)
        org_box = QVBoxLayout()
        org_box.addWidget(self._label("Организация", "Field"))
        self.fields["offender_org"] = self._input("LSPD / LSCSD / FIB …")
        org_box.addWidget(self.fields["offender_org"])
        row3.addLayout(org_box)
        left.addLayout(row3)
        left.addStretch()
        return left

    def _build_right(self):
        right = QVBoxLayout()
        right.setSpacing(5)

        right.addWidget(self._label("4. Событие", "Section"))
        row = QHBoxLayout()
        row.setSpacing(8)
        date_box = QVBoxLayout()
        date_box.addWidget(self._label("Дата и время", "Field"))
        self.fields["date_time"] = self._input("15.11.2026 15:56")
        date_box.addWidget(self.fields["date_time"])
        row.addLayout(date_box)
        place_box = QVBoxLayout()
        place_box.addWidget(self._label("Место", "Field"))
        self.fields["place"] = self._input("больница г. Лос-Сантос")
        place_box.addWidget(self.fields["place"])
        row.addLayout(place_box)
        right.addLayout(row)

        right.addWidget(self._label("5. Что произошло", "Section"))
        self.texts["situation"] = self._text_area(
            "Опишите своими словами: что случилось, кто участвовал, что "
            "нарушено. ИИ перепишет это официальным языком.", 110)
        right.addWidget(self.texts["situation"], 1)

        right.addWidget(self._label("6. Доказательства и ссылки", "Section"))
        self.texts["evidence"] = self._text_area(
            "Ссылки на видео/скриншоты и тайм-коды эпизодов: 03:12 — изъятие "
            "патронов; 05:01 — бездействие напарника.", 90)
        right.addWidget(self.texts["evidence"], 1)

        right.addWidget(self._label("7. Ссылки к форме", "Section"))
        self.fields["id_copy"] = self._input("ссылка на копию ID-карты")
        right.addWidget(self.fields["id_copy"])
        self.fields["contract"] = self._input(
            "ссылка на договор (только при представителе)")
        right.addWidget(self.fields["contract"])
        self.fields["authority"] = self._input(
            "ссылка на полномочия представителя (только при представителе)")
        right.addWidget(self.fields["authority"])

        right.addWidget(self._label("8. Что просите (необязательно)", "Field"))
        self.texts["demands"] = self._text_area(
            "Например: истребовать видеофиксацию, дать оценку действиям "
            "сотрудника, уведомить о решении.", 60)
        right.addWidget(self.texts["demands"], 1)

        right.addWidget(self._label("Готовое обращение от ИИ (BB-код)", "Section"))
        self.result = self._text_area(
            "Здесь появится текст обращения после ответа ИИ.", 150)
        right.addWidget(self.result, 2)
        return right

    # ---------------------------------------------------------------- данные

    def values(self) -> dict:
        """Значения анкеты: строки полей + многострочные блоки."""
        data = {}
        for key, widget in self.fields.items():
            if isinstance(widget, QComboBox):
                index = widget.currentIndex()
                data[key] = widget.currentText() if index > 0 else ""
            else:
                data[key] = widget.text().strip()
        for key, widget in self.texts.items():
            data[key] = widget.toPlainText().strip()
        return data

    def prefill_from_template(self) -> None:
        """Подставляет постоянные реквизиты из «Шаблон» (v60).

        Замечание пользователя: «в Шаблон добавить константные данные на подобии
        номера телефона, чтобы в эти Поля подставлять, а если в Шаблон не
        заполнено — то уже требовать вручную».
        """
        values = appeal_mod.prefill(self.template)
        for key, value in values.items():
            widget = self.fields.get(key)
            if isinstance(widget, QLineEdit) and not widget.text().strip():
                widget.setText(value)
        if values:
            self.status.setText(
                "Из «Шаблон» подставлено: "
                + ", ".join(appeal_mod.FIELDS[key][0].split(" (")[0]
                            for key in values)
                + ".")
        self._refresh_subject()

    def _refresh_subject(self) -> None:
        """Тема форума по ч. 2/ч. 3 ст. 6: зависит от того, кто нарушитель."""
        name = self.fields["applicant"].text().strip()
        org = self.fields["offender_org"].text().strip().lower()
        against_officer = "огп" in org or "прокурат" in org
        self.subject_value = appeal_mod.subject_for(name, against_officer)
        self.missing_hint.setText(self.subject_value)

    def _refresh_buttons(self) -> None:
        has_result = bool(self.generated.strip())
        self.copy_btn.setEnabled(has_result)
        self.save_btn.setEnabled(has_result)

    # --------------------------------------------------------------- действия

    def request_appeal(self) -> bool:
        """Собрать заявку и отдать её в чат. False — данных не хватает."""
        values = self.values()
        missing = appeal_mod.missing_fields(values)
        if missing:
            names = "; ".join(label for _key, label in missing)
            self.status.setText(f"Заполните обязательные поля: {names}.")
            QMessageBox.warning(
                self, "Обращение в ОГП",
                "Не хватает обязательных данных:\n\n" + "\n".join(
                    f"• {label}" for _key, label in missing)
                + "\n\nИх можно заполнить вручную или внести в «Шаблон».")
            return False
        self._refresh_subject()
        self.request_text = appeal_mod.build_request(
            values, subject=self.subject_value)
        self.status.setText(
            "Заявка отправлена в чат: ждём ответ ИИ с текстом обращения.")
        self.request_ready.emit(self.request_text, "Обращение в ОГП")
        diag.event("appeal.request", applicant=values.get("applicant", ""),
                   filled=len([v for v in values.values() if v]))
        return True

    def note_answer_received(self, text: str = "") -> None:
        """Пришёл ответ ИИ: вынуть обращение и показать его."""
        appeal = appeal_mod.extract_appeal(text)
        if not appeal:
            self.status.setText(
                "В ответе ИИ нет блока обращения (===ОБРАЩЕНИЕ BB===). "
                "Отправьте заявку ещё раз.")
            return
        missing = appeal_mod.check_form(appeal)
        self.generated = appeal
        self.result.setPlainText(appeal)
        notes = appeal_mod.extract_notes(text)
        tail = ""
        if missing:
            tail = (" В ответе нет пунктов: "
                    + ", ".join(mark for _key, mark in missing) + ".")
        elif notes and notes.lower() not in ("нет", "нет."):
            tail = f" ИИ отметил: {notes}"
        self.status.setText("Обращение получено и проверено по форме." + tail)
        self._refresh_buttons()

    def copy_result(self) -> None:
        if not self.generated.strip():
            return
        QApplication.clipboard().setText(self.generated)
        self.status.setText("BB-код скопирован в буфер обмена.")

    def save_result(self) -> str:
        """Складывает текст рядом с документами: вернёт путь (пусто — некуда)."""
        if not self.generated.strip():
            return ""
        root = self.documents_root
        if root is None:
            self.status.setText("Не задана папка документов — сохранение невозможно.")
            return ""
        try:
            folder = Path(root) / "Обращения в ОГП"
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / appeal_mod.docx_name(self.values())
            path.write_text(self.generated, encoding="utf-8")
        except Exception:
            diag.exception("appeal.save")
            self.status.setText("Не удалось сохранить файл.")
            return ""
        self.saved.emit(str(path))
        self.status.setText(f"Сохранено: {path}")
        return str(path)
