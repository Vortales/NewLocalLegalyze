"""Тесты постановлений v27: разбор ответа ИИ, данные, история и PNG-бланк.

Проверяем ровно то, на чём держится новая функция:

* триггеры (`===ПОСТАНОВЛЕНИЕ===`, `===КОНЕЦ ОТВЕТА===`) распознаются;
* поля из ответа ИИ попадают в документ, пункты собираются по номерам;
* без триггера и без полей разбор ничего не «выдумывает»;
* документ всегда отдаёт осмысленный .txt для чата и подпись «И. Фамилия»;
* история хранит полный снимок (открыть старое заполнение и поправить);
* PNG-рендер даёт A4 и умеет разбивать длинный документ на страницы.
"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import postanovlenie_data as pdoc  # noqa: E402

AI_ANSWER = """===ПОСТАНОВЛЕНИЕ===
Ведомство: LOS SANTOS COUNTY SHERIFF'S DEPARTMENT
Подразделение: INTERNAL AFFAIRS DIVISION
Вид документа: Постановление о выдаче дисциплинарного взыскания
Заголовок: о выдаче дисциплинарного взыскания
Номер: LSCSD-IAD-141
Дата: 06.11.2026
Постановление вынес: Kirill Goldens
Должность: Major (Майор IAD)
В отношении: Qorick Mythic
Удостоверение: 166759
Звание сотрудника: Cadet
Отдел сотрудника: Patrol
Нарушенный акт: Внутреннего устава Los Santos County Sheriff Department
Пункт нарушения: пункт 19) части 1 статьи а
Взыскание: строгий выговор
Ситуация: Сотрудник оставил пост без разрешения.
Доказательства: Рапорт supervisor.
Постановляющая часть:
1. Назначить сотруднику Qorick Mythic взыскание в виде строгого выговора.
2. Постановление вступает в силу с момента подписания.
3. Контроль оставляю за собой.
===КОНЕЦ ПОСТАНОВЛЕНИЯ===
===КОНЕЦ ОТВЕТА===
"""


class TriggerTests(unittest.TestCase):
    def test_trigger_is_detected_in_any_spacing(self):
        for text in ("===ПОСТАНОВЛЕНИЕ===", "=== ПОСТАНОВЛЕНИЕ ===",
                     "постановление: LSCSD-IAD-1", "Конец: ===КОНЕЦ ПОСТАНОВЛЕНИЯ==="):
            self.assertTrue(pdoc.has_document_trigger(text), text)

    def test_answer_end_is_detected_like_v26_client(self):
        self.assertTrue(pdoc.has_answer_end("текст\n===КОНЕЦ ОТВЕТА==="))
        self.assertTrue(pdoc.has_answer_end("текст\n=== КОНЕЦ ОТВЕТА ==="))
        self.assertFalse(pdoc.has_answer_end("текст без маркера"))

    def test_triggers_are_removed_from_human_readable_text(self):
        clean = pdoc.strip_triggers("Привет\n===ПОСТАНОВЛЕНИЕ===\nТело\n===КОНЕЦ ОТВЕТА===")
        self.assertNotIn("===", clean)
        self.assertIn("Тело", clean)


class ParseTests(unittest.TestCase):
    def test_fields_and_points_are_parsed(self):
        doc = pdoc.parse_ai_answer(AI_ANSWER)
        self.assertIsNotNone(doc)
        self.assertEqual(doc.get("doc_number"), "LSCSD-IAD-141")
        self.assertEqual(doc.get("doc_date"), "06.11.2026")
        self.assertEqual(doc.get("subject_name"), "Qorick Mythic")
        self.assertEqual(doc.get("subject_id"), "166759")
        self.assertEqual(doc.get("penalty"), "строгий выговор")
        self.assertEqual(len(doc.points), 3)
        self.assertIn("строгого выговора", doc.points[0])
        self.assertEqual(doc.source, "ai")

    def test_base_document_fills_the_gaps(self):
        base = pdoc.Postanovlenie(doc_number="LSCSD-IAD-150", author_name="Kirill Goldens")
        doc = pdoc.parse_ai_answer("===ПОСТАНОВЛЕНИЕ===\nВ отношении: Qorick Mythic\n",
                                   base=base)
        self.assertEqual(doc.get("doc_number"), "LSCSD-IAD-150")
        self.assertEqual(doc.get("subject_name"), "Qorick Mythic")

    def test_plain_chat_without_trigger_gives_nothing(self):
        self.assertIsNone(pdoc.parse_ai_answer("Обычный правовой ответ без документа."))
        self.assertIsNone(pdoc.parse_ai_answer(""))

    def test_answer_without_points_gets_points_from_the_charter(self):
        doc = pdoc.parse_ai_answer("===ПОСТАНОВЛЕНИЕ===\nВ отношении: Qorick Mythic\n"
                                   "Взыскание: строгий выговор\n")
        points = doc.decision_points()
        self.assertEqual(len(points), 3)
        self.assertIn("строгого выговора", points[0])

    def test_free_text_answer_keeps_the_story(self):
        doc = pdoc.parse_ai_answer(
            "===ПОСТАНОВЛЕНИЕ===\nНомер: LSCSD-IAD-9\n"
            "Сотрудник отсутствовал на построении.\n")
        self.assertIn("отсутствовал", doc.get("situation"))


class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.doc = pdoc.Postanovlenie(
            doc_number="LSCSD-IAD-141",
            doc_date="06.11.2026",
            author_name="Kirill Goldens",
            author_rank="Major (Майор IAD)",
            subject_name="Qorick Mythic",
            subject_id="166759",
            violation_act="Внутреннего устава Los Santos County Sheriff Department",
            violation_clause="пункт 19) части 1 статьи а",
            penalty="строгий выговор",
        )

    def test_signature_is_initial_plus_surname(self):
        self.assertEqual(self.doc.signature(), "K. Goldens")
        self.assertEqual(pdoc.initials_signature("kirill goldens"), "K. Goldens")
        self.assertEqual(pdoc.initials_signature("Qorick"), "Qorick")
        self.assertEqual(pdoc.initials_signature(""), "")

    def test_genitive_forms_are_used_in_the_resolution(self):
        self.assertEqual(pdoc.penalty_instrumental("строгий выговор"), "строгого выговора")
        self.assertEqual(pdoc.penalty_instrumental("увольнение"), "увольнения")
        self.assertEqual(pdoc.clause_genitive("пункт 19) части 1 статьи а"),
                         "пункта 19) части 1 статьи а")
        self.assertIn("в виде строгого выговора", self.doc.decision_points()[0])

    def test_missing_fields_are_named_for_the_user(self):
        empty = pdoc.Postanovlenie()
        missing = empty.missing_fields()
        self.assertIn("Номер документа", missing)
        self.assertEqual(self.doc.missing_fields(), [])

    def test_chat_txt_is_structured_and_named(self):
        text = self.doc.to_chat_txt()
        self.assertTrue(text.startswith("ЗАЯВКА НА ПОСТАНОВЛЕНИЕ"))
        self.assertIn("Номер: LSCSD-IAD-141", text)
        self.assertIn("Предлагаемые пункты", text)
        # v30.3: в заявке НЕТ триггеров — иначе она принимается за ответ ИИ
        self.assertNotIn(pdoc.TRIGGER_ANSWER_END, text)
        self.assertNotIn("===ПОСТАНОВЛЕНИЕ===", text)  # маркер выдаёт только ИИ
        self.assertFalse(pdoc.has_document_trigger(text))
        self.assertEqual(self.doc.chat_txt_name(), "ПОСТАНОВЛЕНИЕ LSCSD-IAD-141.txt")

    def test_chat_txt_has_no_duplicated_fields(self):
        text = self.doc.to_chat_txt()
        self.assertEqual(text.count("Вид документа:"), 1)

    def test_auto_number_follows_history(self):
        self.assertEqual(self.doc.auto_number([]), "LSCSD-IAD-1")
        self.assertEqual(
            self.doc.auto_number(["LSCSD-IAD-7", "LSCSD-IAD-140"], prefix="LSCSD-IAD"),
            "LSCSD-IAD-141")

    def test_round_trip_through_dict(self):
        clone = pdoc.Postanovlenie.from_dict(self.doc.to_dict())
        for key, _form, _label in pdoc.DOC_FIELDS:
            self.assertEqual(clone.get(key), self.doc.get(key), key)


class RemarksV28Tests(unittest.TestCase):
    """Замечания к v27 — каждая строка проверяется отдельным тестом."""

    #: Ответ ровно из замечаний (включая мусор интерфейса и «не указано»).
    ANSWER = """===ПОСТАНОВЛЕНИЕ===
Вид документа: Постановление о снятии дисциплинарного взыскания
Заголовок: Снятие дисциплинарного взыскания
Номер: LSCSD-IAD-1
Дата: 06.11.2026
Постановление вынес: Marcus Nomad
Должность: Куратор отдела IAD (Assistent sheriff), Сенатор
В отношении: Aliya Nomad
Удостоверение: 131485
Звание сотрудника: не указано
Отдел сотрудника: не указан
Нарушенный акт: Согласно обстоятельств.
Пункт нарушения: Согласно обстоятельств.
Взыскание: предупреждение
Ситуация: Сотрудник грубо нарушил дресс код и суббординацию
Доказательства: не указаны
Постановляющая часть:
1. Назначить сотруднику Aliya Nomad взыскание в виде предупреждения.
2. Постановление вступает в силу с момента подписания.
3. Контроль оставляю за собой.
Дополнительно: Перейти к основному контенту
Справка по использованию специальных возможностей
Memphis.pdf
PDF
Шаблон.txt
File
Перейти к предыдущему запросу
Режим ИИ обрабатывает ваш запрос…
All items removed from input context.
===КОНЕЦ ПОСТАНОВЛЕНИЯ===
===КОНЕЦ ОТВЕТА===
"""

    def test_ui_noise_never_reaches_the_document(self):
        doc = pdoc.parse_ai_answer(self.ANSWER)
        joined = " ".join([
            doc.get("situation"), doc.get("extra"), doc.get("evidence"),
            " ".join(doc.points),
        ]).lower()
        for noise in ("перейти к основному контенту", "memphis.pdf", "шаблон.txt",
                      "all items removed", "режим ии обрабатывает", "pdf"):
            self.assertNotIn(noise, joined, noise)

    def test_placeholders_become_empty_not_text(self):
        doc = pdoc.parse_ai_answer(self.ANSWER)
        self.assertEqual(doc.get("subject_rank"), "")
        self.assertEqual(doc.get("subject_department"), "")
        self.assertEqual(doc.get("evidence"), "")

    def test_removal_document_does_not_assign_a_penalty(self):
        doc = pdoc.parse_ai_answer(self.ANSWER)
        self.assertEqual(doc.kind(), "removal")
        first = doc.decision_points()[0].lower()
        self.assertIn("снять", first)
        self.assertNotIn("назначить", first)

    def test_penalty_document_keeps_assignment(self):
        doc = pdoc.Postanovlenie(doc_type=pdoc.DOC_TYPES[0], subject_name="A B",
                                 penalty="строгий выговор")
        self.assertIn("назначить", doc.decision_points()[0].lower())

    def test_optional_violation_fields_are_not_required(self):
        doc = pdoc.Postanovlenie(doc_number="LSCSD-IAD-1", doc_date="06.11.2026",
                                 author_name="Marcus Nomad", author_rank="Куратор IAD",
                                 subject_name="Aliya Nomad", subject_id="131485",
                                 situation="Нарушение дресс-кода.")
        self.assertEqual(doc.missing_fields(), [])

    def test_chat_txt_asks_the_ai_to_fill_what_the_user_skipped(self):
        doc = pdoc.Postanovlenie(doc_number="LSCSD-IAD-3", doc_date="07.11.2026",
                                 subject_name="Aliya Nomad", subject_id="131485",
                                 situation="Нарушение дресс-кода.")
        text = doc.to_chat_txt()
        self.assertIn("Взыскание: нет данных — определи сам", text)
        self.assertIn("Пункт нарушения: нет данных — определи сам", text)
        # без взыскания шаблонные пункты в заявку не попадают
        self.assertNotIn("Назначить сотруднику", text)

    def test_chat_txt_has_no_ui_noise_even_if_it_was_in_the_answer(self):
        doc = pdoc.parse_ai_answer(self.ANSWER)
        text = doc.to_chat_txt()
        for noise in ("Memphis.pdf", "All items removed", "Режим ИИ"):
            self.assertNotIn(noise, text)

    def test_handwriting_font_is_used_for_the_signature(self):
        import doc_render
        assets = doc_render.DocAssets(ROOT)
        self.assertTrue(assets.fonts.script_is_handwritten)
        self.assertIn("Caveat", Path(assets.fonts.script_path).name)
        # основной текст — PT Serif, а не «системный» DejaVu
        self.assertIn("PTSerif", Path(assets.fonts.body_path).name)
        self.assertIn("PTSerif", Path(assets.fonts.bold_path).name)


class RemarksV29Tests(unittest.TestCase):
    """Замечания к v28: ресурсы бланка, «взыскание в виде взыскания», должность, ФИО."""

    # ── ресурсы: герб, печать и рукописный шрифт находятся при любом запуске ──
    def test_assets_are_found_even_if_app_dir_has_no_data(self):
        """APP_DIR = %APPDATA%\Legalyze, а ресурсы лежат рядом с программой."""
        import tempfile
        import doc_render
        empty = Path(tempfile.mkdtemp())          # «пустой» APP_DIR
        assets = doc_render.DocAssets(empty)
        described = assets.describe()
        self.assertTrue(described["gerb"].endswith("gerb_lscsd.png"), described)
        self.assertTrue(described["pechat"].endswith("stamp_lscsd.png"), described)
        self.assertTrue(Path(described["shrift_podpisi"]).is_file(), described)
        self.assertTrue(assets.fonts.script_is_handwritten, described["shrift_podpisi"])
        self.assertEqual(Path(assets.fonts.body_path).name, "PTSerif-Regular.ttf")

    def test_assets_missing_files_are_reported_not_crashing(self):
        import tempfile
        import doc_render
        with tempfile.TemporaryDirectory() as tmp:
            assets = doc_render.DocAssets(Path(tmp), coat=Path(tmp) / "нет.png")
            described = assets.describe()
            self.assertEqual(described["gerb"], "")
            pages = doc_render.render_document(
                pdoc.Postanovlenie(doc_number="X-1", subject_name="A B"), assets)
            self.assertEqual(len(pages), 1)

    # ── взыскание ──
    def test_penalty_is_never_disciplinary_action_in_itself(self):
        doc = pdoc.Postanovlenie(doc_number="LSCSD-IAD-3", subject_name="Aliya Nomad",
                                 penalty="дисциплинарное взыскание")
        first = doc.decision_points()[0].lower()
        self.assertNotIn("в виде дисциплинарного взыскания", first)
        self.assertIn("назначить", first)

    def test_penalty_is_normalized_to_the_list_of_the_ustav(self):
        cases = {
            "строгого выговора": "строгий выговор",
            "устный": "устный выговор",
            "выговор": "строгий выговор",
            "увольнение": "увольнение",
            "переаттестации": "переаттестация",
            "предупреждение": "предупреждение",
            "понижение": "понижение",
            "какой-то штраф": "",
        }
        for raw, expected in cases.items():
            self.assertEqual(pdoc.normalize_penalty(raw), expected, raw)

    def test_specific_penalty_appears_in_the_resolution(self):
        doc = pdoc.Postanovlenie(subject_name="Aliya Nomad", subject_id="131485",
                                 penalty="строгий выговор")
        self.assertIn("в виде строгого выговора", doc.decision_points()[0])

    # ── должность ──
    def test_only_faction_position_is_used(self):
        for raw, expected in (
            ("Куратор отдела IAD (Assistent sheriff), Сенатор", "Куратор отдела IAD (Assistent sheriff)"),
            ("Куратор отдела IAD (Assistent sheriff); Губернатор", "Куратор отдела IAD (Assistent sheriff)"),
            ("Офицер IAD", "Офицер IAD"),
            ("", ""),
        ):
            self.assertEqual(pdoc.clean_position(raw), expected, raw)

    def test_position_is_cleaned_in_preamble_and_signature(self):
        doc = pdoc.Postanovlenie(author_name="Marcus Nomad",
                                 author_rank="Куратор отдела IAD (Assistent sheriff), Сенатор")
        self.assertNotIn("Сенатор", doc.preamble())
        self.assertEqual(doc.get("author_rank"), "Куратор отдела IAD (Assistent sheriff)")

    # ── ФИО и удостоверение ──
    def test_name_and_id_are_separated_when_the_ai_swapped_them(self):
        doc = pdoc.Postanovlenie(subject_name="Aliya", subject_id="Nomad")
        self.assertEqual(doc.subject_name, "Aliya Nomad")
        self.assertEqual(doc.subject_id, "")
        first = doc.decision_points()[0]
        self.assertNotIn("#Nomad", first)
        self.assertIn("Aliya Nomad", first)

    def test_id_is_digits_only(self):
        doc = pdoc.Postanovlenie(subject_name="Aliya Nomad", subject_id="№131485")
        self.assertEqual(doc.subject_id, "131485")
        doc.set("subject_id", "уд. 85028")
        self.assertEqual(doc.subject_id, "85028")

    def test_name_with_embedded_id_is_cleaned(self):
        doc = pdoc.Postanovlenie(subject_name="Marcus Nomad 85028", subject_id="")
        self.assertEqual(doc.subject_name, "Marcus Nomad")
        self.assertEqual(pdoc.split_subject("Aliya #131485", "131485")[0], "Aliya")

    # ── заявка просит ИИ переработать обстоятельства ──
    def test_chat_txt_keeps_the_words_without_service_marks(self):
        """v30.3: слова пользователя уходят как есть, но без служебных помет."""
        doc = pdoc.Postanovlenie(doc_number="LSCSD-IAD-4", subject_name="Aliya Nomad",
                                 situation="задержал просто так и ходила с ним")
        text = doc.to_chat_txt()
        self.assertIn("Обстоятельства: задержал просто так и ходила с ним", text)
        self.assertNotIn("сырые слова", text.lower())


class RemarksV30Tests(unittest.TestCase):
    """v30: «Ситуация» vs «Обстоятельства», markdown от ИИ и папка выдачи."""

    #: Ровно тот стиль, которым ИИ ответил в реальном чате.
    MD_ANSWER = """===ПОСТАНОВЛЕНИЕ===
- **Ведомство:** LOS SANTOS COUNTY SHERIFF'S DEPARTMENT
- **Подразделение:** INTERNAL AFFAIRS DIVISION
- **Номер:** LSCSD-IAD-1
- **Дата:** 06.11.2026
- **Постановление вынес:** Marcus Nomad
- **Должность:** Куратор отдела IAD (Assistent sheriff), Сенатор
- **В отношении:** Aliya Nomad
- **Удостоверение:** `#131485`
- **Взыскание:** строгий выговор
- **Обстоятельства:** Сотрудник грубо нарушил дресс-код и не исполняет приказы.
- **Примечание:** Mem
===КОНЕЦ ПТАНОВЛЕНИЯ===
===КОНЕЦ ОТВЕТА==="""

    #: Ответ-описание картинки (тот, что пришёл в реальном чате).
    DESCRIPTION = """На изображении представлен официальный бланк игрового документа (вероятно, для ролевого сервера GTA V RP) — Постановление о выдаче дисциплинарного взыскания.

**📑 Шапка документа**
- **Логотип:** Вверху по центру расположен круглый шеврон
- **Обстоятельства:** Сотрудник грубо нарушает дресс-код и неисполняет приказы.
**🖋️ Подпись и водяные знаки**
- **Нижний правый угол:** Синяя рукописная подпись
===КОНЕЦ ОТВЕТА==="""

    def test_field_is_called_obstoyatelstva_in_chat_and_document(self):
        labels = dict((key, label) for key, _f, label in pdoc.DOC_FIELDS)
        self.assertEqual(labels["situation"], "Обстоятельства")
        doc = pdoc.Postanovlenie(situation="Нарушение формы одежды.")
        self.assertIn("Обстоятельства", doc.to_chat_txt())
        self.assertIn("Нарушение формы одежды.", doc.to_chat_txt())
        self.assertNotIn("Ситуация:", doc.to_chat_txt())

    def test_old_answers_with_situaciya_are_still_understood(self):
        doc = pdoc.parse_ai_answer("===ПОСТАНОВЛЕНИЕ===\nСитуация: Нарушил форму одежды.\n")
        self.assertEqual(doc.get("situation"), "Нарушил форму одежды.")

    def test_markdown_keys_are_recognized(self):
        doc = pdoc.parse_ai_answer(self.MD_ANSWER)
        self.assertEqual(doc.get("doc_number"), "LSCSD-IAD-1")
        self.assertEqual(doc.get("subject_name"), "Aliya Nomad")
        self.assertEqual(doc.get("subject_id"), "131485")
        self.assertEqual(doc.get("penalty"), "строгий выговор")
        self.assertEqual(doc.get("extra"), "Mem")
        self.assertEqual(doc.get("author_rank"), "Куратор отдела IAD (Assistent sheriff)")

    def test_markdown_never_reaches_the_document_text(self):
        doc = pdoc.parse_ai_answer(self.MD_ANSWER)
        text = doc.to_chat_txt()
        for mark in ("**", "`", "📑", "🖋️"):
            self.assertNotIn(mark, text, mark)

    def test_image_description_is_not_turned_into_a_document(self):
        self.assertTrue(pdoc.looks_like_image_description(self.DESCRIPTION))
        self.assertIsNone(pdoc.parse_ai_answer(self.DESCRIPTION))

    def test_image_description_keeps_the_user_data(self):
        base = pdoc.Postanovlenie(doc_number="LSCSD-IAD-1", subject_name="Aliya Nomad",
                                  situation="Сотрудник грубо нарушил дресс-код.")
        parsed = pdoc.parse_ai_answer(self.DESCRIPTION, base=base)
        self.assertIsNone(parsed)          # документ не пересобирается

    def test_section_headings_are_not_content(self):
        self.assertTrue(pdoc.is_section_heading("📑 Шапка документа"))
        self.assertFalse(pdoc.is_section_heading("Примечание: Mem"))

    # ── папка выдачи: и «что отправил я», и «что ответил ИИ» ──
    def test_ai_answer_is_saved_next_to_the_request(self):
        import json
        import tempfile
        doc = pdoc.parse_ai_answer(self.MD_ANSWER)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "postanovleniya"
            out = pdoc.doc_folder(root, doc.get("doc_number"))
            (out / "Заявка (отправлено в чат).txt").write_text(
                doc.to_chat_txt(), encoding="utf-8")
            written = [p.name for p in pdoc.save_ai_answer(root, doc, self.MD_ANSWER)]
            self.assertEqual(sorted(written), ["ИИ-документ.txt", "ИИ-ответ.txt", "ИИ-поля.json"])
            folder = root / "LSCSD-IAD-1"
            self.assertTrue((folder / "Заявка (отправлено в чат).txt").is_file())
            raw = (folder / "ИИ-ответ.txt").read_text(encoding="utf-8")
            self.assertIn("**Обстоятельства:**", raw)       # ответ как есть
            fields = json.loads((folder / "ИИ-поля.json").read_text(encoding="utf-8"))
            self.assertEqual(fields["поля"]["subject_name"], "Aliya Nomad")

    def test_folder_name_is_safe(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            folder = pdoc.doc_folder(Path(tmp), "LSCSD-IAD-1")
            self.assertEqual(folder.name, "LSCSD-IAD-1")
            empty = pdoc.doc_folder(Path(tmp), "")
            self.assertEqual(empty.name, "без-номера")
            weird = pdoc.doc_folder(Path(tmp), "a/b:c*?")
            self.assertNotIn("/", weird.name)


class RemarksV30_1Tests(unittest.TestCase):
    """v30.1: описание картинки ПЕРЕД блоком не мешает собрать документ из ответа."""

    #: Ответ ровно из письма: сначала описание, потом блок постановления.
    ANSWER = """На изображении представлен официальный документ (постановление о выдаче дисциплинарного взыскания) из внутриигрового или ролевого процесса (вероятно, GTA RP).

**Полный текст документа:**

===ПОСТАНОВЛЕНИЕ===
Ведомство: Los Santos County Sheriff Department
Подразделение: INTERNAL AFFAIRS DIVISION
Вид документа: Постановление о выдаче дисциплинарного взыскания
Заголовок: о выдаче дисциплинарного взыскания
Номер: LSCSD-IAD-1
Дата: 06.11.2026
Постановление вынес: Marcus Nomad
Должность: Куратор отдела IAD (Assistent sheriff)
В отношении: Aliya Nomad
Удостоверение: 131485
Нарушенный акт: Устав Los Santos County Sheriff Department
Пункт нарушения: пункта 8.5 и пункта 8.1
Взыскание: строгий выговор
Обстоятельства: При проведении проверки установлено, что сотрудник Aliya Nomad 06.11.2026 допустил грубое нарушение дресс-кода, а также проигнорировал законные приказы вышестоящего руководства.
Доказательства: не представлены
Постановляющая часть:
1. За нарушение пунктов 8.5 и 8.1 Устава LSCSD привлечь сотрудника Aliya Nomad к дисциплинарной ответственности в виде строгого выговора.
2. Настоящее постановление вступает в силу с момента его"""

    def test_block_is_extracted_even_after_the_image_description(self):
        block = pdoc.extract_document_block(self.ANSWER)
        self.assertTrue(block.startswith("===ПОСТАНОВЛЕНИЕ"))
        self.assertIn("Пункт нарушения: пункта 8.5 и пункта 8.1", block)
        self.assertNotIn("на изображении", block.lower())

    def test_answer_describing_an_image_still_builds_the_document(self):
        base = pdoc.Postanovlenie(doc_number="LSCSD-IAD-1", subject_name="Aliya Nomad",
                                  situation="сырые слова")
        doc = pdoc.parse_ai_answer(self.ANSWER, base=base)
        self.assertIsNotNone(doc)
        self.assertEqual(doc.get("penalty"), "строгий выговор")
        self.assertEqual(doc.get("violation_clause"), "пункта 8.5 и пункта 8.1")
        self.assertIn("При проведении проверки установлено", doc.get("situation"))
        # данные формы не затёрты сырым текстом пользователя
        self.assertNotIn("сырые слова", doc.get("situation"))
        self.assertEqual(doc.get("doc_number"), "LSCSD-IAD-1")

    def test_truncated_answer_without_the_end_trigger_is_still_parsed(self):
        """.png формируется, даже если ИИ не дописал «===КОНЕЦ ПОСТАНОВЛЕНИЯ===»."""
        truncated = self.ANSWER.split("Постановляющая часть:")[0]
        doc = pdoc.parse_ai_answer(truncated)
        self.assertIsNotNone(doc)
        self.assertEqual(doc.get("subject_name"), "Aliya Nomad")

    def test_block_points_are_parsed(self):
        doc = pdoc.parse_ai_answer(self.ANSWER)
        self.assertTrue(doc.points)
        self.assertIn("строгого выговора", doc.points[0])

    def test_saved_files_include_the_ai_answer(self):
        import tempfile
        doc = pdoc.parse_ai_answer(self.ANSWER)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "postanovleniya"
            written = [p.name for p in pdoc.save_ai_answer(root, doc, self.ANSWER)]
            self.assertIn("ИИ-ответ.txt", written)
            raw = (root / "LSCSD-IAD-1" / "ИИ-ответ.txt").read_text(encoding="utf-8")
            self.assertIn("На изображении представлен", raw)   # как есть
            self.assertIn("===ПОСТАНОВЛЕНИЕ===", raw)

    def test_png_copy_lands_next_to_the_request(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "postanovleniya"
            doc = pdoc.parse_ai_answer(self.ANSWER)
            folder = pdoc.doc_folder(root, doc.get("doc_number"))
            other = Path(tmp) / "выбранная папка"
            other.mkdir()
            import doc_render
            made = doc_render.render_to_png(doc, other, "LSCSD-IAD-1")
            copied = pdoc.copy_pngs_into(folder, made)
            self.assertEqual([p.name for p in copied], ["LSCSD-IAD-1.png"])
            self.assertTrue((folder / "LSCSD-IAD-1.png").is_file())
            self.assertTrue(made[0].is_file())          # исходник не потерян

    def test_title_without_the_word_postanovlenie_is_fixed(self):
        cases = {
            "о выдаче дисциплинарного взыскания": "Постановление о выдаче дисциплинарного взыскания",
            "Снятие дисциплинарного взыскания": "Постановление о снятии дисциплинарного взыскания",
            "Проведение внутренней проверки": "Постановление о проведении внутренней проверки",
            "Отстранение от службы": "Постановление об отстранении от службы",
            "": "Постановление о выдаче дисциплинарного взыскания",
        }
        for raw, expected in cases.items():
            doc = pdoc.Postanovlenie(doc_type=pdoc.DOC_TYPES[0], doc_title=raw)
            self.assertEqual(doc.document_title(), expected, raw)

    def test_empty_answer_is_not_treated_as_a_document(self):
        self.assertEqual(pdoc.extract_document_block("просто текст без блока"), "")
        self.assertIsNone(pdoc.parse_ai_answer("просто текст без блока"))
        self.assertIsNone(pdoc.parse_ai_answer(""))


class FormDoesNotRenderTests(unittest.TestCase):
    """v30.2: бланк печатается ТОЛЬКО из ответа ИИ, а не из формы.

    Замечание: на «Сформировать» в PNG попадали данные конструктора, а не
    ИИ-документ.txt, и файлы ИИ оказывались в другой папке (%APPDATA%).
    """

    def test_submit_does_not_render_a_png(self):
        src = Path("postanovlenie_ui.py").read_text(encoding="utf-8")
        block = src[src.index("def _form(self)"):]
        block = block[:block.index("def mousePressEvent")]
        self.assertNotIn("render.save_pages", block,
                         "«Сформировать» не должен печатать PNG из формы")
        self.assertNotIn("render.render_document", block,
                         "«Сформировать» не должен строить бланк из формы")

    def test_answer_is_rendered_from_the_ai_document_only(self):
        src = Path("main.py").read_text(encoding="utf-8")
        block = src[src.index("def _apply_document_answer"):]
        block = block[:block.index("def _restart_after_document")]
        self.assertIn("prender.render_document(doc,", block)
        self.assertIn("parse_ai_answer", block)

    def test_default_folder_is_next_to_the_program(self):
        import config
        folder = config.postanovlenie_dir({})
        self.assertEqual(folder.name, "postanovleniya")
        self.assertEqual(folder.parent, Path(config.__file__).resolve().parent)
        self.assertNotIn("AppData", str(folder))
        self.assertNotIn("Desktop", str(folder))
        # выбранная пользователем папка имеет приоритет
        self.assertEqual(config.postanovlenie_dir({"postanovlenie_dir": "/tmp/выбрано"}),
                         Path("/tmp/выбрано"))

    def test_document_txt_matches_what_is_printed(self):
        """В ИИ-документ.txt тот же текст, что и в PNG (порядок блоков)."""
        doc = pdoc.Postanovlenie(
            doc_number="LSCSD-IAD-1", doc_date="06.11.2026",
            author_name="Marcus Nomad", author_rank="Куратор отдела IAD (Assistent sheriff)",
            subject_name="Aliya Nomad", subject_id="131485",
            violation_clause="пункта 8.5 и пункта 8.1",
            violation_act="Устав Los Santos County Sheriff Department",
            penalty="строгий выговор",
            situation="При проведении проверки установлено, что сотрудник допустил нарушение.",
            evidence="не представлены",
        )
        text = doc.to_document_txt()
        for expected in ("LOS SANTOS COUNTY SHERIFF'S DEPARTMENT", "INTERNAL AFFAIRS DIVISION",
                         "Постановление о выдаче дисциплинарного взыскания",
                         "Дата: 06.11.2026", "№ LSCSD-IAD-1", "ПОСТАНОВИЛ:",
                         "Основание:", "Обстоятельства:", "Доказательства:",
                         "Подпись: Marcus Nomad"):
            self.assertIn(expected, text)
        self.assertLess(text.index("ПОСТАНОВИЛ:"), text.index("Основание:"))

    def test_ai_answer_files_live_in_the_chosen_folder(self):
        """История, ИИ-документ.txt и PNG — в ВЫБРАННОЙ папке, а не в %APPDATA%."""
        src = Path("postanovlenie_ui.py").read_text(encoding="utf-8")
        self.assertIn("def _documents_root", src)
        self.assertIn("return postanovlenie_dir(self.cfg)", src)
        main_src = Path("main.py").read_text(encoding="utf-8")
        self.assertIn("return Path(postanovlenie_dir(self.cfg))", main_src)
        self.assertNotIn("Path(postanovlenie_dir(self.cfg)).parent", main_src)


class OwnRequestTests(unittest.TestCase):
    """v30.3: главная причина — заявка принималась за ответ ИИ.

    Заявка начиналась словом «ПОСТАНОВЛЕНИЕ» и кончалась «===КОНЕЦ ОТВЕТА===»,
    клиент читал её из чата и печатал PNG из данных конструктора, а настоящий
    ответ ИИ приходил позже и только перезаписывал ИИ-ответ.txt.
    """

    def _request(self, **kw):
        return pdoc.Postanovlenie(**kw).to_chat_txt()

    def test_request_is_not_mistaken_for_the_answer(self):
        text = self._request(doc_number="LSCSD-IAD-1", subject_name="Aliya Nomad",
                             subject_id="131485",
                             situation="Сотрудник грубо нарушил дресс-код")
        self.assertFalse(pdoc.has_document_trigger(text),
                         "в заявке не должно быть триггера ответа")
        self.assertEqual(pdoc.extract_document_block(text), "")
        self.assertIsNone(pdoc.parse_ai_answer(text))

    def test_request_has_no_service_marks(self):
        text = self._request(doc_number="LSCSD-IAD-1",
                             situation="Сотрудник грубо нарушил дресс-код")
        for forbidden in ("сырые слова", "определить по обстоятельствам",
                          pdoc.TRIGGER_ANSWER_END, pdoc.TRIGGER_DOC_START):
            self.assertNotIn(forbidden, text)
        self.assertIn("Обстоятельства: Сотрудник грубо нарушил дресс-код", text)

    def test_own_request_is_recognised_by_text(self):
        text = self._request(doc_number="LSCSD-IAD-1",
                             situation="Сотрудник грубо нарушил дресс-код")
        self.assertTrue(pdoc.is_own_request(text, text))
        self.assertTrue(pdoc.is_own_request("префикс\n" + text + "\nхвост"))
        self.assertFalse(pdoc.is_own_request(
            "На изображении представлен документ.\n\n===ПОСТАНОВЛЕНИЕ===\n"
            "Номер: LSCSD-IAD-1\n===КОНЕЦ ПОСТАНОВЛЕНИЯ===", text))

    def test_plain_mention_is_not_a_trigger(self):
        for text in ("Постановление вынес: Marcus Nomad",
                     "Настоящее постановление вступает в силу.",
                     "На изображении представлен официальный бланк — Постановление "
                     "о выдаче дисциплинарного взыскания."):
            self.assertFalse(pdoc.has_document_trigger(text), text)

    def test_real_marker_is_a_trigger(self):
        for text in ("===ПОСТАНОВЛЕНИЕ===\nНомер: 1",
                     "=== ПОСТАНОВЛЕНИЕ ===",
                     "**===ПОСТАНОВЛЕНИЕ===**",
                     "ПОСТАНОВЛЕНИЕ\nНомер: 1",
                     "ПОСТАНОВЛЕНИЕ: LSCSD-IAD-1",
                     "Концовка: ===КОНЕЦ ПОСТАНОВЛЕНИЯ==="):
            self.assertTrue(pdoc.has_document_trigger(text), text)

    def test_client_skips_its_own_request_before_parsing(self):
        src = Path("main.py").read_text(encoding="utf-8")
        block = src[src.index("def _apply_document_answer"):]
        block = block[:block.index("def _restart_after_document")]
        self.assertIn("is_own_request", block)
        # ответ не печатается, пока не пришёл целиком
        self.assertIn("answer_still_streaming", block)
        self.assertLess(block.index("is_own_request"), block.index("render_document"))
        self.assertLess(block.index("answer_still_streaming"), block.index("save_pages"))

    def test_request_text_is_remembered_on_submit(self):
        src = Path("main.py").read_text(encoding="utf-8")
        self.assertIn("_doc_request_text", src)


class HistoryTests(unittest.TestCase):
    def test_history_keeps_full_snapshot_for_editing(self):
        with tempfile.TemporaryDirectory() as tmp:
            history = pdoc.DocumentHistory(Path(tmp))
            doc = pdoc.Postanovlenie(doc_number="LSCSD-IAD-1", subject_name="Qorick Mythic",
                                     situation="Первая редакция")
            history.save(doc)
            doc.situation = "Вторая редакция"
            history.save(doc)

            self.assertEqual(len(history.items()), 2)
            self.assertEqual(len(history.labels()), 2)
            self.assertEqual(history.used_numbers(), ["LSCSD-IAD-1", "LSCSD-IAD-1"])

            loaded = history.load_index(0)
            self.assertIsNotNone(loaded)
            self.assertIn(loaded.get("situation"),
                          {"Первая редакция", "Вторая редакция"})
            self.assertTrue(history.labels()[0].endswith("LSCSD-IAD-1 · Qorick Mythic"))

    def test_broken_history_file_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "20260101-000000.json").write_text("{битый", encoding="utf-8")
            history = pdoc.DocumentHistory(folder)
            self.assertEqual(history.items(), [])


class RegulationTests(unittest.TestCase):
    def test_charter_penalties_match_the_list_of_the_ustav(self):
        reg = pdoc.Regulation(pdoc.default_regulation_path(ROOT / "data"))
        self.assertTrue(reg.title)
        penalties = [p.lower() for p in reg.penalties()]
        for expected in ("устный выговор", "строгий выговор", "увольнение"):
            self.assertIn(expected, penalties)

    def test_unknown_clause_is_not_invented(self):
        reg = pdoc.Regulation(pdoc.default_regulation_path(ROOT / "data"))
        self.assertEqual(reg.clause_text("пункт 9999) части 1 статьи zz"), "")

    def test_clause_is_found_in_the_real_ustav_format(self):
        reg = pdoc.Regulation(pdoc.default_regulation_path(ROOT / "data"))
        found = reg.clause_text("пункт 3.1 устава")
        self.assertTrue(found.startswith("3.1"), found[:40])
        self.assertIn("дисциплинарное взыскание", found.lower())
        self.assertTrue(reg.clause_text("4.1").startswith("4.1"))


class RenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import doc_render
        except Exception as exc:                       # pragma: no cover
            raise unittest.SkipTest("Pillow недоступен: %s" % exc)
        cls.render = doc_render

    def _doc(self, points=None):
        return pdoc.Postanovlenie(
            doc_number="LSCSD-IAD-141",
            doc_date="06.11.2026",
            author_name="Kirill Goldens",
            author_rank="Major (Майор IAD)",
            subject_name="Qorick Mythic",
            subject_id="166759",
            violation_act="Внутреннего устава Los Santos County Sheriff Department",
            violation_clause="пункт 19) части 1 статьи а",
            penalty="строгий выговор",
            situation="Сотрудник оставил пост без разрешения.",
            points=list(points or []),
        )

    def test_single_page_is_a4_at_200_dpi(self):
        pages = self.render.render_document(self._doc())
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0].size, (self.render.PAGE_W, self.render.PAGE_H))
        # 200 dpi * 8.27" ≈ 1654 px, 11.69" ≈ 2339 px
        self.assertEqual(self.render.PAGE_W, 1654)

    def test_long_document_is_split_into_pages(self):
        points = [f"Пункт {i}. " + "Обстоятельства и правовые основания. " * 8
                  for i in range(1, 12)]
        pages = self.render.render_document(self._doc(points))
        self.assertGreaterEqual(len(pages), 2)
        for page in pages:
            self.assertEqual(page.size, (self.render.PAGE_W, self.render.PAGE_H))

    def test_png_files_are_written_with_readable_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            written = self.render.render_to_png(self._doc(), Path(tmp), "LSCSD-IAD-141")
            self.assertEqual(len(written), 1)
            self.assertEqual(written[0].name, "LSCSD-IAD-141.png")
            self.assertGreater(written[0].stat().st_size, 5000)

            long_points = [f"Пункт {i}. " + "Текст. " * 120 for i in range(1, 10)]
            multi = self.render.render_to_png(self._doc(long_points), Path(tmp), "multi")
            self.assertGreaterEqual(len(multi), 2)
            self.assertTrue(all("стр." in path.name for path in multi))

    def test_coat_of_arms_is_rendered_on_the_page(self):
        """Замечание v27: на готовом PNG не было герба — проверяем пиксели."""
        assets = self.render.DocAssets(ROOT)
        page = self.render.render_document(self._doc(), assets)[0].convert("RGB")
        # Полоса, где стоит герб (по центру сверху) должна содержать цветные
        # пиксели эмблемы, а не быть полностью белой.
        crop = page.crop((self.render.PAGE_W // 2 - 130, 120,
                          self.render.PAGE_W // 2 + 130, 380))
        colors = crop.getcolors(maxcolors=1_000_000) or []
        non_white = sum(count for count, color in colors
                        if min(color) < 200)
        self.assertGreater(non_white, 2000)

    def test_assets_fall_back_gracefully_without_images(self):
        assets = self.render.DocAssets(ROOT)
        described = assets.describe()
        self.assertTrue(described["gerb"].endswith("gerb_lscsd.png"))
        self.assertTrue(described["pechat"].endswith("stamp_lscsd.png"))
        self.assertTrue(described["shrift_podpisi"])


if __name__ == "__main__":
    unittest.main()
