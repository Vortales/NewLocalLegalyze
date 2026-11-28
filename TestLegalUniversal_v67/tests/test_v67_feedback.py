"""v67: «Обратная связь 66v.docx» — семь пунктов и четыре находки по выгрузке.

Выгрузка пользователя `Постановление_66v.7z` (сессия журналов
`20261128-190131-18824-02d216`, 16:01–16:37) показала семь замечаний. По
каждому задан вопрос пользователю, решение зафиксировано в теле PR #1:

1. `no_save_until_used` — «номер постановления перескакивает с 1 на 2, а при
   выборе существующего постановления создаётся новое». Причина: открытие формы
   и «Новое обращение» сразу писали пустое дело (`cases.save`), и фантомы 1, 3,
   4 заняли общие номера 0001/0003/0004. Дело пишется только когда появился
   текст обращения или ушла первая заявка; уже лежащие фантомы убирает
   `purge_empty()`.
2. `defer_and_diff` — «выпадающие списки открываются и сразу закрываются».
   Причина: опрос состояния (`POLL_MS=250`, `_doc_watch_timer=2500`) делал
   `clear()+addItem()` под открытым popup. Обновление откладывается, пока
   список открыт или прокурор печатает, а список перестраивается только если
   СОСТАВ пунктов изменился.
3. `one_call` — «статус „заявка в очереди…" висит», «постановление собирается
   дважды». Причина: заявка проверки обращения не имела узла (`node_kind=""`,
   `stage_kind="intake"`), запись считалась занятой и очередь не поднималась
   (16:03:18 → 16:11:35, восемь минут тишины); по узлу 2.N/3 шли ДВА запроса —
   «ответ узла» и авто-заявка документа (12 `gov.rendered` на 6 узлов, 15
   снимков истории). Теперь ОДИН запрос на этап: «Этап N» отправляет заявку на
   ДОКУМЕНТ, её ответ сохраняется в узел и из него печатается PNG; авто-заявка
   осталась только у этапа 1. Страница ИИ обновляется ДО и ПОСЛЕ каждого сбора.
4. `fit_page` — «нужна кнопка „Постановления"»: отдельное окно со списком .png
   по всем этапам/подэтапам, страница целиком (не крупнее 100 %), прокрутка,
   колесо = масштаб, кнопки «Открыть папку» и «Открыть файл».
5. «№2 заявитель не распознан» — список «Обращения» брал заявителя из ТЕКСТА
   обращения и игнорировал `intake_fields` из ответа ИИ (в деле 2 там
   `applicant_name="Lorenzo Shroud"`, `subject_name="Heavy Asaev"`).
6. «в „История" дубликация записей „- 0002-1 Heavy Asaev"» — на каждый номер
   лежало два снимка. Теперь в списке одна запись на номер, а повторный сбор
   показывается как «0002-1 · версия 2».
7. «работай строго по задаче, новые ошибки — только по согласованию». Четыре
   найденные при разборе выгрузки ошибки показаны пользователю с примерами и
   одобрены (находки 1–4 ниже).

Находки (одобрены пользователем):

* находка 1 — роль заявки забывалась до очистки записи, поэтому
  `auto_document` оставался в состоянии `requested` у узлов «1» и «3», хотя PNG
  были напечатаны (`cases/2/case.json`);
* находка 2 (`stage_only`) — сырой «ИИ-ответ.txt» писался ДО разбора ответа в
  папку обращения и затирался следующим ответом
  (`Обращение №2/0002/ИИ-ответ.txt`, 13 015 Б эха авто-заявки);
* находка 3 — `snapshot_current()` снимал «Версия-001» с пустой папки (3 файла
  заявки, 0 PNG), поэтому настоящий комплект оказывался в «Версия-002»;
* находка 4 — папка обращения создавалась вместе с фантомным делом: пустые
  «Обращение №3» и «Обращение №4» в выгрузке.

Также внедрена «Рекомендация.txt» (типографика) — её тесты в
`tests/test_v57_remarks.py` (решение `body_only`: красная строка связного
текста 1,15 см, нумерованные пункты «ПОСТАНОВИЛ» — прежние 1,25 см).
"""

from __future__ import annotations

import ast
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

import doc_layout as dl
import gov_case as cases
import pending_send as psend
import postanovlenie_prokuror_data as gdata

ROOT = Path(__file__).resolve().parent.parent
UI = "postanovlenie_prokuror_ui.py"
MAIN = "main.py"


def _src(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _block(src: str, start: str, end: str) -> str:
    i = src.index(start)
    j = src.index(end, i)
    return src[i:j]


def _method(src: str, name: str) -> str:
    """Тело метода класса — до следующего `def` того же отступа."""
    i = src.index(f"    def {name}(")
    j = src.index("\n    def ", i + 1)
    return src[i:j]


class _FakeDiag:
    """Журнал событий: тесты видят, что код сообщил диагностике."""

    def __init__(self):
        self.events: list = []

    def event(self, name, **fields):
        self.events.append((name, fields))

    def exception(self, name, **fields):
        self.events.append((name, dict(fields, error="exception")))

    def names(self):
        return [name for name, _fields in self.events]


def _module_level(names, module=UI):
    """Вынуть функции/константы верхнего уровня и выполнить их без PyQt6.

    `postanovlenie_prokuror_ui.py` и `main.py` тянут PyQt6, которого в
    тестовом окружении нет, поэтому чистая логика исполняется из исходника.
    """
    tree = ast.parse(_src(module))
    nodes = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in names:
                    nodes.append(node)
        elif isinstance(node, ast.AnnAssign) and \
                isinstance(node.target, ast.Name) and node.target.id in names:
            nodes.append(node)
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names:
            nodes.append(node)
    missing = set(names) - {_node_name(node) for node in nodes}
    if missing:
        raise AssertionError(f"в {module} нет {sorted(missing)}")
    diag = _FakeDiag()
    namespace = {"re": __import__("re"), "time": time, "json": json,
                 "Path": Path, "diag": diag, "os": os}
    code = compile(ast.Module(body=nodes, type_ignores=[]), "<v67>", "exec")
    exec(code, namespace)                                  # noqa: S102
    namespace["_diag"] = diag
    return namespace


def _node_name(node) -> str:
    if isinstance(node, ast.Assign):
        return node.targets[0].id
    if isinstance(node, ast.AnnAssign):
        return node.target.id
    return node.name


def _method_fn(name, module=UI, class_name="PostanovlenieProkuroraDialog",
               extra=()):
    """Скомпилировать один метод класса как функцию (self — первый аргумент)."""
    tree = ast.parse(_src(module))
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == class_name)
    node = next(item for item in cls.body
                if isinstance(item, ast.FunctionDef) and item.name == name)
    code = compile(ast.Module(body=[node], type_ignores=[]), "<v67m>", "exec")
    diag = _FakeDiag()
    namespace = {"re": __import__("re"), "time": time, "json": json,
                 "Path": Path, "diag": diag, "os": os}
    for key, value in extra:
        namespace[key] = value
    exec(code, namespace)                                  # noqa: S102
    namespace["_diag"] = diag
    return namespace[name], diag


def _touch(path: Path, when: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    os.utime(path, (when, when))
    return path


def _png(folder: Path, name: str, when: float | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(b"\x89PNG\r\n\x1a\n")
    if when:
        os.utime(path, (when, when))
    return path


# --------------------------------------------------------------------------- #
#  п.4: окно «Постановления» — что лежит на диске
# --------------------------------------------------------------------------- #

class DocsOnDiskTests(unittest.TestCase):
    """`doc_layout.documents_on_disk` — список по файлам, а не по данным дела."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "postanovleniya"
        base = self.root / "Обращение №2" / "0002"
        now = time.time()
        stage1 = base / "Этап 1 — принятие"
        _png(stage1, "0002-1 — Этап 1 — стр. 2.png", now - 40)
        _png(stage1, "0002-1 — Этап 1 — стр. 1.png", now - 40)
        _png(stage1, "0002-1 — Этап 1 — стр. 10.png", now - 40)
        (stage1 / "ИИ-ответ.txt").write_text("ответ", encoding="utf-8")
        (stage1 / "Заявка (Этап 1).txt").write_text("заявка", encoding="utf-8")
        # прежняя версия того же этапа — в списке НЕ показывается
        _png(stage1 / "Архив" / "Версия-001", "0002-1 — Этап 1 — стр. 1.png",
             now - 90)
        chain = base / "Цепочка A"
        _png(chain / "Этап 2.1 — истребование", "0002-2 — Этап 2.1 — стр. 1.png",
             now - 30)
        # подэтап без PNG (заявка ушла, ответа нет) — в списке не появляется
        (chain / "Этап 2.2 — видеофиксация").mkdir(parents=True, exist_ok=True)
        (chain / "Этап 2.2 — видеофиксация" / "Заявка (Этап 2.2).txt") \
            .write_text("заявка", encoding="utf-8")
        _png(base / "Этап 3 — итог", "0002-6 — Этап 3 — стр. 1.png", now - 10)
        # дело прежней нумерации: папка AG-1, длинные имена файлов
        legacy = self.root / "Обращение №1" / "AG-1" / "этап-1 — принятие"
        _png(legacy, "AG-1_Постановление_Этап_1_принятие — стр.1.png", now - 99)
        _png(legacy, "AG-1_Постановление_Этап_1_принятие — стр.2.png", now - 99)
        # пустое обращение (фантом v66) — ни одного постановления
        (self.root / "Обращение №3" / "0003").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_stages_are_listed_in_order(self):
        items = dl.documents_on_disk(self.root, "2", "0002")
        self.assertEqual([item["caption"] for item in items],
                         ["0002-1 · Этап 1 — принятие",
                          "0002-2 · Этап 2.1 — истребование",
                          "0002-6 · Этап 3 — итог"])
        self.assertEqual([item["node"] for item in items], ["1", "2.1", "3"])
        self.assertEqual([item["number"] for item in items],
                         ["0002-1", "0002-2", "0002-6"])

    def test_pages_are_sorted_by_page_number(self):
        items = dl.documents_on_disk(self.root, "2", "0002")
        pages = [page.name for page in items[0]["pages"]]
        self.assertEqual(pages, ["0002-1 — Этап 1 — стр. 1.png",
                                 "0002-1 — Этап 1 — стр. 2.png",
                                 "0002-1 — Этап 1 — стр. 10.png"])

    def test_archive_and_empty_stages_are_skipped(self):
        items = dl.documents_on_disk(self.root, "2", "0002")
        folders = [str(item["folder"]) for item in items]
        self.assertFalse(any("Архив" in folder for folder in folders))
        self.assertFalse(any("2.2" in folder for folder in folders))

    def test_legacy_case_is_listed_too(self):
        items = dl.documents_on_disk(self.root, "1")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["caption"], "AG-1 · этап-1 — принятие")
        self.assertEqual(items[0]["number"], "")     # общего номера у v59 нет
        self.assertEqual(len(items[0]["pages"]), 2)

    def test_unknown_and_empty_appeals_give_nothing(self):
        self.assertEqual(dl.documents_on_disk(self.root, "3", "0003"), [])
        self.assertEqual(dl.documents_on_disk(self.root, "99", "0099"), [])
        self.assertEqual(dl.documents_on_disk(self.root, ""), [])

    def test_node_of_folder(self):
        self.assertEqual(dl.node_of_folder("Этап 2.3 — допрос"), "2.3")
        self.assertEqual(dl.node_of_folder("Этап 1 — принятие"), "1")
        self.assertEqual(dl.node_of_folder("этап-2 — санкция"), "")


class FitPageScaleTests(unittest.TestCase):
    """Масштаб «страница целиком, не крупнее 100 %» (решение `fit_page`)."""

    def setUp(self):
        space = _module_level(["DOCS_MAX_FIT_ZOOM", "DOCS_MIN_ZOOM",
                               "DOCS_MAX_ZOOM", "DOCS_ZOOM_STEP",
                               "DOCS_VIEW_PADDING", "fit_page_scale"])
        self.space = space
        self.fit = space["fit_page_scale"]

    def test_limits_are_declared(self):
        self.assertEqual(self.space["DOCS_MAX_FIT_ZOOM"], 1.0)
        self.assertLess(self.space["DOCS_MIN_ZOOM"], 1.0)
        self.assertGreater(self.space["DOCS_MAX_ZOOM"], 1.0)
        self.assertGreater(self.space["DOCS_ZOOM_STEP"], 1.0)

    def test_a4_at_200dpi_fits_the_window(self):
        # Страница А4 при DPI=200 — 1654 × 2339 px (doc_render.PAGE_W).
        scale = self.fit(1654, 2339, 1000, 700)
        self.assertAlmostEqual(scale, 700 / 2339, places=6)
        self.assertLessEqual(scale, 1.0)
        self.assertLessEqual(2339 * scale, 700 + 1e-6)
        self.assertLessEqual(1654 * scale, 1000 + 1e-6)

    def test_small_image_is_not_enlarged(self):
        self.assertEqual(self.fit(400, 300, 1000, 700), 1.0)

    def test_manual_zoom_may_exceed_fit(self):
        scale = self.fit(400, 300, 1000, 700,
                         max_zoom=self.space["DOCS_MAX_ZOOM"])
        self.assertGreater(scale, 1.0)

    def test_degenerate_sizes_do_not_crash(self):
        for args in ((0, 0, 100, 100), (100, 100, 0, 0), (None, None, 10, 10)):
            self.assertGreater(self.fit(*args), 0.0)


class DocsWindowTests(unittest.TestCase):
    """Окно «Постановления»: кнопка, список, страница, «Открыть папку»."""

    def setUp(self):
        self.ui = _src(UI)

    def test_button_is_in_the_same_row_right_of_facts(self):
        # ряд кнопок формы постановления (не стартового окна обращений)
        start = self.ui.index('self.cases_btn = QPushButton("Обращения")')
        begin = self.ui.rindex("buttons = QHBoxLayout()", 0, start)
        row = self.ui[begin:self.ui.index("root.addLayout(buttons)", begin)]
        order = [name for name in ("close_btn", "cases_btn", "facts_btn",
                                   "docs_btn", "addStretch", "resend_btn",
                                   "form_btn") if name in row]
        self.assertEqual(order, ["close_btn", "cases_btn", "facts_btn",
                                 "docs_btn", "addStretch", "resend_btn",
                                 "form_btn"])
        self.assertIn('QPushButton("Постановления")', row)
        self.assertIn("self._toggle_docs_window()", row)

    def test_window_is_built_from_files_on_disk(self):
        refresh = _method(self.ui, "_refresh_docs_window")
        self.assertIn("dl.documents_on_disk", refresh)
        self.assertIn("self.case.doc_base", refresh)
        self.assertIn("QListWidgetItem", refresh)
        # выбор прокурора сохраняется при перечитывании списка
        self.assertIn("keep = self._current_doc_caption()", refresh)

    def test_list_is_not_rebuilt_without_changes(self):
        """п.2 и п.4 вместе: частый опрос не сбрасывает список под руками."""
        refresh = _method(self.ui, "_refresh_docs_window")
        self.assertIn("_docs_signature(items", refresh)
        self.assertIn("if not force and signature ==", refresh)
        signature = _module_level(["_docs_signature"])["_docs_signature"]
        with tempfile.TemporaryDirectory() as tmp:
            page = _png(Path(tmp) / "Этап 1 — принятие",
                        "0001-1 — Этап 1 — стр. 1.png", 1_700_000_000.0)
            items = [{"caption": "0001-1 · Этап 1 — принятие",
                      "folder": page.parent, "pages": [page],
                      "node": "1", "number": "0001-1"}]
            first = signature(items, "1")
            self.assertEqual(first, signature(items, "1"))
            self.assertNotEqual(first, signature(items, "2"))     # другое дело
            self.assertNotEqual(first, signature([], "1"))        # файлов нет
            page.write_bytes(b"y")
            os.utime(page, (1_700_000_900.0, 1_700_000_900.0))
            self.assertNotEqual(first, signature(items, "1"))     # перепечатали

    def test_page_is_shown_whole_and_not_larger_than_100(self):
        show = _method(self.ui, "_show_doc_page")
        self.assertIn("_docs_effective_zoom", show)
        self.assertIn("Qt.AspectRatioMode.KeepAspectRatio", show)
        self.assertIn("label.resize(scaled.size())", show)
        self.assertIn("стр. ", show)
        effective = _method(self.ui, "_docs_effective_zoom")
        self.assertIn("self._docs_fit_zoom(pixmap)", effective)
        self.assertIn("DOCS_MAX_ZOOM", effective)
        fit = _method(self.ui, "_docs_fit_zoom")
        self.assertIn("fit_page_scale(", fit)
        self.assertIn("DOCS_VIEW_PADDING", fit)

    def test_wheel_changes_zoom_instead_of_scrolling(self):
        self.assertIn("_docs_zoom_by", _method(self.ui, "eventFilter"))
        wheel = _block(self.ui, "def eventFilter", "def _ui_busy")
        self.assertIn("QEvent.Type.Wheel", wheel)
        self.assertIn('"_docs_wheel_target"', wheel)
        self.assertIn("return True", wheel)
        zoom = _method(self.ui, "_docs_zoom_by")
        self.assertIn("DOCS_ZOOM_STEP", zoom)
        step = _method(self.ui, "_docs_zoom_step")
        self.assertIn("DOCS_MIN_ZOOM", step)
        self.assertIn("self._docs_fit_zoom()", step)
        reset = _method(self.ui, "_docs_zoom_reset")
        self.assertIn("self._docs_zoom = 0.0", reset)

    def test_open_folder_and_open_file_buttons(self):
        build = _method(self.ui, "_build_docs_window")
        for caption in ("Открыть папку", "Открыть файл", "По размеру",
                        "‹ страница", "страница ›", "Обновить", "Скрыть"):
            self.assertIn(f'"{caption}"', build, caption)
        self.assertIn('installEventFilter(self)', build)
        folder = _method(self.ui, "_open_doc_folder")
        self.assertIn("_reveal_path", folder)
        file_open = _method(self.ui, "_open_doc_file")
        self.assertIn("_current_doc_page()", file_open)
        reveal = _method(self.ui, "_reveal_path")
        self.assertIn("os.startfile", reveal)
        self.assertIn("xdg-open", reveal)

    def test_window_is_not_modal_and_is_placed_beside_the_form(self):
        build = _method(self.ui, "_build_docs_window")
        self.assertIn("QDialog(self)", build)
        self.assertIn("Qt.WindowType.WindowStaysOnTopHint", build)
        self.assertNotIn("setModal(True)", build)
        place = _method(self.ui, "_place_docs_window")
        self.assertIn("geometry.right() + 12", place)
        toggle = _method(self.ui, "_toggle_docs_window")
        self.assertIn("window.hide()", toggle)
        self.assertIn("self._refresh_docs_window(force=True)", toggle)

    def test_answer_refreshes_the_list_when_the_window_is_open(self):
        refresh_doc = _method(self.ui, "refresh_document")
        self.assertIn("self._refresh_docs_window(defer_if_busy=True)", refresh_doc)
        view = _method(self.ui, "_refresh_case_view")
        self.assertIn("self._refresh_docs_window(defer_if_busy=True)", view)
        deferred = _method(self.ui, "_apply_deferred_sync")
        self.assertIn("self._refresh_docs_window()", deferred)


# --------------------------------------------------------------------------- #
#  п.3: один запрос на этап (`one_call`)
# --------------------------------------------------------------------------- #

class OneCallTests(unittest.TestCase):
    """Один запрос — один ответ — один PNG: узлы 2.N и 3 больше не дублируются."""

    def setUp(self):
        self.ui = _src(UI)
        self.main = _src(MAIN)

    def test_node_request_method_is_gone(self):
        """`_send_node` (запрос «ответ узла») удалён: второго запроса нет."""
        self.assertNotIn("def _send_node(", self.ui)
        self.assertNotIn("self._send_node(", self.ui)
        self.assertNotIn("cases.node_request(", self.ui)

    def test_stage_button_sends_the_document_request(self):
        action = _method(self.ui, "_run_main_action")
        self.assertIn("self._send_node_document(kind", action)
        self.assertIn('request_name=f"Заявка (Этап {kind}).txt"', action)
        sender = _method(self.ui, "_send_node_document")
        self.assertIn("cases.node_document_request(", sender)
        self.assertIn("pending_send.build_record(", sender)
        self.assertIn("self.restart_requested.emit", sender)
        self.assertIn("node_kind=kind", sender)
        self.assertIn("kind=\"document\"", sender)
        manual = _method(self.ui, "_save_node_document")
        self.assertIn('self._send_node_document(kind, request_name='
                      '"Заявка (Сформировать).txt"', manual)

    def test_intake_record_carries_its_node(self):
        """находка 1/п.3: заявка проверки обращения относится к узлу «1»."""
        intake = _method(self.ui, "_intake_check")
        self.assertIn('node_kind="1"', intake)
        self.assertEqual(psend.record_node({"kind": "intake",
                                            "stage_kind": "intake"}), "1")
        self.assertEqual(psend.record_node({"kind": "document",
                                            "node_kind": "2.3",
                                            "stage_kind": "measure"}), "2.3")
        self.assertEqual(psend.record_node({"kind": "document",
                                            "stage_kind": "measure"}), "measure")
        # `kind="intake"` — всегда проверка обращения, то есть узел «1».
        self.assertEqual(psend.record_node({"kind": "intake",
                                            "stage_kind": "measure"}), "1")
        self.assertEqual(psend.record_node({}), "")

    def test_busy_slot_is_released_by_the_intake_answer(self):
        """Восемь минут тишины: запись intake держала слот занятым.

        Журнал `20261128-190131-18824-02d216`: 16:03:18 `queued kind=intake`
        → 16:11:35 первый `gov.rendered`. Ответ на проверку обращения пришёл,
        но `main._pending_answer_received` не узнал свою заявку (у записи
        `node_kind` был пуст) и не очистил слот, поэтому очередь не
        поднималась, а статус «заявка в очереди…» висел.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = psend.build_record(
                kind="intake", request_text="заявка", message_text="сообщение",
                case_number="2", stage_kind="intake", node_kind="1",
                title="Этап 1 · Обращение №2")
            psend.save(root, record)
            self.assertTrue(psend.is_busy(psend.load(root)))
            self.assertEqual(psend.record_node(record), "1")
            # v66: запись intake без `node_kind` тоже относится к узлу «1».
            legacy = dict(record)
            legacy.pop("node_kind")
            self.assertEqual(psend.record_node(legacy), "1")
            psend.clear(root)
            self.assertFalse(psend.is_busy(psend.load(root)))
            self.assertEqual(psend.queue_len(root), 0)

    def test_auto_document_is_only_for_stage_one(self):
        case = cases.GovCase(number="2", status=cases.STATUS_IN_PROGRESS,
                             doc_base="0002")
        case.set_work_node("1", answer="===ПРОВЕРКА ОБРАЩЕНИЯ===", done=True)
        self.assertTrue(case.auto_document_due("1"))
        case.set_work_node("2.1", answer="===ПОСТАНОВЛЕНИЕ===", done=True)
        self.assertFalse(case.auto_document_due("2.1"))
        case.set_work_node("3", answer="===ПОСТАНОВЛЕНИЕ===", done=True)
        self.assertFalse(case.auto_document_due("3"))

    def test_answer_marks_the_node_done_whatever_the_role(self):
        """находка 1: `auto_document` = `done` при любой роли заявки."""
        block = _block(self.main, "def _apply_gov_answer", "def _open_prompts_folder")
        self.assertIn('case.set_auto_document(node_key, "done")', block)
        self.assertIn('diag.event("gov.auto_document.done"', block)
        self.assertIn("saved_node = psend.record_node(record)", self.main)
        received = _method(self.main, "_pending_answer_received")
        self.assertIn("_pending_answer_role", received)
        self.assertIn("_pending_answer_node", received)

    def test_fresh_chat_is_planned_before_and_after_a_collect(self):
        """п.3: «Как и до каждого сбора „Постановления" так и после обновлять»."""
        sender = _method(self.ui, "_send_node_document")
        self.assertIn("self.restart_requested.emit", sender)      # ДО
        block = _block(self.main, "def _apply_gov_answer", "def _open_prompts_folder")
        self.assertIn("self._schedule_fresh_chat_after_answer()", block)   # ПОСЛЕ
        # ПОСЛЕ — только если следующей заявки нет: иначе чат очистится перед
        # её отправкой, а лишняя перезагрузка откладывала выгрузку вложений.
        self.assertIn("psend.is_busy(psend.load(root))", block)
        self.assertIn("psend.queue_len(root)", block)
        self.assertIn('diag.event("gov.fresh_chat_skipped"', block)

    def test_auto_document_request_still_runs_for_stage_one(self):
        block = _block(self.main, "def _apply_gov_answer", "def _open_prompts_folder")
        self.assertIn("self._auto_document_request(case, node_key)", block)
        self.assertIn("self._drain_pending_queue()", block)
        request = _method(self.main, "_auto_document_request")
        self.assertIn("auto_document_due", request)
        self.assertIn("ROLE_AUTO_DOCUMENT", request)


# --------------------------------------------------------------------------- #
#  п.1 и находка 4: фантомные дела и пустые папки обращений
# --------------------------------------------------------------------------- #

class NoPhantomCaseTests(unittest.TestCase):
    """`no_save_until_used`: дело пишется, только когда в нём есть содержание."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "postanovleniya"
        self.store = cases.GovCaseStore(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_empty_case_is_not_used(self):
        blank = cases.GovCase(number="1", status=cases.STATUS_NEW,
                              doc_base="0001")
        self.assertFalse(blank.is_used())
        self.assertTrue(cases.GovCase(number="2", appeal_raw="текст").is_used())
        self.assertTrue(cases.GovCase(number="3", appeal_number="999").is_used())
        self.assertTrue(cases.GovCase(number="4", intake_answer="Принято").is_used())
        self.assertTrue(cases.GovCase(number="5", outcome="accept").is_used())
        with_node = cases.GovCase(number="6")
        with_node.set_work_node("1", answer="ответ", done=True)
        self.assertTrue(with_node.is_used())
        closed = cases.GovCase(number="7", status=cases.STATUS_CLOSED,
                               doc_base="0007")
        self.assertTrue(closed.is_used())

    def test_save_skips_the_empty_case_and_its_folder(self):
        blank = cases.GovCase(number="1", status=cases.STATUS_NEW,
                              doc_base="0001")
        self.assertIsNone(self.store.save(blank))
        self.assertFalse(self.store.path("1").exists())
        self.assertFalse((self.root / "Обращение №1").exists())
        blank.appeal_raw = "Прошу провести проверку."
        path = self.store.save(blank)
        self.assertIsNotNone(path)
        self.assertTrue(path.is_file())
        self.assertTrue((self.root / "Обращение №1").is_dir())

    def test_force_still_writes(self):
        blank = cases.GovCase(number="9")
        self.assertIsNotNone(self.store.save(blank, force=True))
        self.assertTrue(self.store.path("9").is_file())

    def test_first_real_case_gets_the_first_number(self):
        """п.1: «номер постановления перескакивает с 1 на 2»."""
        self.assertIsNone(self.store.save(cases.GovCase(number="1")))
        real = cases.GovCase(number=self.store.next_number(),
                             appeal_raw="Обращение гражданина")
        real.ensure_doc_base(self.store.used_doc_bases(exclude=real.number))
        self.store.save(real)
        self.assertEqual(real.number, "1")
        self.assertEqual(real.doc_base, "0001")

    def test_purge_removes_phantoms_and_keeps_real_folders(self):
        phantom = cases.GovCase(number="3", status=cases.STATUS_NEW,
                                doc_base="0003")
        self.store.save(phantom, force=True)
        (self.root / "Обращение №3").mkdir(parents=True, exist_ok=True)
        real = cases.GovCase(number="1", appeal_raw="текст обращения",
                             doc_base="0001")
        self.store.save(real)
        folder = self.root / "Обращение №1" / "0001"
        _png(folder / "Этап 1 — принятие", "0001-1 — Этап 1 — стр. 1.png")

        purged = self.store.purge_empty()

        self.assertIn("3", purged)
        self.assertNotIn("1", purged)
        self.assertFalse(self.store.path("3").exists())
        self.assertFalse((self.root / "Обращение №3").exists())
        self.assertTrue(self.store.path("1").is_file())
        self.assertTrue(folder.is_dir())
        self.assertEqual(self.store.next_number(), "2")

    def test_purge_keeps_a_folder_with_files(self):
        """находка 4: «Обращение №1» с заявкой и ответом не удаляется."""
        case = cases.GovCase(number="1", status=cases.STATUS_NEW)
        self.store.save(case, force=True)
        appeal = self.root / "Обращение №1"
        (appeal / "AG-1").mkdir(parents=True, exist_ok=True)
        (appeal / "AG-1" / "Обращение.txt").write_text("текст", encoding="utf-8")
        self.assertEqual(self.store.purge_empty(), ["1"])
        self.assertTrue(appeal.is_dir())
        self.assertFalse(self.store.path("1").exists())

    def test_form_does_not_save_a_blank_case(self):
        ui = _src(UI)
        # в docstring метода история правки упомянута — проверяем только код
        new_case = _method(ui, "new_case").split('"""')[-1]
        self.assertNotIn("self.cases.save(", new_case)
        self.assertNotIn("self.cases.merge_save(", new_case)
        self.assertIn("self._on_appeal_changed()", new_case)
        # при старте клиента уже лежащие фантомы убираются
        self.assertIn("purge_empty()", _src(MAIN))


# --------------------------------------------------------------------------- #
#  п.2: списки не закрываются под руками (`defer_and_diff`)
# --------------------------------------------------------------------------- #

class FakeCombo:
    """QComboBox без Qt: считает перестроения и хранит пункты."""

    def __init__(self, rows=()):
        self.rows = [(text, data) for text, data in rows]
        self.index = 0 if self.rows else -1
        self.clears = 0
        self.adds = 0
        self.text_updates = 0
        self.blocked = 0

    def count(self):
        return len(self.rows)

    def itemData(self, position):
        return self.rows[position][1] if 0 <= position < len(self.rows) else None

    def itemText(self, position):
        return self.rows[position][0] if 0 <= position < len(self.rows) else ""

    def setItemText(self, position, text):
        self.text_updates += 1
        self.rows[position] = (text, self.rows[position][1])

    def currentData(self):
        return self.itemData(self.index)

    def currentIndex(self):
        return self.index

    def setCurrentIndex(self, position):
        self.index = position

    def findData(self, data):
        for position, (_text, value) in enumerate(self.rows):
            if value == data:
                return position
        return -1

    def clear(self):
        self.clears += 1
        self.rows = []
        self.index = -1

    def addItem(self, text, data=None):
        self.adds += 1
        self.rows.append((text, data))
        if self.index < 0:
            self.index = 0

    def blockSignals(self, flag):
        self.blocked += 1 if flag else 0


class FakeForm:
    """Форма без Qt: занятость и отложенное обновление — как в классе."""

    def __init__(self, busy=False):
        self.busy = busy
        self.deferred: list = []

    def _ui_busy(self):
        return self.busy

    def _defer_sync(self, what):
        self.deferred.append(what)


class StableListTests(unittest.TestCase):
    """Список перестраивается только когда изменился СОСТАВ пунктов."""

    def setUp(self):
        self.refill, self.diag = _method_fn("_refill_combo")

    def test_same_composition_updates_texts_only(self):
        combo = FakeCombo([("Этап 2.1 — пройдено 1 из 4", "2.1"),
                           ("Этап 2.2 — видеофиксация", "2.2")])
        form = FakeForm()
        rebuilt = self.refill(form, combo,
                              [("Этап 2.1 — пройдено 2 из 4", "2.1"),
                               ("Этап 2.2 — видеофиксация", "2.2")], "facts")
        self.assertFalse(rebuilt)
        self.assertEqual(combo.clears, 0)
        self.assertEqual(combo.adds, 0)
        self.assertEqual(combo.text_updates, 1)
        self.assertEqual(combo.itemText(0), "Этап 2.1 — пройдено 2 из 4")
        self.assertNotIn("ui.combo_rebuilt", self.diag.names())

    def test_changed_composition_rebuilds_and_keeps_the_choice(self):
        combo = FakeCombo([("Этап 2.1", "2.1"), ("Этап 2.2", "2.2")])
        combo.setCurrentIndex(1)
        form = FakeForm()
        rebuilt = self.refill(form, combo,
                              [("Этап 2.1", "2.1"), ("Этап 2.3", "2.3")],
                              "facts")
        self.assertTrue(rebuilt)
        self.assertEqual(combo.clears, 1)
        self.assertEqual(combo.adds, 2)
        self.assertEqual(combo.currentData(), "2.1")   # прежний пункт пропал
        self.assertIn("ui.combo_rebuilt", self.diag.names())

    def test_selection_is_restored_by_data(self):
        combo = FakeCombo([("A", "A"), ("B", "B"), ("C", "C")])
        combo.setCurrentIndex(2)
        rebuilt = self.refill(FakeForm(), combo,
                              [("A · пройдено", "A"), ("B", "B"), ("C", "C")],
                              "chain")
        self.assertFalse(rebuilt)
        self.assertEqual(combo.currentData(), "C")

    def test_prefer_is_used_when_nothing_was_selected(self):
        combo = FakeCombo()
        self.refill(FakeForm(), combo, [("A", "A"), ("B", "B")], "chain",
                    prefer="B")
        self.assertEqual(combo.currentData(), "B")

    def test_open_popup_defers_the_refresh(self):
        combo = FakeCombo([("Этап 2.1", "2.1")])
        form = FakeForm(busy=True)
        self.assertFalse(self.refill(form, combo, [("Этап 2.2", "2.2")], "facts"))
        self.assertEqual(form.deferred, ["facts"])
        self.assertEqual(combo.clears, 0)

    def test_combo_reports_its_popup(self):
        ui = _src(UI)
        self.assertIn("class StableComboBox(QComboBox):", ui)
        combo_src = _block(ui, "class StableComboBox(QComboBox):",
                           "class GovCasesDialog(QDialog):")
        for name in ("def showPopup", "def hidePopup", "def popup_is_open",
                     "self._popup_open"):
            self.assertIn(name, combo_src)
        self.assertIn("StableComboBox(", _method(ui, "_build_work_window"))
        self.assertIn("on_close=self._on_combo_popup_closed", ui)

    def test_typing_defers_the_refresh(self):
        ui = _src(UI)
        self.assertIn("TYPING_IDLE_SEC", ui)
        busy = _method(ui, "_ui_busy")
        self.assertIn("self._combo_popup_open()", busy)
        self.assertIn("self._typing_now()", busy)
        typing = _method(ui, "_typing_now")
        self.assertIn("TYPING_IDLE_SEC", typing)
        watcher = _method(ui, "_watch_typing")
        self.assertIn("installEventFilter(self)", watcher)
        self.assertIn("QEvent.Type.KeyPress", _method(ui, "eventFilter"))
        deferred = _method(ui, "_apply_deferred_sync")
        self.assertIn("if self._ui_busy():", deferred)
        self.assertIn('self._defer_sync("deferred")', deferred)

    def test_timer_is_single_shot(self):
        init = _block(_src(UI), "def __init__(self, cfg", "def _label(")
        self.assertIn("self._sync_timer.setSingleShot(True)", init)
        self.assertIn("self._sync_timer.timeout.connect", init)


# --------------------------------------------------------------------------- #
#  п.5: заявитель и нарушитель из ответа ИИ
# --------------------------------------------------------------------------- #

class IntakeRowTests(unittest.TestCase):
    """«№2 заявитель не распознан» — список игнорировал `intake_fields`."""

    def _case(self, **fields):
        case = cases.GovCase(number="2", status=cases.STATUS_IN_PROGRESS,
                             doc_base="0002")
        case.intake_fields = dict(fields)
        return case

    def test_summary_is_built_from_the_answer(self):
        case = self._case(applicant_name="Lorenzo Shroud", applicant_id="34074",
                          subject_name="Heavy Asaev", subject_id="110384",
                          subject_department="LSPD (CPD)",
                          appeal_date="25.11.2026")
        summary = cases.intake_summary(case)
        self.assertIn("заявитель Lorenzo Shroud 34074", summary)
        self.assertIn("нарушитель Heavy Asaev (LSPD (CPD))", summary)
        self.assertIn("событие 25.11.2026", summary)

    def test_summary_is_empty_without_the_answer(self):
        self.assertEqual(cases.intake_summary(cases.GovCase(number="3")), "")

    def test_row_shows_the_applicant_from_the_answer(self):
        case = self._case(applicant_name="Lorenzo Shroud",
                          subject_name="Heavy Asaev")
        case.appeal_raw = "Прошу провести проверку сотрудника."
        row = cases.case_row(case)
        self.assertIn("Lorenzo Shroud", row["title"])
        self.assertNotIn("не распознан", row["title"])
        self.assertEqual(row["applicant"], "Lorenzo Shroud")
        self.assertEqual(row["subject"], "Heavy Asaev")
        self.assertIn("нарушитель Heavy Asaev", row["requisites"])

    def test_row_falls_back_to_the_appeal_text(self):
        case = cases.GovCase(number="4", appeal_raw="")
        row = cases.case_row(case)
        self.assertIn("заявитель не распознан", row["title"])
        self.assertEqual(row["requisites"], "")

    def test_cases_dialog_uses_the_row(self):
        ui = _src(UI)
        self.assertIn("case_rows(", ui)
        self.assertIn('row.get("requisites"', ui)


# --------------------------------------------------------------------------- #
#  п.6: одна запись истории на номер постановления
# --------------------------------------------------------------------------- #

class HistoryVersionTests(unittest.TestCase):
    """«- 0002-1 Heavy Asaev» дважды → одна строка с отметкой версии."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "postanovleniya"
        self.history = gdata.GovHistory(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def _doc(self, number="0002-1", created=None, **fields):
        doc = gdata.PostanovlenieProkurora(
            doc_number=number,
            subject_name=fields.pop("subject", "Heavy Asaev"),
            appeal_raw="обращение", **fields)
        doc.created_at = float(created or time.time())
        return doc

    def test_two_snapshots_of_one_number_give_one_row(self):
        now = time.time()
        path1 = self.history.save(self._doc(created=now - 600))
        path2 = self.history.save(self._doc(created=now))
        os.utime(path1, (now - 600, now - 600))
        os.utime(path2, (now, now))

        rows = self.history.items()
        self.assertEqual(len(rows), 1)
        self.assertEqual(Path(rows[0][0]), path2)          # показан свежий
        self.assertEqual(self.history.version_of("0002-1"), 2)
        labels = self.history.labels()
        self.assertEqual(len(labels), 1)
        self.assertIn("0002-1", labels[0])
        self.assertIn("Heavy Asaev", labels[0])
        self.assertIn("· версия 2", labels[0])

    def test_different_numbers_stay_separate(self):
        now = time.time()
        for index, number in enumerate(("0002-1", "0002-2", "0002-3")):
            self.history.save(self._doc(number=number, created=now - index * 60))
        self.assertEqual(len(self.history.items()), 3)
        self.assertFalse(any("версия" in label for label in self.history.labels()))

    def test_replace_skips_an_identical_snapshot(self):
        doc = self._doc(created=time.time() - 30)
        first = self.history.replace(doc)
        again = self.history.replace(doc)
        self.assertEqual(first, again)
        self.assertEqual(len(self.history.items()), 1)
        self.assertEqual(self.history.version_of("0002-1"), 1)

    def test_replace_keeps_a_changed_document(self):
        self.history.replace(self._doc(created=time.time() - 30))
        self.history.replace(self._doc(created=time.time(),
                                       situation="Новые обстоятельства."))
        self.assertEqual(len(self.history.items()), 1)
        self.assertEqual(self.history.version_of("0002-1"), 2)
        self.assertIn("· версия 2", self.history.labels()[0])

    def test_drafts_are_not_shown(self):
        doc = self._doc(created=time.time())
        self.history.save(doc, draft=True)
        self.assertEqual(self.history.items(), [])
        self.history.replace(doc)
        self.assertEqual(len(self.history.items()), 1)


# --------------------------------------------------------------------------- #
#  находка 2: сырой ответ ИИ — в папку этапа
# --------------------------------------------------------------------------- #

class RawAnswerStageTests(unittest.TestCase):
    """«ИИ-ответ.txt» больше не затирается следующим ответом."""

    def setUp(self):
        self.main = _src(MAIN)
        self.block = _block(self.main, "def _apply_gov_answer",
                            "def _open_prompts_folder")

    def test_raw_answer_is_not_written_before_parsing(self):
        """В v66 сырьё писалось ДО разбора — в папку обращения."""
        head = _block(self.main, "def _apply_gov_answer", "answer_text = pgov.extract_ai_answer")
        self.assertNotIn("raw_path.write_text", head)
        self.assertIn("raw_folder = self._gov_raw_answer_folder(", self.block)
        self.assertIn("saved_text = text.replace(", self.block)

    def test_stage_folder_is_used_for_the_raw_answer(self):
        folder = _method(self.main, "_gov_raw_answer_folder")
        self.assertIn("pcases.node_folder_guess(", folder)
        self.assertIn("case.new_numbering()", folder)
        self.assertIn("return fallback", folder)

    def test_raw_answer_is_written_once_into_the_stage_dir(self):
        self.assertEqual(self.block.count('(stage_dir / "ИИ-ответ.txt").write_text'),
                         1)
        # и для новой, и для прежней нумерации — в папку ЭТАПА
        tail = self.block.split('main_paths = prender.save_pages', 1)[1]
        self.assertIn('(stage_dir / "ИИ-ответ.txt").write_text', tail)

    def test_failed_render_does_not_overwrite_the_live_version(self):
        safely, diag = _method_fn("_gov_save_raw_safely", module=MAIN,
                                  class_name="MainWindow")
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "Этап 2.1 — истребование"
            first = safely(object(), folder, "первый ответ")
            self.assertEqual(first.name, "ИИ-ответ.txt")
            self.assertEqual(first.read_text(encoding="utf-8"), "первый ответ")
            # действующая версия есть (PNG напечатан) — сырьё кладётся рядом
            _png(folder, "0002-2 — Этап 2.1 — стр. 1.png")
            second = safely(object(), folder, "ответ с ошибкой")
            self.assertEqual(second.name, "ИИ-ответ (не напечатан).txt")
            self.assertEqual(first.read_text(encoding="utf-8"), "первый ответ")
            self.assertIn("gov.raw_saved", diag.names())


# --------------------------------------------------------------------------- #
#  находка 3: первая настоящая версия — «Версия-001»
# --------------------------------------------------------------------------- #

class FirstVersionIsRealTests(unittest.TestCase):
    """Пустая папка этапа (заявка без PNG) версией не считается."""

    def test_markers_and_guard(self):
        self.assertIn("*.png", dl.VERSION_MARKERS)
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp) / "Этап 1 — принятие"
            stage.mkdir()
            (stage / "Заявка (Этап 1).txt").write_text("заявка", encoding="utf-8")
            (stage / "Обращение.txt").write_text("текст", encoding="utf-8")
            (stage / "Заявка-поля.json").write_text("{}", encoding="utf-8")
            self.assertFalse(dl.has_document(stage))
            self.assertIsNone(dl.snapshot_current(stage))
            self.assertFalse((stage / "Архив").exists())
            _png(stage, "0001-1 — Этап 1 — стр. 1.png")
            self.assertTrue(dl.has_document(stage))
            version = dl.snapshot_current(stage)
            self.assertIsNotNone(version)
            self.assertEqual(version.name, "Версия-001")
            self.assertTrue((version / "0001-1 — Этап 1 — стр. 1.png").is_file())
            self.assertFalse(list(stage.glob("*.png")))

    def test_missing_folder_is_not_a_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(dl.has_document(Path(tmp) / "нет"))

    def test_main_uses_the_guard(self):
        layout = _src("doc_layout.py")
        snapshot = _block(layout, "def snapshot_current", "def archive_matches")
        self.assertIn("has_document(stage)", snapshot)


# --------------------------------------------------------------------------- #
#  Прочее: версия сборки и состав выпуска
# --------------------------------------------------------------------------- #

class ReleaseTests(unittest.TestCase):
    """Версия 2.23.0, `universal-embedded-v67`, промт 1.38."""

    def test_version_is_bumped(self):
        config = _src("config.py")
        self.assertIn('CURRENT_VERSION = "2.23.0"', config)
        build = _src("СБОРКА_NUITKA.txt")
        self.assertIn("2.23.0.0", build)
        self.assertIn("TestLegalUniversal_v67", build)
        script = _src("build_nuitka_standalone.ps1")
        self.assertIn('"--file-version=2.23.0.0"', script)
        self.assertIn('"--product-version=2.23.0.0"', script)
        self.assertIn("universal-embedded-v67", _src("diagnostics.py"))
        self.assertIn('release="universal-embedded-v67"',
                      _src("native_browser.py"))

    def test_remarks_file_exists(self):
        text = (ROOT / "ИСПРАВЛЕНИЯ_v67.md").read_text(encoding="utf-8")
        for mark in ("п.1", "п.2", "п.3", "п.4", "п.5", "п.6", "п.7",
                     "Находка", "Постановление_66v.7z",
                     "20261128-190131-18824-02d216"):
            self.assertIn(mark, text, mark)

    def test_prompt_is_not_shipped(self):
        """Промт лежит вне папки выпуска и в поставку не вкладывается."""
        self.assertFalse(list(ROOT.glob("promt*.txt")))
        prompts = ROOT.parent / "Для новой версии" / "Текущий промт"
        self.assertTrue((prompts / "promt 1.38.txt").is_file())


if __name__ == "__main__":
    unittest.main()
