"""Тесты постановлений v27: разбор ответа ИИ, данные, история и PNG-бланк.

Проверяем ровно то, на чём держится новая функция:

* триггеры (`===ПОСТАНОВЛЕНИЕ===`, `===КОНЕЦ ОТВЕТА===`) распознаются;
* поля из ответа ИИ попадают в документ, пункты собираются по номерам;
* без триггера и без полей разбор ничего не «выдумывает»;
* документ всегда отдаёт осмысленный .txt для чата и подпись «И. Фамилия»;
* история хранит полный снимок (открыть старое заполнение и поправить);
* PNG-рендер даёт A4 и умеет разбивать длинный документ на страницы.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
_P = Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import postanovlenie_data as pdoc  # noqa: E402
import postanovlenie_prokuror_data as gdata  # noqa: E402

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
                         "Основание:", "Обстоятельства:",
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


class V31PipelineTests(unittest.TestCase):
    """v31: ответ ИИ -> файл -> PNG ИЗ ФАЙЛА, в выбранной папке.

    Требование пользователя:
      Документы -> Постановление -> ввод данных -> «Сформировать»;
      как только ответ ИИ получен — он сохраняется, и из него строится .png
      в выбранной папке. Ничего из конструктора в PNG попадать не должно.
    """

    ANSWER = """На изображении представлен официальный документ.

===ПОСТАНОВЛЕНИЕ===
Ведомство: Los Santos County Sheriff Department
Номер: LSCSD-IAD-1
Дата: 06.11.2026
Постановление вынес: Marcus Nomad
В отношении: Aliya Nomad
Взыскание: строгий выговор
Обстоятельства: При проведении проверки установлено нарушение дресс-кода.
Постановляющая часть:
1. Привлечь сотрудника к ответственности в виде строгого выговора.
2. Постановление вступает в силу с момента подписания.
===КОНЕЦ ПОСТАНОВЛЕНИЯ===
===КОНЕЦ ОТВЕТА==="""

    def test_png_is_built_from_the_saved_file(self):
        """PNG строится из ИИ-ответ.txt, прочитанного С ДИСКА, а не из памяти."""
        import tempfile
        import doc_render
        base = pdoc.Postanovlenie(doc_number="LSCSD-IAD-1",
                                  situation="сырые слова пользователя")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "выбранная папка"
            folder = pdoc.doc_folder(root, "LSCSD-IAD-1")
            raw = folder / "ИИ-ответ.txt"
            raw.write_text(self.ANSWER, encoding="utf-8")

            saved = raw.read_text(encoding="utf-8")          # шаг 3 конвейера
            doc = pdoc.parse_ai_answer(saved, base=base)     # шаг 4
            self.assertIsNotNone(doc)
            png = doc_render.render_to_png(doc, root, doc.get("doc_number"))

            self.assertEqual([p.name for p in png], ["LSCSD-IAD-1.png"])
            self.assertTrue((root / "LSCSD-IAD-1.png").is_file())
            self.assertEqual(doc.get("vedomstvo"), "Los Santos County Sheriff Department")
            self.assertNotIn("сырые слова", doc.get("situation"))
            self.assertEqual(len(doc.points), 2)

    def test_answer_without_content_is_not_a_document(self):
        """Один «Номер:» без пунктов — ещё не постановление (нет данных ИИ)."""
        self.assertIsNone(pdoc.parse_ai_answer(
            "===ПОСТАНОВЛЕНИЕ===\nНомер: LSCSD-IAD-1\n===КОНЕЦ ПОСТАНОВЛЕНИЯ==="))
        self.assertIsNotNone(pdoc.parse_ai_answer(self.ANSWER))

    def test_client_saves_answer_first_then_reads_it_back(self):
        """Проверка порядка шагов в клиенте по исходнику."""
        src = Path("main.py").read_text(encoding="utf-8")
        block = src[src.index("def _apply_document_answer"):]
        block = block[:block.index("def _restart_after_document")]
        save_at = block.index("raw_path.write_text")
        read_at = block.index("raw_path.read_text")
        parse_at = block.index("parse_ai_answer(saved_text")
        render_at = block.index("prender.render_document")
        self.assertLess(save_at, read_at)
        self.assertLess(read_at, parse_at)
        self.assertLess(parse_at, render_at)
        # PNG — в корень выбранной папки, копия — в папку документа
        self.assertIn("save_pages(pages, root, stem)", block)
        self.assertIn("copy_pngs_into(folder", block)

    def test_same_answer_is_not_rendered_twice(self):
        src = Path("main.py").read_text(encoding="utf-8")
        block = src[src.index("def _apply_document_answer"):]
        block = block[:block.index("def _restart_after_document")]
        self.assertIn("_doc_handled_text", block)

    def test_form_does_not_render_and_points_to_the_folder(self):
        src = Path("postanovlenie_ui.py").read_text(encoding="utf-8")
        form = src[src.index("def _form(self)"):]
        form = form[:form.index("def mousePressEvent")]
        self.assertNotIn("render_document", form)
        self.assertNotIn("save_pages", form)
        head = src[:src.index("def _choose_folder")]
        self.assertIn("PNG сохраняется сюда", head)


class V32DialogNotPoppingTests(unittest.TestCase):
    """v32: «Бланк не сформирован» больше не появляется.

    Реальная ситуация: ИИ ответил правильно (15 полей), но всплыло окно
    «Бланк не сформирован». Причина — наше собственное сообщение в чате,
    в котором были ЛИТЕРАЛЬНЫЕ маркеры «===ПОСТАНОВЛЕНИЕ===» и
    «===КОНЕЦ ОТВЕТА===». Клиент принимал его за ответ, находил «блок» и
    прекращал опрос, так и не дождавшись настоящего ответа ИИ.
    """

    #: Служебное сообщение, которое уходит в чат вместе с заявкой (v31/v32).
    SEND = (
        "Сформировано постановление, файл заявки приложен: «Заявка (отправлено "
        "в чат).txt». Работай ТОЛЬКО с текстом заявки. Не описывай изображения и "
        "вложения, не пиши «на изображении представлен», не разбирай шапку, "
        "логотип и подписи. Ответь строго по формату промта.")

    #: Ответ ИИ из письма (сокращённо, но со всеми ключевыми полями).
    ANSWER = """===ПОСТАНОВЛЕНИЕ===

Ведомство: Los Santos County Sheriff Department

Номер: LSCSD-IAD-1

Дата: 06.11.2026

Постановление вынес: Marcus Nomad

В отношении: Aliya Nomad

Удостоверение: 131485

Пункт нарушения: Глава VIII пункт 8.11, Глава V пункт 5.7

Взыскание: строгий выговор

Обстоятельства: При проведении проверки служебной деятельности установлено нарушение.

Постановляющая часть:

1. За нарушение пунктов 5.7 и 8.11 выдать взыскание в виде строгого выговора.

2. Настоящее постановление вступает в силу с момента подписания.

===КОНЕЦ ПОСТАНОВЛЕНИЯ===

===КОНЕЦ ОТВЕТА==="""

    #: То же сообщение, но с маркерами — как было в v31 (источник ошибки).
    OLD_SEND = (
        "Сформировано постановление, файл заявки приложен: «Заявка (отправлено "
        "в чат).txt». Ответь одним блоком от ===ПОСТАНОВЛЕНИЕ=== до "
        "===КОНЕЦ ПОСТАНОВЛЕНИЯ=== и заверши ответ ===КОНЕЦ ОТВЕТА===.")

    def test_old_message_with_markers_is_not_a_document(self):
        """Даже со маркерами служебная фраза не документ: в ней нет полей."""
        self.assertEqual(pdoc.count_document_fields(self.OLD_SEND), 0)
        self.assertFalse(pdoc.looks_like_document(self.OLD_SEND))
        self.assertEqual(pdoc.extract_document_block(self.OLD_SEND), "")
        self.assertIsNone(pdoc.parse_ai_answer(self.OLD_SEND))

    def test_real_answer_is_a_document(self):
        self.assertGreaterEqual(pdoc.count_document_fields(self.ANSWER), 8)
        self.assertTrue(pdoc.looks_like_document(self.ANSWER))
        doc = pdoc.parse_ai_answer(self.ANSWER)
        self.assertIsNotNone(doc)
        self.assertEqual(doc.get("subject_name"), "Aliya Nomad")
        self.assertEqual(doc.get("penalty"), "строгий выговор")
        self.assertEqual(len(doc.points), 2)

    def test_send_message_has_no_literal_markers(self):
        src = Path("main.py").read_text(encoding="utf-8")
        start = src.index("def _auto_send_document_request")
        text_at = src.index("text = (", start)
        # литерал сообщения: от `text = (` до конца этого вызова,
        # комментарии (строки с «#») в него не входят
        end = src.index("self._set_status", text_at)
        literal = "\n".join(line for line in src[text_at:end].splitlines()
                            if not line.strip().startswith("#"))
        for marker in ("===ПОСТАНОВЛЕНИЕ", "===КОНЕЦ ПОСТАНОВЛЕНИЯ", "===КОНЕЦ ОТВЕТА"):
            self.assertNotIn(marker, literal)

    def test_client_decides_by_fields_not_by_marker(self):
        """Решение о готовности ответа — по полям; ждём дальше, если их нет."""
        src = Path("main.py").read_text(encoding="utf-8")
        block = src[src.index("def _apply_document_answer"):]
        block = block[:block.index("def _restart_after_document")]
        self.assertIn("looks_like_document", block)
        # проверка полей идёт ДО остановки таймера
        self.assertLess(block.index("looks_like_document"),
                        block.index("_doc_watch_timer.stop()"))
        self.assertIn("answer_without_fields", block)

    def test_word_request_still_works(self):
        """Ответ без маркеров, но с полями — принимается (маркер не обязателен)."""
        no_markers = self.ANSWER.replace("===ПОСТАНОВЛЕНИЕ===", "") \
                                 .replace("===КОНЕЦ ПОСТАНОВЛЕНИЯ===", "") \
                                 .replace("===КОНЕЦ ОТВЕТА===", "")
        self.assertTrue(pdoc.looks_like_document(no_markers))
        self.assertIsNotNone(pdoc.parse_ai_answer(no_markers))

    def test_unwritable_folder_falls_back_to_program_dir(self):
        import config
        folder = config.postanovlenie_dir({"postanovlenie_dir": "/proc/nope/x"})
        self.assertEqual(folder.name, "postanovleniya")
        self.assertEqual(folder.parent, Path(config.__file__).resolve().parent)


class V33FormatTests(unittest.TestCase):
    """v33: выравнивание по ширине, широкое основание, взыскание по тяжести."""

    def test_no_evidence_line_in_document(self):
        doc = pdoc.Postanovlenie(doc_number="LSCSD-IAD-1",
                                 situation="Нарушение формы одежды.",
                                 evidence="не представлены")
        text = doc.to_document_txt()
        self.assertNotIn("Доказательства", text)
        # старые файлы читаются без ошибки
        self.assertEqual(doc.get("evidence"), "не представлены")
        self.assertIsNotNone(doc.to_dict()["evidence"])

    def test_evidence_not_printed_by_renderer(self):
        src = Path("doc_render.py").read_text(encoding="utf-8")
        block = src[src.index("facts = []"):]
        block = block[:block.index("if facts:")]
        code = "\n".join(line for line in block.splitlines()
                         if not line.strip().startswith("#"))
        self.assertNotIn("Доказательства", code)

    def test_basis_includes_other_acts(self):
        doc = pdoc.Postanovlenie(
            violation_act="Устав Los Santos County Sheriff Department",
            violation_clause="пункт 8.11",
            legal_basis="ст. 15.1 УК SA; ст. 12 УПК SA")
        basis = doc.basis_text()
        self.assertIn("Устав", basis)
        self.assertIn("ст. 15.1 УК SA", basis)
        self.assertIn("ст. 12 УПК SA", basis)

    def test_basis_is_printed_in_document(self):
        doc = pdoc.Postanovlenie(violation_act="Устав LSCSD",
                                 violation_clause="пункт 8.11",
                                 legal_basis="ст. 15.1 УК SA")
        self.assertIn("ст. 15.1 УК SA", doc.to_document_txt())

    def test_legal_basis_parses_from_ai_answer(self):
        doc = pdoc.parse_ai_answer(
            "===ПОСТАНОВЛЕНИЕ===\n"
            "Взыскание: строгий выговор\n"
            "Обстоятельства: Оскорбление руководителя.\n"
            "Нормативное основание: ст. 15.1 УК SA; ст. 12 УПК SA\n"
            "Постановляющая часть:\n1. Наложить взыскание.\n")
        self.assertIn("ст. 15.1 УК SA", doc.get("legal_basis"))

    def test_penalty_follows_severity(self):
        cases = {
            "опоздал на построение впервые": "предупреждение",
            "нарушил дресс-код, не выполнил приказ": "устный выговор",
            "оскорбил шерифа, грубое нарушение субординации": "строгий выговор",
            "не прошёл аттестацию, несоответствие квалификации": "переаттестация",
            "превышение полномочий, халатность": "понижение",
            "тяжкое преступление, получение взятки": "увольнение",
        }
        for text, expected in cases.items():
            self.assertEqual(pdoc.suggest_penalty(text), expected, text)

    def test_all_penalties_stay_in_charter_list(self):
        for provided in pdoc.PENALTIES:
            self.assertIn(provided, pdoc.PENALTY_GENITIVE)

    def test_justified_text_is_wider_than_left_aligned(self):
        """Проверка выравнивания по ширине на реальном абзаце бланка."""
        import doc_render
        from PIL import Image, ImageDraw
        font = doc_render.DocAssets().fonts.get(28, "body")
        text = ("Я, сотрудник Los Santos County Sheriff Department Marcus Nomad, "
                "занимающий должность Куратор отдела IAD (Assistent sheriff), в рамках "
                "своих полномочий, предоставленных мне внутренним уставом и иными "
                "нормативно-правовыми актами департамента, настоящим постановил:")
        width, line_h = 900, 44

        probe = Image.new("RGB", (1000, 100), "white")
        lines = doc_render._wrap(ImageDraw.Draw(probe), text, font, width)

        # 1) без выравнивания строки короче ширины абзаца («лесенка»)
        left = Image.new("RGB", (1000, 400), "white")
        left_draw = ImageDraw.Draw(left)
        for index, line in enumerate(lines):
            left_draw.text((20, 20 + index * line_h), line, font=font, fill=(0, 0, 0))
        left_last = [max(x for x in range(20, 20 + width + 1)
                         if left.getpixel((x, 20 + index * line_h + 20)) != (255, 255, 255))
                     for index in range(len(lines) - 1)]

        # 2) с выравниванием каждая строка доходит почти до правого края
        just = Image.new("RGB", (1000, 400), "white")
        doc_render.draw_justified(ImageDraw.Draw(just), text, font, 20, 20, width, line_h)
        just_last = [max(x for x in range(20, 20 + width + 1)
                         if just.getpixel((x, 20 + index * line_h + 20)) != (255, 255, 255))
                     for index in range(len(lines) - 1)]

        self.assertTrue(left_last and just_last)
        for left_x, just_x in zip(left_last, just_last):
            self.assertGreater(just_x, left_x)                    # стало шире
            self.assertGreater(just_x, 20 + width * 0.96)         # до края абзаца


class V34LegalCheckTests(unittest.TestCase):
    """v34: проверка постановления по уставу и уменьшенная печать.

    Замечания из анализа: взыскание вне вилки устава, процессуальный кодекс
    как основание взыскания, чёрный список без п. 4.5, ограничения по
    должности (п. 4.4), печать налезала на текст.
    """

    @classmethod
    def setUpClass(cls):
        cls.reg = pdoc.Regulation(
            Path("data/reestr/Устав Los Santos County Sheriff Department.json"))

    def _doc(self, **kw):
        base = dict(doc_number="LSCSD-IAD-1", author_name="Marcus Nomad",
                    author_rank="Куратор отдела IAD (Assistent sheriff)",
                    subject_name="Aliya Nomad", subject_id="131485",
                    violation_act="Устав Los Santos County Sheriff Department",
                    situation="Нарушение порядка применения силы.")
        base.update(kw)
        return pdoc.Postanovlenie(**base)

    # ── вилка взысканий из устава ───────────────────────────────────────────
    def test_allowed_penalties_are_read_from_the_charter(self):
        self.assertEqual(pdoc.allowed_penalties_for("пункт 8.1", self.reg),
                         ["строгий выговор", "понижение", "увольнение"])
        self.assertEqual(pdoc.allowed_penalties_for("8.11", self.reg),
                         ["строгий выговор", "понижение"])
        self.assertEqual(
            pdoc.allowed_penalties_for("пункты 5.1 и 5.2", self.reg),
            ["предупреждение", "устный выговор", "строгий выговор",
             "переаттестация", "понижение"])

    def test_penalty_outside_the_range_is_reported(self):
        # за 5.1 и 5.2 увольнение допустимо, но за 5.2 — нет
        doc = self._doc(violation_clause="пункты 5.1 и 5.2", penalty="увольнение")
        issues = pdoc.check_document(doc, self.reg)
        self.assertTrue(any("не предусмотрено" in issue for issue in issues), issues)

    def test_valid_penalty_has_no_such_issue(self):
        doc = self._doc(violation_clause="пункт 8.1", penalty="увольнение")
        issues = pdoc.check_document(doc, self.reg)
        self.assertFalse(any("не предусмотрено" in issue for issue in issues), issues)

    # ── процессуальный кодекс ───────────────────────────────────────────────
    def test_procedural_code_is_not_a_basis(self):
        doc = self._doc(
            violation_clause="пункт 8.1", penalty="понижение",
            legal_basis="часть 1 статьи 16 Процессуального кодекса")
        issues = pdoc.check_document(doc, self.reg)
        self.assertTrue(any("Процессуальный кодекс" in issue for issue in issues), issues)
        self.assertEqual(
            pdoc.procedural_parts(doc.get("legal_basis")),
            ["часть 1 статьи 16 Процессуального кодекса"])

    def test_criminal_code_is_not_flagged_as_procedural(self):
        doc = self._doc(violation_clause="пункт 8.1", penalty="понижение",
                        legal_basis="ст. 6.1 Уголовного кодекса")
        issues = pdoc.check_document(doc, self.reg)
        self.assertFalse(any("Процессуальный" in issue for issue in issues), issues)

    # ── чёрный список и полномочия ─────────────────────────────────────────
    def test_black_list_needs_clause_4_5(self):
        doc = self._doc(violation_clause="пункт 8.1", penalty="увольнение",
                        extra="занесение в Черный список департамента")
        issues = pdoc.check_document(doc, self.reg)
        self.assertTrue(any("чёрный список" in issue.lower() for issue in issues), issues)
        doc2 = self._doc(violation_clause="пункт 8.1", penalty="увольнение",
                         legal_basis="п. 4.5 Устава LSCSD",
                         extra="занесение в Черный список департамента")
        issues2 = pdoc.check_document(doc2, self.reg)
        self.assertFalse(any("чёрный список" in issue.lower() for issue in issues2), issues2)

    def test_demotion_limit_by_rank(self):
        doc = self._doc(violation_clause="пункт 8.1", penalty="понижение",
                        author_rank="Куратор отдела IAD (Assistent sheriff)")
        issues = pdoc.check_document(doc, self.reg)
        self.assertTrue(any("п. 4.4" in issue for issue in issues), issues)
        sheriff = self._doc(violation_clause="пункт 8.1", penalty="понижение",
                            author_rank="Заместитель Шерифа")
        self.assertFalse(any("п. 4.4" in issue
                             for issue in pdoc.check_document(sheriff, self.reg)))

    def test_unknown_clause_is_reported(self):
        doc = self._doc(violation_clause="пункт 99.9", penalty="строгий выговор")
        issues = pdoc.check_document(doc, self.reg)
        self.assertTrue(any("99.9" in issue for issue in issues), issues)

    def test_clean_document_has_no_issues(self):
        doc = self._doc(violation_clause="пункт 8.11",
                        penalty="строгий выговор",
                        legal_basis="ст. 6.1 Уголовного кодекса")
        self.assertEqual(pdoc.check_document(doc, self.reg), [])

    # ── оформление ──────────────────────────────────────────────────────────
    def test_stamp_is_half_size(self):
        src = Path("doc_render.py").read_text(encoding="utf-8")
        self.assertIn("stamp_size = 330 // 2", src)
        self.assertNotIn("stamp_size = 330\n", src)

    def test_stamp_does_not_overlap_text(self):
        """Верх печати не поднимается выше последней строки текста."""
        import doc_render
        from PIL import Image
        doc = self._doc(violation_clause="пункт 8.11", penalty="строгий выговор",
                        situation="Короткий текст.")
        pages = doc_render.render_document(doc)
        self.assertTrue(pages)
        text_bottom = doc._text_bottom
        stamp_size = 330 // 2
        stamp_top = doc_render.PAGE_H - doc_render.MARGIN_BOTTOM - stamp_size - 30
        stamp_top = max(stamp_top, text_bottom + 8)
        self.assertGreaterEqual(stamp_top, text_bottom)
        self.assertLess(stamp_top + stamp_size, doc_render.PAGE_H)

    def test_client_saves_check_report(self):
        src = Path("main.py").read_text(encoding="utf-8")
        self.assertIn("check_document", src)
        self.assertIn("Проверка.txt", src)
        # проверка — до сохранения PNG не обязана, но должна быть в конвейере
        block = src[src.index("def _apply_document_answer"):]
        block = block[:block.index("def _restart_after_document")]
        self.assertLess(block.index("save_pages"), block.index("check_document"))

    def test_form_shows_allowed_penalties(self):
        src = Path("postanovlenie_ui.py").read_text(encoding="utf-8")
        self.assertIn("allowed_penalties_for", src)
        self.assertIn("Устав за эти пункты допускает", src)


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


class V36OOCTests(unittest.TestCase):
    """v36: внеигровые термины (IC/OOC) не должны попадать в документ."""

    def test_ic_terms_are_found(self):
        self.assertIn("IC", pdoc.ooc_terms("без весомых IC причин"))
        self.assertIn("OOC", pdoc.ooc_terms("это OOC информация"))
        self.assertTrue(pdoc.ooc_terms("по правилам сервера"))

    def test_normal_words_are_not_flagged(self):
        for text in ("сотрудник действовал законно",
                     "в соответствии с действующим законодательством",
                     "проведена служебная проверка"):
            self.assertEqual(pdoc.ooc_terms(text), [], text)

    def test_sanitize_rewrites_ic_phrases(self):
        self.assertEqual(pdoc.sanitize_ooc("при отсутствии на то весомых IC причин"),
                         "в отсутствие законных и фактических оснований")
        self.assertEqual(pdoc.sanitize_ooc("в соответствии с IC информацией"),
                         "на основании материалов проверки")
        self.assertIn("в отсутствие законных и фактических оснований",
                      pdoc.sanitize_ooc("забрал лицензию без весомых IC-причин"))

    def test_check_document_reports_ooc(self):
        doc = pdoc.Postanovlenie(
            doc_number="LSCSD-IAD-1", author_name="Marcus Nomad",
            author_rank="Куратор IAD", subject_name="Aliya Nomad", subject_id="131485",
            situation="изъял лицензию при отсутствии на то весомых IC причин")
        issues = pdoc.check_ooc(doc)
        self.assertTrue(any("IC" in issue for issue in issues), issues)

    def test_clean_document_has_no_ooc_issues(self):
        doc = pdoc.Postanovlenie(
            doc_number="LSCSD-IAD-1", author_name="Marcus Nomad",
            subject_name="Aliya Nomad", subject_id="131485",
            situation="изъял лицензию в отсутствие законных и фактических оснований")
        self.assertEqual(pdoc.check_ooc(doc), [])


class V36ResolutionParsingTests(unittest.TestCase):
    """v36: «Постановляющая часть: …» больше не уезжает в обстоятельства."""

    ANSWER = (
        "===ПОСТАНОВЛЕНИЕ===\n"
        "Номер: LSCSD-IAD-1\n"
        "Дата: 06.11.2026\n"
        "В отношении: Aliya Nomad\n"
        "Удостоверение: 131485\n"
        "Обстоятельства: сотрудник изъял лицензию без оснований.\n"
        "Постановляющая часть: 1. Применить взыскание в виде строгого выговора. "
        "2. Внести запись в личное дело.\n"
        "===КОНЕЦ ПОСТАНОВЛЕНИЯ===\n"
        "===КОНЕЦ ОТВЕТА===\n"
    )

    def test_points_are_taken_from_the_single_line(self):
        doc = pdoc.parse_ai_answer(self.ANSWER)
        self.assertEqual(len(doc.points), 2, doc.points)
        self.assertIn("строгого выговора", doc.points[0])
        self.assertIn("личное дело", doc.points[1])

    def test_situation_does_not_get_the_resolution(self):
        doc = pdoc.parse_ai_answer(self.ANSWER)
        self.assertNotIn("Применить взыскание", doc.situation)


class V36ProkurorTests(unittest.TestCase):
    """v36: постановление Прокурора — бланк, разбор, проверка."""

    def setUp(self):
        import postanovlenie_prokuror_data as g
        self.g = g
        self.doc = g.PostanovlenieProkurora(
            doc_number="936", doc_date="06.11.2026",
            author_name="Mekyla Barbosov", author_rank="Прокурор", author_id="15902",
            applicant_name="Lordik Claw", applicant_id="83461", appeal_date="06.11.2026")

    def test_preamble_is_like_the_examples(self):
        text = self.doc.preamble()
        self.assertIn("Я, Прокурор штата Сан-Андреас Mekyla Barbosov", text)
        self.assertIn("удостоверение № 15902", text)
        self.assertIn("статьи 36 Конституции", text)
        self.assertIn("статьи 8 Закона", text)
        self.assertIn("статьями 11, 12, 13 и 14 Регламента", text)
        self.assertIn("рассмотрев обращение гражданина Lordik Claw от 06.11.2026", text)

    def test_number_line_and_title(self):
        self.assertEqual(self.doc.number_line(), "ПОСТАНОВЛЕНИЕ № 936")
        self.assertEqual(self.doc.title(), self.g.GOV_DOC_TYPES[0])

    def test_accept_points_include_deadlines_and_appeal(self):
        points = " ".join(self.doc.decision_points())
        self.assertIn("Принять к рассмотрению обращение", points)
        self.assertIn("72 час", points)
        self.assertIn("обжаловано", points)

    def test_refusal_points_contain_basis(self):
        self.doc.doc_type = "Об отказе в принятии обращения к рассмотрению"
        self.doc.legal_basis = "пункт 1 статьи 16 Регламента"
        points = " ".join(self.doc.decision_points())
        self.assertIn("Отказать в принятии обращения", points)
        self.assertIn("статьи 16 Регламента", points)
        self.assertIn("72 часа", points)

    def test_check_flags_period_over_72_hours(self):
        self.doc.points = ["Рассмотреть обращение в срок, не превышающий 120 часов."]
        issues = self.g.check_gov_document(self.doc)
        self.assertTrue(any("120" in issue for issue in issues), issues)

    def test_check_flags_refusal_without_basis(self):
        self.doc.doc_type = "Об отказе в принятии обращения к рассмотрению"
        self.doc.points = ["Отказать в принятии обращения.",
                           "Может быть обжаловано в течение 72 часов."]
        issues = self.g.check_gov_document(self.doc)
        self.assertTrue(any("статьи 16" in issue for issue in issues), issues)

    def test_clean_document_passes(self):
        for doc_type in self.g.GOV_DOC_TYPES:
            self.doc.doc_type = doc_type
            self.doc.doc_stage = self.g.gov_stage(doc_type)
            self.doc.points = []
            # v39: итоговое постановление о привлечении к ответственности
            # обязано опираться на норму — иначе санкция без права. Для
            # «чистого» документа норму и указываем.
            self.doc.violation_clause = "статья 28 Этического кодекса"
            self.doc.legal_basis = "статья 23 Регламента подачи обращений"
            issues = self.g.check_gov_document(self.doc)
            self.assertEqual(issues, [], f"{doc_type}: {issues}")

    def test_answer_is_parsed(self):
        answer = (
            "===ПОСТАНОВЛЕНИЕ===\n"
            "Номер: AG-937\n"
            "Дата: 06.11.2026\n"
            "Вид документа: О привлечении к административной ответственности\n"
            "Заявитель: Marcus Nomad\n"
            "ID заявителя: 85028\n"
            "Дата обращения: 03.11.2026\n"
            "В отношении: Mikato Wane\n"
            "Пункт нарушения: статьи 8, 10, 11 Этического кодекса\n"
            "УСТАНОВИЛ:\n"
            "03.11.2026 сотрудники не пресекли нарушение.\n"
            "ПОСТАНОВИЛ:\n"
            "1. Признать виновным.\n"
            "2. Назначить штраф 30 000 $.\n"
            "===КОНЕЦ ПОСТАНОВЛЕНИЯ===\n"
            "===КОНЕЦ ОТВЕТА===\n")
        doc = self.g.parse_gov_answer(answer)
        self.assertIsNotNone(doc)
        self.assertEqual(doc.get("doc_number"), "AG-937")
        self.assertEqual(doc.get("applicant_name"), "Marcus Nomad")
        self.assertEqual(doc.get("applicant_id"), "85028")
        self.assertEqual(len(doc.points), 2, doc.points)
        # «УСТАНОВИЛ» — отдельный раздел, а не «Обстоятельства» (v36)
        self.assertIn("не пресекли", doc.established)

    def test_document_txt_has_both_sections(self):
        self.doc.situation = "06.11.2026 сотрудник бездействовал."
        text = self.doc.to_document_txt()
        self.assertIn("УСТАНОВИЛ:", text)
        self.assertIn("ПОСТАНОВИЛ:", text)
        self.assertIn("OFFICE OF THE ATTORNEY GENERAL", text)
        self.assertIn("ПОСТАНОВЛЕНИЕ № 936", text)

    def test_gov_assets_use_prosecutor_emblem(self):
        from pathlib import Path as _P
        assets = self.g.gov_assets(_P("."))
        self.assertTrue(str(assets.coat).endswith("gerb_gov.png"), assets.coat)
        self.assertTrue(str(assets.stamp).endswith("stamp_gov.png"), assets.stamp)

    def test_document_menu_offers_both(self):
        src = _P("main.py").read_text(encoding="utf-8")
        self.assertIn('QPushButton("Постановление IAD")', src)
        self.assertIn('QPushButton("Постановление Прокурора")', src)
        self.assertIn("_open_postanovlenie_prokurora", src)
        self.assertIn("PostanovlenieProkuroraDialog", src)

    def test_template_has_no_extra_document_rows(self):
        """v37: в «Шаблоне» больше нет отдельных реквизитов для бланков."""
        import config
        src = _P("main.py").read_text(encoding="utf-8")
        self.assertIn("EXTRA_FIELDS = []", src)
        for legacy in ("RankTitle", "PositionFull", "Authority", "GovRank", "GovID"):
            self.assertNotIn(f'("{legacy}", "', src)
        # Ключи читаются из старых конфигов, но в окне не показываются.
        for legacy in ("RankTitle", "PositionFull", "Authority", "City",
                       "GovRank", "GovID"):
            self.assertIn(legacy, config.EXTRA_TEMPLATE_FIELDS)

    def test_template_defaults_come_from_character_fields(self):
        """Звание=Должность, ведомство=Фракция, удостоверение=ID, город фикс."""
        import config
        cfg = {"template": {"Name": "Marcus Nomad", "ID": "190948",
                            "Fraction": "LSCSD", "Rang": "Major",
                            "Department": "iad",
                            "JobTitle": "Куратор отдела IAD (Assistent sheriff)"}}
        defaults = config.document_defaults(cfg)
        self.assertEqual(defaults["author_name"], "Marcus Nomad")
        self.assertEqual(defaults["author_id"], "190948")
        self.assertEqual(defaults["vedomstvo"], "LSCSD")
        self.assertEqual(defaults["division"], "INTERNAL AFFAIRS DIVISION")
        self.assertEqual(defaults["author_rank"],
                         "Куратор отдела IAD (Assistent sheriff)")
        self.assertEqual(defaults["position"],
                         "Куратор отдела IAD (Assistent sheriff)")
        self.assertEqual(defaults["city"], "г. Лос-Сантос")


class V36ProkurorParsingTests(unittest.TestCase):
    """v36: разбор ответа ИИ по постановлению Прокурора без типовых ошибок."""

    ANSWER = (
        "===ПОСТАНОВЛЕНИЕ===\n"
        "Номер: AG-937\n"
        "Дата: 06.11.2026\n"
        "Постановление вынес: Mekyla Barbosov\n"
        "Должность: Прокурор\n"
        "Удостоверение прокурора: 15902\n"
        "Заявитель: Marcus Nomad\n"
        "ID заявителя: 85028\n"
        "Дата обращения: 03.11.2026\n"
        "Вид документа: О привлечении к административной ответственности\n"
        "В отношении: Mikato Wane\n"
        "Удостоверение: 32229\n"
        "Нарушенный акт: Этический кодекс штата San-Andreas\n"
        "Пункт нарушения: статьи 8, 10, 11\n"
        "Обстоятельства: сотрудники не пресекли нарушение в холле ПД.\n"
        "УСТАНОВИЛ:\n"
        "03 ноября 2026 года в канцелярию поступило обращение гражданина Marcus Nomad.\n"
        "ПОСТАНОВИЛ:\n"
        "1. Признать виновным.\n"
        "===КОНЕЦ ПОСТАНОВЛЕНИЯ===\n"
        "===КОНЕЦ ОТВЕТА===\n")

    def setUp(self):
        import postanovlenie_prokuror_data as g
        self.g = g
        self.doc = g.parse_gov_answer(self.ANSWER)

    def test_certificate_of_prosecutor_is_not_overwritten_by_offender(self):
        self.assertEqual(self.doc.get("author_id"), "15902")
        self.assertEqual(self.doc.get("subject_id"), "32229")

    def test_established_section_is_separate_from_circumstances(self):
        self.assertIn("канцелярию", self.doc.established)
        self.assertNotIn("канцелярию", self.doc.situation)

    def test_facts_do_not_duplicate_established(self):
        text = self.doc.facts_text()
        self.assertEqual(text.count("в канцелярию поступило обращение"), 1)

    def test_basis_contains_act_name(self):
        basis = self.doc.basis_text()
        self.assertIn("статьи 8, 10, 11", basis)
        self.assertIn("Этический кодекс", basis)

    def test_evidence_not_repeated_when_codes_already_in_text(self):
        self.doc.established = ("Изложенные обстоятельства подтверждаются "
                                "видеозаписью с тайм-кодами 05:01, 08:23 и 08:50.")
        self.doc.evidence = "видеозапись, тайм-коды 05:01, 08:23"
        self.assertEqual(self.doc.facts_text().count("В подтверждение"), 0)


# --------------------------------------------------------------------------- #
#  v37: дефекты, найденные в реальных выгрузках папки «Постановление/»
# --------------------------------------------------------------------------- #

#: Ответ ИИ из выгрузки LSCSD-IAD-1 (сокращённо, структура сохранена).
AI_ANSWER_V37_IAD = (
    "Перейти к основному контенту\n"
    "Справка по использованию специальных возможностей\n"
    "Memphis.pdf\n"
    "PDF\n"
    "Шаблон.txt\n"
    "File\n"
    "Заявка (отправлено в чат).txt\n"
    "File\n"
    "Чат в режиме ИИ: Сформировано постановление, файл заявки приложен: "
    "«Заявка (отправлено в чат).txt». Работай ТОЛЬКО с текстом заявки. "
    "НИ В КАКОМ РАЗДЕЛЕ не используй внеигровые термины: «IC», «OOC».\n"
    "Вы отправили 3 файлов и сделали запрос \"Сформировано постановление\"\n"
    "Сформировано постановление, файл заявки приложен: «Заявка (отправлено в чат).txt».\n"
    "Ответ в режиме ИИ, исходные файлы: 3 файлов, исходный запрос: \"...\"\n"
    "ПОСТАНОВЛЕНИЕ\n"
    "Ведомство: LOS SANTOS COUNTY SHERIFF'S DEPARTMENT\n"
    "Подразделение: INTERNAL AFFAIRS DIVISION\n"
    "Вид документа: Постановление о выдаче дисциплинарного взыскания\n"
    "Номер: LSCSD-IAD-1\n"
    "Дата: 07.11.2026\n"
    "Постановление вынес: Marcus Nomad\n"
    "В отношении: Maison Nomad\n"
    "Удостоверение: 190948\n"
    "Нарушенный акт: Устав Los Santos County Sheriff Department\n"
    "Пункт нарушения: 8.5\n"
    "Нормативное основание: Этический кодекс штата San-Andreas\n"
    "Взыскание: устный выговор\n"
    "Обстоятельства: Сотрудник допустил нарушение дресс-кода при исполнении "
    "служебных обязанностей.\n"
    "Постановляющая часть:\n"
    "За нарушение пункта 8.5 Устава применить устный выговор.\n"
    "Установить срок отработки 48 часов.\n"
    "КОНЕЦ ПОСТАНОВЛЕНИЯ\n"
    "===КОНЕЦ ОТВЕТА===\n"
    "Перейти к предыдущему запросу\n"
    "Memphis.pdf\n"
)

#: Ответ ИИ из выгрузки AG-2 (структура сохранена).
AI_ANSWER_V37_GOV = """STATE OF SAN-ANDREAS
OFFICE OF THE ATTORNEY GENERAL
ПОСТАНОВЛЕНИЕ № AG-2
О принятии обращения к собственному производству
г. Лос-Сантос, 07.11.2026
Я, Прокурор штата Сан-Андреас Jonathan Raine, удостоверение № 198632, рассмотрев обращение гражданина Kirito Gold от 02.11.2026,
УСТАНОВИЛ:
Место события: в ходе служебной деятельности на территории штата Сан-Андреас. В Офис Генерального прокурора поступило обращение гражданина Kirito Gold.
ПОСТАНОВИЛ:
1. Принять обращение к производству.
2. Возбудить прокурорскую проверку.
3. Настоящее постановление вступает в законную силу с момента подписания.
4. Настоящее решение может быть обжаловано вышестоящему прокурору в срок, не превышающий 72 часа.
5. Прокурор штата Сан-Андреас
6. J. Raine
7. Удостоверение № 198632
Основание: по обстоятельствам, указанным в заявке
"""


class V37ChatLeakTests(unittest.TestCase):
    """Служебный текст веб-чата не попадает в документ."""

    def setUp(self):
        self.doc = pdoc.parse_ai_answer(AI_ANSWER_V37_IAD)

    def test_chat_instructions_are_not_circumstances(self):
        self.assertNotIn("IC", self.doc.situation)
        self.assertNotIn("OOC", self.doc.situation)
        self.assertNotIn("Сформировано постановление", self.doc.situation)

    def test_chat_junk_lines_are_dropped(self):
        for junk in ("PDF", "File", "Заявка (отправлено в чат).txt",
                     "Перейти к предыдущему запросу"):
            self.assertNotIn(junk, self.doc.situation)

    def test_resolution_heading_opens_points(self):
        """«Постановляющая часть:» раньше считалась заголовком и терялась."""
        self.assertEqual(len(self.doc.points), 2)
        self.assertIn("устный выговор", self.doc.points[0])
        # резолюция не должна утечь в обстоятельства
        self.assertNotIn("Установить срок отработки", self.doc.situation)
        self.assertNotIn("Постановляющая часть", self.doc.situation)

    def test_no_ooc_remarks_after_scrub(self):
        self.assertEqual(pdoc.check_ooc(self.doc), [])


class V37BareClauseTests(unittest.TestCase):
    """Голый номер пункта получает падеж и название акта."""

    def _doc(self, clause):
        return pdoc.Postanovlenie(
            doc_number="X", subject_name="Ivan Petrov", penalty="устный выговор",
            violation_act="Устав Los Santos County Sheriff Department",
            violation_clause=clause)

    def test_bare_number_becomes_punkt(self):
        point = self._doc("8.5").decision_points()[0]
        self.assertIn("нарушением пункта 8.5 Устава Los Santos County", point)

    def test_act_is_in_genitive(self):
        self.assertNotIn("Устав Los Santos", self._doc("8.5").decision_points()[0])
        self.assertIn("Устава Los Santos", self._doc("8.5").basis_text())

    def test_vague_clause_is_not_a_norm(self):
        doc = self._doc("по обстоятельствам, указанным в заявке")
        self.assertNotIn("по обстоятельствам", doc.decision_points()[0])
        self.assertNotIn("по обстоятельствам", doc.basis_text())
        issues = " ".join(pdoc.check_document(doc))
        self.assertIn("не указан", issues)

    def test_penalty_with_extra_words_is_flagged(self):
        doc = self._doc("8.5")
        doc.points = ["Назначить устный выговор с занесением в личное дело."]
        issues = " ".join(pdoc.check_document(doc))
        self.assertIn("занесением в личное дело", issues)

    def test_ethic_code_is_clause_8_6(self):
        doc = self._doc("8.5")
        doc.legal_basis = "Этический кодекс штата San-Andreas"
        issues = " ".join(pdoc.check_document(doc))
        self.assertIn("8.6", issues)


class V37ProkurorTests(unittest.TestCase):
    """Дефекты выгрузки AG-2."""

    def setUp(self):
        import postanovlenie_prokuror_data as g
        self.g = g
        self.doc = g.parse_gov_answer(AI_ANSWER_V37_GOV)

    def test_signature_points_are_not_resolution(self):
        points = self.doc.decision_points()
        self.assertEqual(len(points), 4)
        joined = " ".join(points)
        self.assertNotIn("J. Raine", joined)
        self.assertNotIn("Удостоверение", joined)

    def test_place_placeholder_dropped(self):
        self.assertNotIn("в ходе служебной деятельности",
                         self.doc.facts_text())
        self.assertNotIn("Место события:", self.doc.facts_text())

    def test_placeholder_basis_dropped(self):
        self.assertNotIn("по обстоятельствам", self.doc.basis_text())
        self.assertNotIn("по обстоятельствам", self.doc.facts_text())

    def test_author_found_in_preamble(self):
        self.assertEqual(self.doc.get("author_name"), "Jonathan Raine")
        self.assertEqual(self.doc.get("author_id"), "198632")

    def test_appeal_without_norm_is_flagged(self):
        """В AG-2 обжалование было без ссылки на статьи 27–28 Регламента."""
        self.doc.applicant_name = "Kirito Gold"
        issues = " ".join(self.g.check_gov_document(self.doc))
        self.assertIn("27–28", issues)

    def test_no_remarks_when_appeal_has_norm(self):
        self.doc.applicant_name = "Kirito Gold"
        self.doc.points = [
            p for p in self.doc.decision_points() if "обжаловано" not in p]
        self.doc.points.append(self.g.PostanovlenieProkurora().appeal_point())
        self.assertEqual(self.g.check_gov_document(self.doc), [])

    def test_appeal_form_is_parsed_not_pasted(self):
        raw = (
            "От: Kirito Gold 178393\n"
            "1. Данные о заявителе: Kirito Gold 178393\n"
            "2. Предмет обращения: заявление о преступлении должностного лица\n"
            "3. Сведения о нарушителе: Todd Spencer 181956: FIB\n"
            "4. Дата и время события: 31.10.2026 19:13 - 19:41\n"
            "5. Подробное описание ситуации: \n"
            "6. Подпись и расшифровка заявителя: G.Kirito\n")
        self.assertTrue(self.g.looks_like_appeal_form(raw))
        doc = self.g.PostanovlenieProkurora(situation=raw)
        self.assertNotIn("Данные о заявителе", doc.facts_text())
        issues = " ".join(self.g.check_gov_document(doc))
        self.assertIn("Подробное описание ситуации", issues)


class V37AuthorTests(unittest.TestCase):
    """Поле «Постановление вынес: …» не должно съедаться маркером."""

    def test_author_survives_marker_check(self):
        text = ("===ПОСТАНОВЛЕНИЕ===\n"
                "Постановление вынес: Marcus Nomad\n"
                "Должность: Куратор отдела IAD\n"
                "Обстоятельства: Сотрудник нарушил форму одежды.\n"
                "===КОНЕЦ ОТВЕТА===\n")
        doc = pdoc.parse_ai_answer(text)
        self.assertEqual(doc.get("author_name"), "Marcus Nomad")
        self.assertIn("Marcus Nomad", doc.to_document_txt())


# --------------------------------------------------------------------------- #
#  v37 (вторая итерация): одно поле «Обращение», шаблон без лишних строк
# --------------------------------------------------------------------------- #

#: Обращение гражданина ровно в той форме, в какой его присылают.
APPEAL_FORM = """От: гражданина штата Сан-Андреас Mark Skywalker
В: Уважаемый Офис Генерального Прокурора штата Сан-Андреас
1. Данные о заявителе: Mark Skywalker 152306
1.1 Данные о представителе заявителя:нету
2. Предмет обращения: заявление о преступлении, совершённом должностным лицом при исполнении либо в связи с исполнением служебных обязанностей
3. Сведения о нарушителе: Kyria Suspectzx 8332
4. Дата и время события: 27.10.2026 00:40
5. Подробное описание ситуации: Сотрудник открыл огонь без оснований.
Настоящим заверяю, что все показания являются подлинными.
К заявлению прилагаю:
6. Ксерокопия идентификационной карты: https://ibb.co/7xd2y12R
8. Доказательства правонарушения:
ТАЙМКОД
00:04 - потребовал остановиться
02:09 - начал стрелять
9. Копия договора о представлении интересов: нету
Дата подачи заявления: 27.10.2026
Подпись и расшифровка заявителя: Skywalker"""


class V37AppealFieldTests(unittest.TestCase):
    """Одно поле «Обращение» вместо полей заявителя и нарушителя."""

    def setUp(self):
        import postanovlenie_prokuror_data as g
        self.g = g
        self.doc = g.PostanovlenieProkurora(doc_number="AG-1",
                                            author_name="Jonathan Raine")
        self.doc.apply_appeal(APPEAL_FORM)

    def test_applicant_and_violator_are_parsed(self):
        self.assertEqual(self.doc.get("applicant_name"), "Mark Skywalker")
        self.assertEqual(self.doc.get("applicant_id"), "152306")
        self.assertEqual(self.doc.get("subject_name"), "Kyria Suspectzx")
        self.assertEqual(self.doc.get("subject_id"), "8332")
        self.assertEqual(self.doc.get("event_date"), "27.10.2026 00:40")
        self.assertIn("заявление о преступлении", self.doc.get("appeal_subject"))

    def test_description_has_no_form_tail(self):
        situation = self.doc.get("situation")
        self.assertIn("открыл огонь", situation)
        self.assertNotIn("Настоящим заверяю", situation)
        self.assertNotIn("К заявлению прилагаю", situation)

    def test_timcodes_are_kept(self):
        self.assertIn("02:09", self.doc.get("evidence"))

    def test_chat_text_has_appeal_whole_and_no_duplicate_fields(self):
        text = self.doc.to_chat_txt()
        self.assertIn("ОБРАЩЕНИЕ ГРАЖДАНИНА", text)
        self.assertIn("3. Сведения о нарушителе: Kyria Suspectzx 8332", text)
        # отдельные поля-дубли из заявки убраны
        self.assertNotIn("Заявитель: Mark", text)
        self.assertNotIn("В отношении: Kyria", text)
        self.assertNotIn("нет данных — определи сам", text)

    def test_city_is_always_los_santos(self):
        self.assertEqual(self.doc.get("city"), "г. Лос-Сантос")

    def test_form_has_single_appeal_field(self):
        src = _P("postanovlenie_prokuror_ui.py").read_text(encoding="utf-8")
        self.assertIn('self.texts["appeal_raw"]', src)
        for removed in ('self.cb_subject', 'self.fields["applicant_name"]',
                        'self.fields["subject_name"]',
                        'self.fields["event_place"]'):
            self.assertNotIn(removed, src)

    def test_chat_message_asks_to_read_the_appeal(self):
        src = _P("main.py").read_text(encoding="utf-8")
        self.assertIn("ОБРАЩЕНИЕ ГРАЖДАНИНА приведено в заявке целиком", src)


class V37SecondRunDefectTests(unittest.TestCase):
    """Дефекты второй выгрузки (папка «Постановление_1»)."""

    def test_resolution_has_no_chat_junk_points(self):
        import postanovlenie_prokuror_data as g
        answer = (
            "===ПОСТАНОВЛЕНИЕ===\nНомер: AG-2\n"
            "Я, Прокурор штата Сан-Андреас Jonathan Raine, удостоверение № 198632.\n"
            "УСТАНОВИЛ:\nОбращение поступило.\nПОСТАНОВИЛ:\n"
            "1. Принять обращение к производству.\n"
            "2. Продлить проверку на 72 часа.\n"
            "Заявка (отправлено в\n"
            "Ответ от режима ИИ готов.\n")
        doc = g.parse_gov_answer(answer)
        joined = " ".join(doc.points)
        self.assertNotIn("Заявка (отправлено", joined)
        self.assertNotIn("Ответ от режима ИИ", joined)

    def test_clause_with_act_name_is_shortened(self):
        self.assertEqual(
            pdoc.shorten_clause("Пункт 8.5 Устава LSCSD — нарушение дресс-кода"),
            "пункт 8.5 (нарушение дресс-кода)")
        self.assertEqual(pdoc.shorten_clause("пункт 8.5"), "пункт 8.5")

    def test_basis_has_no_double_act_name(self):
        doc = pdoc.Postanovlenie(
            violation_act="Устав Los Santos County Sheriff Department",
            violation_clause="Пункт 8.5 Устава LSCSD — нарушение дресс-кода")
        basis = doc.basis_text()
        self.assertEqual(basis.count("Устава Los Santos"), 1)

    def test_section_headings_are_centered_in_png(self):
        src = _P("doc_render.py").read_text(encoding="utf-8")
        self.assertIn('section("УСТАНОВИЛ:")', src)
        self.assertIn('section("ПОСТАНОВИЛ:")', src)
        self.assertIn('anchor="ma"', src)


class V37FullFormTests(unittest.TestCase):
    """Полный бланк ОГП как в выгрузке: продолжения названий пунктов."""

    FORM = """От: гражданина штата Сан-Андреас Mark Skywalker
В: Уважаемый Офис Генерального Прокурора штата Сан-Андреас
1. Данные о заявителе: Mark Skywalker 152306
1.1 Данные о представителе заявителя:нету
2. Предмет обращения: заявление о преступлении, совершённом должностным лицом
3. Сведения о нарушителе: Kyria Suspectzx 8332
4. Дата и время события: 27.10 00:40
5. Подробное описание ситуации: Сотрудник открыл огонь без оснований.
Настоящим заверяю, что все показания являются подлинными.
К заявлению прилагаю:
6. Ксерокопия идентификационной карты или других документов, удостоверяющих личность заявителя: https://ibb.co/7xd2y12R
7. Электронную почту и номер телефона: 42791428
8. Доказательства правонарушения:
ТАЙМКОД
00:04 - потребовал остановиться
02:09 - начал стрелять
9. Копия договора о представлении интересов: нету
10. Документ, подтверждающий полномочия представителя: нету
Дата подачи заявления: 27.10.2026
Подпись и расшифровка заявителя:Skywalker. [M.Skywalker]"""

    def setUp(self):
        self.parsed = gdata.parse_appeal_text(self.FORM)

    def test_id_copy_keeps_the_whole_point(self):
        value = self.parsed["id_copy"]
        self.assertTrue(value.startswith("Ксерокопия идентификационной карты"))
        self.assertIn("https://ibb.co/7xd2y12R", value)

    def test_other_points_are_parsed(self):
        self.assertEqual(self.parsed["contacts"], "42791428")
        self.assertEqual(self.parsed["contract"], "нету")
        self.assertIn("M.Skywalker", self.parsed["signature"])
        self.assertEqual(self.parsed["filed_date"], "27.10.2026")

    def test_description_stops_before_form_tail(self):
        self.assertNotIn("Настоящим заверяю", self.parsed["description"])

    def test_evidence_keeps_timecodes(self):
        self.assertIn("00:04", self.parsed["evidence"])
        self.assertIn("02:09", self.parsed["evidence"])


class V37EstablishedOrderTests(unittest.TestCase):
    """«УСТАНОВИЛ»: факты прокурора → правовая оценка ИИ, без дублей."""

    def _doc(self, prosecutor=""):
        doc = gdata.PostanovlenieProkurora(doc_number="AG-9",
                                           author_name="Jonathan Raine")
        doc.apply_appeal(V37FullFormTests.FORM)
        if prosecutor:
            doc.situation = prosecutor
            doc.prosecutor_facts = prosecutor
        doc.established = "27 октября 2026 года в Офис Генерального прокурора поступило обращение."
        return doc

    def test_appeal_retelling_is_not_repeated_when_ai_text_exists(self):
        facts = self._doc().facts_text()
        self.assertEqual(facts.count("открыл огонь без оснований"), 0)

    def test_prosecutor_facts_go_first(self):
        doc = self._doc("Просмотрев видеофиксацию, установлено: 02:09 — стрельба.")
        facts = doc.facts_text()
        self.assertLess(facts.index("Просмотрев видеофиксацию"),
                        facts.index("поступило обращение"))

    def test_prosecutor_facts_are_marked(self):
        doc = self._doc("Просмотрев видеофиксацию, установлено: 02:09 — стрельба.")
        self.assertTrue(doc._prosecutor_own_facts())
        self.assertFalse(self._doc()._prosecutor_own_facts())

    def test_appeal_retelling_is_used_when_ai_gave_nothing(self):
        doc = gdata.PostanovlenieProkurora(doc_number="AG-9")
        doc.apply_appeal(V37FullFormTests.FORM)
        self.assertIn("открыл огонь без оснований", doc.facts_text())


# --------------------------------------------------------------------------- #
#  v38: «История» с удалением + дефекты выгрузки «Постановление_3»
# --------------------------------------------------------------------------- #

class V38HistoryDeleteTests(unittest.TestCase):
    """«В «История» добавь возможность удалять «Постановления»»."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.history = pdoc.DocumentHistory(Path(self.tmp))
        self.saved = []
        for number in ("AG-1", "AG-2"):
            doc = pdoc.Postanovlenie(doc_number=number, author_name="Marcus Nomad",
                                     subject_name="Nello Deshine")
            self.saved.append(self.history.save(doc))

    def test_saved_documents_are_listed(self):
        self.assertEqual(len(self.history.items()), 2)
        self.assertEqual(len(self.history.labels()), 2)

    def test_delete_one_record(self):
        self.assertTrue(self.history.delete_index(0))
        self.assertEqual(len(self.history.items()), 1)
        remaining = [path.name for path in self.saved if path.exists()]
        self.assertEqual(len(remaining), 1)

    def test_delete_keeps_other_records_intact(self):
        path = self.history.path_at(1)
        self.history.delete_index(0)
        self.assertTrue(path.exists())

    def test_delete_out_of_range_returns_false(self):
        self.assertFalse(self.history.delete_index(99))

    def test_clear_history(self):
        self.assertEqual(self.history.delete_all(), 2)
        self.assertEqual(self.history.items(), [])

    def test_label_at_matches_list(self):
        self.assertEqual(self.history.label_at(0), self.history.labels()[0])

    def test_forms_have_delete_buttons(self):
        for name in ("postanovlenie_ui.py", "postanovlenie_prokuror_ui.py"):
            src = (Path(name)).read_text(encoding="utf-8")
            self.assertIn('QPushButton("Удалить")', src, name)
            self.assertIn('QPushButton("Очистить историю")', src, name)
            self.assertIn("def _delete_history", src, name)
            self.assertIn("def _clear_history", src, name)
            self.assertIn("history_delete_btn", src, name)


class V38ProkurorFormLayoutTests(unittest.TestCase):
    """«Обращение» и «Обстоятельства» не должны уезжать вправо."""

    def setUp(self):
        self.src = Path("postanovlenie_prokuror_ui.py").read_text(encoding="utf-8")

    def test_text_areas_wrap_by_words(self):
        self.assertIn("def _text_area", self.src)
        self.assertIn("LineWrapMode.WidgetWidth", self.src)
        self.assertIn("WrapAtWordBoundaryOrAnywhere", self.src)
        self.assertIn("ScrollBarAlwaysOff", self.src)

    def test_all_case_fields_use_wrapped_area(self):
        """v42: обращение и четыре части обстоятельств — с переносом строк."""
        self.assertIn('self.texts["appeal_raw"] = self._text_area()', self.src)
        for key in ("facts_video", "facts_for", "facts_against", "proposal",
                    "own_decision"):
            self.assertIn(f'self.texts["{key}"] = self._text_area(', self.src)

    def test_no_unbounded_plain_text_edits(self):
        """Все текстовые поля формы создаются через `_text_area` (с переносом)."""
        for line in self.src.splitlines():
            if "self.texts[" in line and "QPlainTextEdit()" in line:
                self.fail(f"поле без переноса строк: {line.strip()}")

    def test_columns_do_not_stretch(self):
        self.assertIn("columns.setStretch(1, 1)", self.src)
        self.assertIn("left.setMaximumWidth(", self.src)


class V38DocumentDefectTests(unittest.TestCase):
    """Ошибки итогового документа из выгрузки «Постановление_3»."""

    def test_doc_type_tail_is_removed_from_facts(self):
        doc = gdata.PostanovlenieProkurora(doc_number="AG-1")
        doc.situation = ("Заявитель сообщает о неправомерных действиях.\n"
                         "О принятии обращения к собственному производству")
        self.assertNotIn("к собственному производству", doc.facts_text())

    def test_doc_type_tail_is_removed_mid_paragraph(self):
        doc = gdata.PostanovlenieProkurora(doc_number="AG-1")
        doc.situation = ("… ч. 2 ст. 1 гл. 15 УК. "
                         "О принятии обращения к собственному производству")
        self.assertFalse(doc.facts_text().rstrip().endswith("производству."))

    def test_city_is_not_an_event_place(self):
        self.assertTrue(gdata.is_place_placeholder("г. Лос-Сантос"))
        self.assertTrue(gdata.is_place_placeholder("Лос-Сантос"))
        self.assertFalse(gdata.is_place_placeholder("КПП-2 Форта-Занкудо"))

    def test_first_resolution_point_survives(self):
        """Пункт с «(удостоверение № …)» больше не считается подписью."""
        answer = (
            "ПОСТАНОВЛЕНИЕ\nНомер: AG-1\n"
            "ПОСТАНОВИЛ:\n"
            "1. Принять обращение гражданина Nikita Hiddens в отношении сотрудника "
            "Nello Deshine (удостоверение № 171034) к собственному производству.\n"
            "2. Возбудить прокуроровское производство.\n"
            "Прокурор штата Сан-Андреас\nM. Nomad\nУдостоверение № 85028\n")
        doc = gdata.parse_gov_answer(answer)
        self.assertEqual(len(doc.points), 2)
        self.assertTrue(doc.points[0].startswith("Принять обращение"))
        self.assertIn("прокурорское производство", doc.points[1])

    def test_typos_are_fixed(self):
        self.assertEqual(gdata.fix_gov_typos("Возбудить прокуроровское производство"),
                         "Возбудить прокурорское производство")

    def test_non_prosecutor_rank_is_replaced(self):
        doc = gdata.PostanovlenieProkurora(
            author_rank="Куратор отдела IAD (Assistent sheriff), Сенатор")
        self.assertEqual(doc.person_rank(), "Прокурор")
        self.assertIn("Я, Прокурор штата Сан-Андреас", doc.preamble())
        prosecutor = gdata.PostanovlenieProkurora(author_rank="Прокурор штата Сан-Андреас")
        self.assertEqual(prosecutor.person_rank(), "Прокурор штата Сан-Андреас")

    def test_gov_form_uses_attorney_general_assets(self):
        import doc_render as render
        assets = render.DocAssets(Path("."))
        gov = assets.for_gov()
        if assets.coat_gov:
            self.assertEqual(Path(gov.coat).name, "gerb_gov.png")
        if assets.stamp_gov:
            self.assertEqual(Path(gov.stamp).name, "stamp_gov.png")
        self.assertIsNotNone(gov.fonts)

    def test_each_clause_of_a_list_gets_genitive(self):
        value = pdoc.clause_genitive("часть 2 статьи 12.8, часть 2 статьи 15.1")
        self.assertEqual(value, "части 2 статьи 12.8, части 2 статьи 15.1")

    def test_act_genitive_for_adjectives(self):
        self.assertEqual(pdoc.act_genitive("Уголовный кодекс штата Сан-Андреас"),
                         "Уголовного кодекса штата Сан-Андреас")


class V38BasisTests(unittest.TestCase):
    """«Основание» не должно повторять названия актов (выгрузка AG-2)."""

    def test_acts_named_with_articles_are_not_repeated(self):
        doc = gdata.PostanovlenieProkurora(
            violation_act="Уголовный кодекс штата Сан-Андреас, "
                          "Процессуальный кодекс штата Сан-Андреас",
            violation_clause="статья 16.4 Уголовного кодекса штата Сан-Андреас, "
                             "статья 17 Процессуального кодекса штата Сан-Андреас")
        basis = doc.basis_text()
        self.assertEqual(basis.lower().count("уголовн"), 1)
        self.assertEqual(basis.lower().count("процессуальн"), 1)

    def test_act_still_printed_when_articles_have_no_act_name(self):
        doc = gdata.PostanovlenieProkurora(
            violation_act="Уголовный кодекс штата Сан-Андреас",
            violation_clause="часть 2 статьи 12.8, часть 2 статьи 15.1")
        basis = doc.basis_text()
        # v39: акт печатается в родительном падеже — «часть 2 статьи 12.8 …
        # Уголовного кодекса штата Сан-Андреас».
        self.assertIn("Уголовного кодекса штата Сан-Андреас", basis)
        self.assertIn("части 2 статьи 12.8", pdoc.clause_genitive(
            doc.get("violation_clause")))

    def test_norm_without_act_name_keeps_the_act(self):
        doc = gdata.PostanovlenieProkurora(
            violation_act="Уголовный кодекс штата Сан-Андреас",
            violation_clause="часть 2 статьи 12.8")
        self.assertIn("Уголовного кодекса", doc.basis_text())


class V39DocTypeTwoPartsTests(unittest.TestCase):
    """v39: «Вид решения» = первичное решение + итоговая санкция."""

    def setUp(self):
        self.doc = gdata.PostanovlenieProkurora(
            doc_number="AG-1", doc_date="07.11.2026",
            author_name="Marcus Nomad", author_id="85028",
            applicant_name="Kinder Himmars", applicant_id="54337",
            appeal_date="06.11.2026")

    # ── части списка ────────────────────────────────────────────────────
    def test_primary_part_has_only_procedural_decisions(self):
        for kind in gdata.GOV_PRIMARY_TYPES:
            self.assertEqual(gdata.gov_stage(kind), gdata.GOV_STAGE_PRIMARY, kind)
        self.assertIn("О принятии обращения к собственному производству",
                      gdata.GOV_PRIMARY_TYPES)
        self.assertIn("Об отказе в принятии обращения к рассмотрению",
                      gdata.GOV_PRIMARY_TYPES)

    def test_final_part_has_sanctions(self):
        for kind in gdata.GOV_FINAL_TYPES:
            self.assertEqual(gdata.gov_stage(kind), gdata.GOV_STAGE_FINAL, kind)
        self.assertIn("О привлечении к административной ответственности",
                      gdata.GOV_FINAL_TYPES)
        self.assertIn("О возбуждении уголовного дела по обращению",
                      gdata.GOV_FINAL_TYPES)

    def test_refusal_in_merits_is_final_stage(self):
        self.assertEqual(gdata.gov_stage("Об отказе в удовлетворении обращения"),
                         gdata.GOV_STAGE_FINAL)

    def test_stage_types_split(self):
        self.assertEqual(gdata.stage_types(gdata.GOV_STAGE_PRIMARY),
                         gdata.GOV_PRIMARY_TYPES)
        self.assertEqual(gdata.stage_types(gdata.GOV_STAGE_FINAL),
                         gdata.GOV_FINAL_TYPES)

    def test_auto_value_is_not_a_concrete_type(self):
        self.assertNotIn(gdata.GOV_DOC_TYPE_AUTO, gdata.GOV_DOC_TYPES)

    # ── этап документа ──────────────────────────────────────────────────
    def test_stage_derived_from_doc_type(self):
        self.doc.doc_type = "О привлечении к административной ответственности"
        self.assertTrue(self.doc.is_final())
        self.doc.doc_type = "О переадресации обращения по подведомственности"
        self.assertFalse(self.doc.is_final())

    def test_explicit_stage_wins(self):
        self.doc.doc_type = "О принятии обращения к собственному производству"
        self.doc.doc_stage = gdata.GOV_STAGE_FINAL
        self.assertEqual(self.doc.stage(), gdata.GOV_STAGE_FINAL)

    # ── заявка для ИИ ───────────────────────────────────────────────────
    def test_chat_request_states_stage_and_rules(self):
        self.doc.doc_type = "О привлечении к административной ответственности"
        self.doc.appeal_raw = "1. Данные о заявителе: Kinder Himmars 54337"
        text = self.doc.to_chat_txt()
        self.assertIn("Этап: 2.", text)
        self.assertIn("ЭТАП 2", text)
        self.assertIn("статье 23", text)

    def test_primary_stage_forbids_sanctions(self):
        self.doc.doc_type = "Об отказе в принятии обращения к рассмотрению"
        self.doc.appeal_raw = "1. Данные о заявителе: Kinder Himmars 54337"
        text = self.doc.to_chat_txt()
        self.assertIn("ЭТАП 1", text)
        self.assertIn("санкцию не", text.lower())
        self.assertIn("статьи 16", text)

    # ── нормализация вида решения из ответа ИИ ──────────────────────────
    def test_free_form_type_is_normalized(self):
        self.assertEqual(
            gdata.normalize_gov_doc_type("Постановление об отказе в рассмотрении обращения"),
            "Об отказе в принятии обращения к рассмотрению")
        self.assertEqual(
            gdata.normalize_gov_doc_type("отказ в удовлетворении обращения"),
            "Об отказе в удовлетворении обращения")
        self.assertEqual(
            gdata.normalize_gov_doc_type("привлечение к административной ответственности"),
            "О привлечении к административной ответственности")

    def test_answer_marks_auto_type_by_merits(self):
        base = gdata.PostanovlenieProkurora(doc_number="AG-3", doc_date="07.11.2026",
                                            doc_type=gdata.GOV_DOC_TYPE_AUTO)
        answer = ("===ПОСТАНОВЛЕНИЕ===\n"
                  "Номер: AG-3\n"
                  "Дата: 07.11.2026\n"
                  "Заявитель: Kinder Himmars\n"
                  "УСТАНОВИЛ:\n"
                  "Сотрудник FIB допустил нарушение Этического кодекса.\n"
                  "ПОСТАНОВИЛ:\n"
                  "1. Признать сотрудника виновным и назначить штраф в размере "
                  "30000 $.\n"
                  "===КОНЕЦ ПОСТАНОВЛЕНИЯ===\n")
        doc = gdata.parse_gov_answer(answer, base=base)
        self.assertIsNotNone(doc)
        self.assertEqual(doc.get("doc_type"),
                         "О привлечении к административной ответственности")
        self.assertTrue(doc.is_final())

    # ── резолюция по этапам ─────────────────────────────────────────────
    def test_merits_refusal_points(self):
        self.doc.doc_type = "Об отказе в удовлетворении обращения"
        self.doc.legal_basis = "пункт «б» части 1 статьи 23 Регламента"
        points = " ".join(self.doc.decision_points())
        self.assertIn("В удовлетворении обращения", points)
        self.assertIn("недостаточности", points)
        self.assertIn("обжаловано", points)
        self.assertNotIn("Отказать в принятии обращения", points)

    def test_redirect_points_name_the_body(self):
        self.doc.doc_type = "О переадресации обращения по подведомственности"
        self.doc.subject_department = "FIB"
        points = " ".join(self.doc.decision_points())
        self.assertIn("Intelligence Branch (IB)", points)

    def test_completion_points(self):
        self.doc.doc_type = ("О завершении проверки по обращению и передаче "
                             "материалов в уголовное производство")
        points = " ".join(self.doc.decision_points())
        self.assertIn("Завершить проверку", points)
        self.assertIn("уголовное производство", points)

    # ── проверки по праву ───────────────────────────────────────────────
    def test_check_flags_sanction_on_primary_stage(self):
        self.doc.doc_type = "О принятии обращения к собственному производству"
        self.doc.points = ["Назначить штраф в размере 30000 $."]
        issues = gdata.check_gov_document(self.doc)
        self.assertTrue(any("этап 1" in issue for issue in issues), issues)

    def test_check_flags_merits_without_sanction(self):
        self.doc.doc_type = "О привлечении к административной ответственности"
        self.doc.points = ["Принять к рассмотрению обращение Kinder Himmars."]
        issues = gdata.check_gov_document(self.doc)
        self.assertTrue(any("итоговое" in issue.lower() for issue in issues), issues)

    def test_check_flags_sanction_without_norm(self):
        self.doc.doc_type = "О привлечении к административной ответственности"
        self.doc.points = ["Признать виновным и назначить штраф в размере 30000 $."]
        issues = gdata.check_gov_document(self.doc)
        self.assertTrue(any("нормы" in issue for issue in issues), issues)

    def test_sanction_with_norm_passes(self):
        self.doc.doc_type = "О привлечении к административной ответственности"
        self.doc.points = ["Признать виновным в нарушении статьи 28 Этического "
                           "кодекса и назначить штраф в размере 30000 $.",
                           "Настоящее постановление может быть обжаловано "
                           "вышестоящему прокурору в срок 72 часа (статьи 27–28 "
                           "Регламента)."]
        self.doc.violation_clause = "статья 28 Этического кодекса"
        issues = gdata.check_gov_document(self.doc)
        self.assertFalse(any("нормы" in issue for issue in issues), issues)

    # ── форма ───────────────────────────────────────────────────────────
    def test_form_has_stage_selector_and_dependent_list(self):
        src = Path("postanovlenie_prokuror_ui.py").read_text(encoding="utf-8")
        self.assertIn("self.cb_stage", src)
        self.assertIn("_on_stage_changed", src)
        self.assertIn("GOV_STAGE_TITLES", src)
        # v41: в форме два разных значения «определит ИИ» — через
        # `auto_type_for()`/`type_choices()`, а не одно общее.
        self.assertIn("auto_type_for", src)

    def test_form_fills_types_by_stage(self):
        src = Path("postanovlenie_prokuror_ui.py").read_text(encoding="utf-8")
        block = src[src.index("def _fill_types"):]
        block = block[:block.index("def _on_stage_changed")]
        self.assertIn("type_choices(stage)", block)
        self.assertNotIn("GOV_DOC_TYPE_AUTO", block)   # v41: только по этапу

    def test_suggestion_prefers_criminal_over_court(self):
        doc = gdata.PostanovlenieProkurora(
            doc_stage=gdata.GOV_STAGE_FINAL,
            doc_type=gdata.GOV_DOC_TYPE_AUTO,
            situation="В действиях сотрудника усматриваются признаки "
                      "преступления, предусмотренного статьёй 15.1 УК.")
        self.assertEqual(gdata.suggest_doc_type(doc),
                         "О возбуждении уголовного дела по обращению")

    def test_suggestion_uses_penalty_field(self):
        doc = gdata.PostanovlenieProkurora(
            doc_stage=gdata.GOV_STAGE_FINAL, doc_type=gdata.GOV_DOC_TYPE_AUTO,
            penalty="привлечь к административной ответственности, штраф в размере 30000 $")
        self.assertEqual(gdata.suggest_doc_type(doc),
                         "О привлечении к административной ответственности")

    def test_suggestion_falls_back_to_refusal_without_evidence(self):
        doc = gdata.PostanovlenieProkurora(
            doc_stage=gdata.GOV_STAGE_FINAL, doc_type=gdata.GOV_DOC_TYPE_AUTO,
            situation="Изложены обстоятельства дела.")
        self.assertEqual(gdata.suggest_doc_type(doc),
                         "Об отказе в удовлетворении обращения")

    def test_auto_value_is_not_normalized_to_a_type(self):
        self.assertEqual(gdata.normalize_gov_doc_type(gdata.GOV_DOC_TYPE_AUTO), "")


class V39PaginationTests(unittest.TestCase):
    """v39: текст не должен наезжать на колонтитул «стр. N из M»."""

    def _render(self, doc):
        import doc_render as render
        assets = gdata.gov_assets(Path("."))
        return render.render_document(doc, assets), render

    def test_long_facts_break_across_pages(self):
        doc = gdata.PostanovlenieProkurora(
            doc_number="AG-9", doc_date="07.11.2026", author_name="M Nomad",
            author_id="85028", applicant_name="Kinder Himmars",
            doc_type="О принятии обращения к собственному производству",
            situation=" ".join(f"Обстоятельство номер {i} установлено по "
                               f"видеофиксации и подтверждено тайм-кодом 0{i % 10}:15."
                               for i in range(1, 90)))
        pages, render = self._render(doc)
        self.assertGreaterEqual(len(pages), 2)
        limit = render.PAGE_H - render.MARGIN_BOTTOM - 120
        for page in pages:
            bottom = getattr(page, "_text_bottom", 0)
            self.assertLessEqual(bottom, limit + 60, "текст ушёл под колонтитул")

    def test_paragraph_is_split_not_dropped(self):
        doc = gdata.PostanovlenieProkurora(
            doc_number="AG-10", doc_date="07.11.2026", author_name="M Nomad",
            applicant_id="1", applicant_name="A B",
            doc_type="О принятии обращения к собственному производству",
            situation=" ".join(f"Фрагмент {i} описания события." for i in range(1, 220)))
        pages, _render = self._render(doc)
        text = doc.to_document_txt()
        # последний фрагмент абзаца действительно попал в документ
        self.assertIn("Фрагмент 219 описания события", text)
        self.assertGreater(len(pages), 1)


class V391AutoForPrimaryStageTests(unittest.TestCase):
    """v39.1: «Определит ИИ» доступен и на первичном этапе."""

    def setUp(self):
        self.form = "1. Данные о заявителе: Mark Skywalker 152306\n" \
                    "3. Сведения о нарушителе: Kyria Suspectzx 8332 FIB\n"

    def _doc(self, description):
        from postanovlenie_prokuror_data import PostanovlenieProkurora
        return PostanovlenieProkurora(
            doc_stage=gdata.GOV_STAGE_PRIMARY, doc_type=gdata.GOV_DOC_TYPE_AUTO,
            appeal_raw=(self.form + f"5. Подробное описание ситуации: {description}\n"
                        "Подпись и расшифровка заявителя: M.S"))

    def test_auto_is_offered_on_both_stages(self):
        """v43: «определит ИИ» есть на обоих этапах; по умолчанию — по обращению."""
        src = Path("postanovlenie_prokuror_ui.py").read_text(encoding="utf-8")
        block = src[src.index("def _fill_types"):]
        block = block[:block.index("def _on_stage_changed")]
        self.assertIn("gdata.type_choices(stage)", block)
        for stage in (gdata.GOV_STAGE_PRIMARY, gdata.GOV_STAGE_FINAL):
            choices = gdata.type_choices(stage)
            self.assertEqual(choices[0], gdata.DEFAULT_DOC_TYPE)
            self.assertIn(gdata.auto_type_for(stage), choices)

    def test_auto_primary_accepts_well_formed_appeal(self):
        self.assertEqual(gdata.suggest_doc_type(self._doc("Задержали без оснований.")),
                         "О принятии обращения к собственному производству")

    def test_auto_primary_refuses_without_description(self):
        doc = self._doc("")
        self.assertEqual(gdata.suggest_doc_type(doc),
                         "Об отказе в принятии обращения к рассмотрению")

    def test_auto_primary_stops_withdrawn_appeal(self):
        self.assertEqual(
            gdata.suggest_doc_type(self._doc("Прошу отозвать обращение.")),
            "О прекращении производства по обращению")

    def test_auto_primary_redirects_foreign_competence(self):
        self.assertEqual(
            gdata.suggest_doc_type(self._doc("Вопрос не относится к компетенции ОГП.")),
            "О переадресации обращения по подведомственности")

    def test_primary_suggestion_always_in_primary_part(self):
        for text in ("", "Обычное описание.", "Прошу отозвать обращение."):
            stage = gdata.gov_stage(gdata.suggest_doc_type(self._doc(text)))
            self.assertEqual(stage, gdata.GOV_STAGE_PRIMARY, text)


class V391LayoutTests(unittest.TestCase):
    """v39.1: форма должна вмещать содержимое без обрезки."""

    def setUp(self):
        self.src = Path("postanovlenie_prokuror_ui.py").read_text(encoding="utf-8")

    def test_window_is_wide_enough_for_two_columns(self):
        self.assertIn("setMinimumSize(1080, 720)", self.src)
        self.assertIn("resize(1240, 880)", self.src)

    def test_left_column_fits_long_rank_and_type(self):
        self.assertIn("left.setMinimumWidth(460)", self.src)
        self.assertIn("left.setMaximumWidth(500)", self.src)

    def test_stage_combo_uses_short_labels_with_tooltips(self):
        self.assertIn("GOV_STAGE_SHORT", self.src)
        self.assertIn("setToolTip(gdata.GOV_STAGE_TITLES[stage])", self.src)

    def test_type_combo_does_not_clip_long_value(self):
        self.assertIn("setMinimumContentsLength", self.src)
        self.assertIn("cb_type.setToolTip(text)", self.src)

    def test_only_one_hint_label_under_lists(self):
        """v42: лишних подсказок под списками нет — только пояснение этапа."""
        self.assertNotIn("self.type_hint = self._label(", self.src)
        self.assertIn("self.stage_hint = self._label(", self.src)

    def test_empty_appeal_hint_is_neutral_not_warning(self):
        block = self.src[self.src.index("def _on_appeal_changed"):]
        block = block[:block.index("def _intake_check")]
        self.assertNotIn('"Поле пустое', block)

    def test_placeholders_do_not_carry_instructions(self):
        """v42: в полях только образец данных, без служебных указаний."""
        self.assertNotIn("Необязательно: если оставить пустым", self.src)
        self.assertNotIn("Поле пустое", self.src)
        self.assertIn("00:04 — сотрудник с ОПЗ 8332", self.src)

    def test_short_stage_labels_fit_the_combo(self):
        for stage, title in gdata.GOV_STAGE_SHORT.items():
            self.assertLessEqual(len(title), 48, title)
            self.assertIn(stage, gdata.GOV_STAGE_TITLES)


class V40ExtraFactsTests(unittest.TestCase):
    """v40: в «УСТАНОВИЛ» не должно быть лишнего материала (выгрузка AG-1)."""

    APPEAL = (
        "От: гражданина штата Сан-Андреас Sae Gucci\n"
        "1. Данные о заявителе: Sae Gucci 56741\n"
        "2. Предмет обращения: заявление об обжаловании решения должностного лица\n"
        "3. Сведения о нарушителе: сотрудник FIB\n"
        "5. Подробное описание ситуации: Я стоял, никого не трогал, ко мне "
        "подбегает шериф и просит снять маску. Сотрудник ФИБ накинул на меня "
        "наручники, хотя 15 секунд на исполнение требования не закончились.\n"
        "8. Доказательства правонарушения: не указаны\n"
        "Подпись и расшифровка заявителя: S.Gucci\n")

    ESTABLISHED = ("В Офис Генерального прокурора поступило обращение от "
                   "гражданина Sae Gucci на неправомерные действия сотрудника "
                   "Federal Investigation Bureau. Доказательства отсутствуют.")

    def _doc(self, **kwargs):
        doc = gdata.PostanovlenieProkurora(
            doc_number="AG-1", doc_date="07.11.2026", author_name="Marcus Nomad",
            author_id="85028", applicant_name="Sae Gucci",
            doc_stage=gdata.GOV_STAGE_FINAL,
            doc_type="Об отказе в удовлетворении обращения")
        doc.apply_appeal(self.APPEAL)
        doc.established = self.ESTABLISHED
        for key, value in kwargs.items():
            setattr(doc, key, value)
        return doc

    # ── пересказ заявителя не дублируется ───────────────────────────────
    def test_raw_appeal_in_situation_is_not_repeated(self):
        doc = self._doc(situation=self.APPEAL, prosecutor_facts=self.APPEAL)
        facts = doc.facts_text()
        self.assertIn("Доказательства отсутствуют", facts)
        self.assertNotIn("Я стоял, никого не трогал", facts)

    def test_own_prosecutor_facts_are_kept(self):
        own = ("00:14 — сотрудник ФИБ надевает наручники; 00:22 — уводит "
               "заявителя к патрульному автомобилю.")
        doc = self._doc(situation=own, prosecutor_facts=own)
        facts = doc.facts_text()
        self.assertIn("00:14", facts)
        self.assertIn("Доказательства отсутствуют", facts)
        self.assertNotIn("Я стоял, никого не трогал", facts)

    def test_situation_matching_appeal_description_is_dropped(self):
        doc = self._doc(situation="Я стоял, никого не трогал, ко мне подбегает "
                                  "шериф и просит снять маску.")
        self.assertNotIn("Я стоял, никого не трогал", doc.facts_text())

    # ── «образуют нарушение» только при реальном нарушении ─────────────
    def test_no_offence_phrase_for_refusal_in_merits(self):
        doc = self._doc(violation_act="Регламент подачи обращений в Офис "
                                      "Генерального прокурора штата San-Andreas",
                        violation_clause="отсутствует")
        facts = doc.facts_text()
        self.assertNotIn("образуют нарушение", facts)
        self.assertNotIn("Регламента подачи обращений", facts.split("Доказательства")[1])

    def test_no_offence_phrase_for_completed_check(self):
        doc = self._doc(doc_type="О завершении проверки по обращению и передаче "
                                 "материалов в уголовное производство",
                        violation_act="Регламент подачи обращений")
        self.assertNotIn("образуют нарушение", doc.facts_text())

    def test_offence_phrase_still_printed_when_charged(self):
        doc = self._doc(doc_type="О привлечении к административной ответственности",
                        violation_act="Этический кодекс штата San-Andreas",
                        violation_clause="статья 28")
        self.assertIn("образуют нарушение", doc.facts_text())

    # ── заглушки «не указан» больше не попадают в текст ─────────────────
    def test_placeholder_answers_are_recognized(self):
        for value in ("не указан", "Не указано", "отсутствует", "отсутствуют",
                      "нет данных", "—", "-", "неизвестно"):
            self.assertTrue(gdata.is_placeholder(value), value)
        for value in ("Sae Gucci", "44628", "статья 28 Этического кодекса"):
            self.assertFalse(gdata.is_placeholder(value), value)

    def test_resolution_has_no_placeholder_ids(self):
        doc = self._doc(applicant_id="не указан", subject_name="сотрудник FIB",
                        subject_id="не указано")
        points = " ".join(doc.decision_points())
        self.assertNotIn("не указан", points)
        self.assertNotIn("не указано", points)
        self.assertIn("Sae Gucci", points)

    # ── сквозной разбор ответа ИИ ───────────────────────────────────────
    def test_answer_with_placeholder_fields_produces_clean_facts(self):
        base = self._doc(situation=self.APPEAL, prosecutor_facts=self.APPEAL)
        answer = ("===ПОСТАНОВЛЕНИЕ===\n"
                  "Номер: AG-1\nДата: 07.11.2026\n"
                  "Вид документа: Об отказе в удовлетворении обращения\n"
                  "Пункт нарушения: отсутствует\n"
                  "Нарушенный акт: Регламент подачи обращений в Офис Генерального "
                  "прокурора штата San-Andreas\n"
                  "УСТАНОВИЛ:\n" + self.ESTABLISHED + "\n"
                  "ПОСТАНОВИЛ:\n1. В удовлетворении обращения отказать.\n"
                  "===КОНЕЦ ПОСТАНОВЛЕНИЯ===\n")
        doc = gdata.parse_gov_answer(answer, base=base)
        facts = doc.facts_text()
        self.assertNotIn("образуют нарушение", facts)
        self.assertNotIn("Я стоял, никого не трогал", facts)
        self.assertEqual(facts.count("Доказательства отсутствуют"), 1)


class V41FieldLabelLeakTests(unittest.TestCase):
    """v41: названия полей бланка не должны попадать в текст «УСТАНОВИЛ».

    Выгрузка «Постановление_4/AG-2»: ИИ закончил «Обстоятельства» обрубком
    «Отдел сотрудника:» (поле пустое), а в «УСТАНОВИЛ» первой строкой шло
    «Место события: здание Emergency Medical Services г. Лос-Сантос».
    """

    APPAREL = ("Видео длится 46 секунд, сотрудник FIB 301 просто стоит "
               "спиной и никак не реагирует")

    def test_tail_label_without_value_is_removed(self):
        cleaned = gdata.strip_field_label_leaks(self.APPAREL + "\nОтдел сотрудника:")
        self.assertEqual(cleaned, self.APPAREL)

    def test_label_with_value_keeps_only_value(self):
        cleaned = gdata.strip_field_label_leaks(
            "Место события: здание Emergency Medical Services г. Лос-Сантос. "
            "Видео длится 46 секунд.")
        self.assertEqual(cleaned, "здание Emergency Medical Services "
                                  "г. Лос-Сантос. Видео длится 46 секунд.")

    def test_label_in_the_middle_does_not_glue_sentences(self):
        cleaned = gdata.strip_field_label_leaks(
            "Здание EMS. Место события: Лос-Сантос. Далее сотрудник ушёл.")
        self.assertEqual(cleaned, "Здание EMS. Лос-Сантос. Далее сотрудник ушёл.")

    def test_plain_text_is_untouched(self):
        text = "Сидел я на раскладушке.\nО принятии обращения к производству"
        self.assertEqual(gdata.strip_field_label_leaks(text), text)

    def test_event_place_is_not_printed_as_a_field_label(self):
        doc = gdata.PostanovlenieProkurora(
            doc_number="AG-2", doc_stage=gdata.GOV_STAGE_FINAL,
            doc_type="О привлечении к административной ответственности",
            situation=self.APPAREL + "\nОтдел сотрудника:",
            prosecutor_facts=self.APPAREL + "\nОтдел сотрудника:",
            event_place="здание Emergency Medical Services г. Лос-Сантос",
            event_date="06.11.2026 10:17",
            applicant_name="Lordik Claw", applicant_id="83461",
            subject_name="сотрудника FIB с опознавательным знаком № 301",
            violation_act="Устав Federal Investigation Bureau",
            violation_clause="пункта 2.5 Главы II Устава FIB")
        gdata.drop_placeholders(doc)
        facts = doc.facts_text()
        self.assertNotIn("Отдел сотрудника", facts)
        self.assertNotIn("Место события:", facts)
        # место остаётся, но внутри фразы, а не ярлыком реквизита
        self.assertIn("здание Emergency Medical Services", facts)
        self.assertIn("Событие имело место", facts)
        self.assertNotIn("\n:", facts)


class V41FactsJoinTests(unittest.TestCase):
    """v41 (AG-2): части «УСТАНОВИЛ» не склеиваются и не дублируют вывод."""

    def _doc(self, **kwargs):
        params = dict(
            doc_number="AG-2", doc_stage=gdata.GOV_STAGE_FINAL,
            doc_type="О привлечении к административной ответственности",
            applicant_name="Lordik Claw", applicant_id="83461",
            subject_name="сотрудника FIB с опознавательным знаком № 301",
            violation_act="Устав Federal Investigation Bureau",
            violation_clause="пункта 2.5 Главы II Устава FIB")
        params.update(kwargs)
        return gdata.PostanovlenieProkurora(**params)

    def test_parts_are_separated_by_full_stop(self):
        doc = self._doc(
            situation="сотрудник FIB 301 просто стоит спиной и никак не реагирует",
            established="Гражданин обратился в Офис Генерального прокурора.")
        facts = doc.facts_text()
        self.assertIn("не реагирует.", facts)
        self.assertIn("реагирует. Гражданин обратился", facts)

    def test_offence_phrase_is_printed_once(self):
        doc = self._doc(
            established="Указанные обстоятельства образуют нарушение: статьи 28 "
                        "Этического кодекса.")
        self.assertEqual(doc.facts_text().count("образуют нарушение"), 1)

    def test_offence_phrase_appears_when_ai_did_not_write_it(self):
        doc = self._doc(established="Сотрудник бездействовал.")
        self.assertIn("образуют нарушение", doc.facts_text())


class V41HistorySplitTests(unittest.TestCase):
    """v41: истории IAD и Прокурора не пересекаются.

    Замечания: «Загрузить в форму» падало с AttributeError (история IAD
    парсила документ Прокурора), запись появлялась в истории дважды, а номер
    AG-2 продолжал нумерацию IAD.
    """

    def test_folders_are_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            iad = pdoc.DocumentHistory(root)
            gov = gdata.GovHistory(root)
            self.assertEqual(iad.folder, root / "history" / "iad")
            self.assertEqual(gov.folder, root / "history" / "gov")
            self.assertNotEqual(iad.folder, gov.folder)

    def test_gov_history_parses_gov_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            gov = gdata.GovHistory(Path(tmp))
            doc = gdata.PostanovlenieProkurora(
                doc_number="AG-1", applicant_name="Lordik Claw",
                subject_name="FIB 301",
                doc_stage=gdata.GOV_STAGE_FINAL,
                doc_type="О привлечении к административной ответственности",
                situation="Сотрудник бездействовал.", prosecutor_facts="Сотрудник бездействовал.")
            gov.save(doc)
            loaded = gov.load_index(0)
            self.assertIsInstance(loaded, gdata.PostanovlenieProkurora)
            # именно на этих методах падало «Загрузить в форму»
            self.assertEqual(loaded.stage(), gdata.GOV_STAGE_FINAL)
            self.assertIn("бездействовал", loaded.facts_text())

    def test_iad_history_ignores_gov_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gdata.GovHistory(root).save(gdata.PostanovlenieProkurora(
                doc_number="AG-1", applicant_name="Lordik Claw",
                subject_name="FIB 301"))
            iad = pdoc.DocumentHistory(root)
            self.assertEqual(iad.items(), [])
            self.assertEqual(iad.used_numbers(), [])

    def test_legacy_gov_record_does_not_leak_into_iad_list(self):
        """Старая запись Прокурора в корне хранилища не видна форме IAD (v41)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "20260101-000000.json").write_text(
                json.dumps(gdata.PostanovlenieProkurora(
                    doc_number="AG-1", doc_stage=gdata.GOV_STAGE_FINAL,
                    applicant_name="Lordik Claw").history_payload(),
                    ensure_ascii=False), encoding="utf-8")
            iad = pdoc.DocumentHistory(root)
            self.assertEqual(iad.items(), [])
            gov = gdata.GovHistory(root)
            self.assertEqual(len(gov.items()), 1)
            self.assertEqual(gov.load_index(0).get("doc_number"), "AG-1")

    def test_numbers_are_independent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            iad = pdoc.DocumentHistory(root)
            gov = gdata.GovHistory(root)
            iad.save(pdoc.Postanovlenie(doc_number="LSCSD-IAD-1",
                                        subject_name="Qorick Mythic"))
            iad.save(pdoc.Postanovlenie(doc_number="LSCSD-IAD-2",
                                        subject_name="Qorick Mythic"))
            self.assertEqual(
                gdata.PostanovlenieProkurora().auto_number(gov.used_numbers(),
                                                          prefix="AG"), "AG-1")
            gov.save(gdata.PostanovlenieProkurora(doc_number="AG-1",
                                                 applicant_name="Lordik Claw"))
            self.assertEqual(
                pdoc.Postanovlenie().auto_number(iad.used_numbers()), "LSCSD-IAD-3")

    def test_same_snapshot_is_not_duplicated(self):
        """Один снимок, скопированный в новый путь, показывается один раз."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            history = pdoc.DocumentHistory(root)
            doc = pdoc.Postanovlenie(doc_number="LSCSD-IAD-1", subject_name="Qorick")
            saved = history.save(doc)
            (root / saved.name).write_text(saved.read_text(encoding="utf-8"),
                                           encoding="utf-8")
            self.assertEqual(len(history.items()), 1)


class V41TwoStageAutoTests(unittest.TestCase):
    """v41: «Определит ИИ» на этапе 1 — по обращению, на этапе 2 — по обстоятельствам."""

    def test_auto_values_differ_by_stage(self):
        self.assertEqual(gdata.auto_type_for(gdata.GOV_STAGE_PRIMARY),
                         "Определит ИИ по обращению")
        self.assertEqual(gdata.auto_type_for(gdata.GOV_STAGE_FINAL),
                         "Определит ИИ по обстоятельствам")
        self.assertNotEqual(gdata.GOV_DOC_TYPE_AUTO_APPEAL, gdata.GOV_DOC_TYPE_AUTO)

    def test_auto_value_is_first_choice_of_its_stage(self):
        """v43: первым ВСЕГДА «Определит ИИ по обращению»; виды — своего этапа."""
        for stage in (gdata.GOV_STAGE_PRIMARY, gdata.GOV_STAGE_FINAL):
            choices = gdata.type_choices(stage)
            self.assertEqual(choices[0], gdata.DEFAULT_DOC_TYPE)
            self.assertEqual(choices[0], "Определит ИИ по обращению")
            # «по обстоятельствам» остаётся дополнительным пунктом (этап 2)
            self.assertIn(gdata.auto_type_for(stage), choices)
            # виды решений своего этапа, без чужих
            for kind in gdata.stage_types(stage):
                self.assertIn(kind, choices)
        # на итоговом этапе в списке два «определит ИИ»: по обращению и по
        # обстоятельствам — но по умолчанию выбран первый пункт.
        final = gdata.type_choices(gdata.GOV_STAGE_FINAL)
        self.assertIn(gdata.GOV_DOC_TYPE_AUTO, final)
        self.assertNotIn(gdata.GOV_DOC_TYPE_AUTO, gdata.type_choices(gdata.GOV_STAGE_PRIMARY))

    def test_stage_is_derived_from_auto_value(self):
        self.assertEqual(gdata.gov_stage(gdata.GOV_DOC_TYPE_AUTO_APPEAL),
                         gdata.GOV_STAGE_PRIMARY)
        self.assertEqual(gdata.gov_stage(gdata.GOV_DOC_TYPE_AUTO),
                         gdata.GOV_STAGE_FINAL)

    def test_auto_values_are_recognized_and_not_normalized(self):
        for value in (gdata.GOV_DOC_TYPE_AUTO, gdata.GOV_DOC_TYPE_AUTO_APPEAL):
            self.assertTrue(gdata.is_auto_type(value))
            self.assertEqual(gdata.normalize_gov_doc_type(value), "")
        self.assertFalse(gdata.is_auto_type("О принятии обращения к собственному производству"))

    def test_title_never_prints_the_auto_placeholder(self):
        doc = gdata.PostanovlenieProkurora(doc_type=gdata.GOV_DOC_TYPE_AUTO_APPEAL)
        self.assertNotIn("Определит ИИ", doc.title())
        doc = gdata.PostanovlenieProkurora(doc_type=gdata.GOV_DOC_TYPE_AUTO,
                                          doc_stage=gdata.GOV_STAGE_FINAL)
        self.assertNotIn("Определит ИИ", doc.title())

    def test_form_uses_stage_choices(self):
        src = (_P("postanovlenie_prokuror_ui.py")).read_text(encoding="utf-8")
        block = src[src.index("def _fill_types"):]
        block = block[:block.index("def _on_stage_changed")]
        self.assertIn("type_choices(stage)", block)
        self.assertIn("auto_type_for", src)

    def test_stage_note_explains_evidence_of_the_stage(self):
        """v42: подсказка этапа живёт в данных дела (`gov_case.STAGE_META`)."""
        import gov_case as c
        self.assertFalse(c.stage_meta(c.STAGE_INTAKE)["needs_facts"])
        self.assertTrue(c.stage_meta(c.STAGE_MEASURE)["needs_facts"])
        src = (_P("postanovlenie_prokuror_ui.py")).read_text(encoding="utf-8")
        self.assertIn("Этап откроется, когда обращение принято", src)

    def test_prompt_asks_for_appeal_based_decision_on_stage_one(self):
        src = (_P("main.py")).read_text(encoding="utf-8")
        self.assertIn("Определит ИИ по обращению", src)
        self.assertIn("Определит ИИ по обстоятельствам", src)


class V42IntakeParseTests(unittest.TestCase):
    """v42: «Проверка ИИ» — ответ ИИ переводит обращение в состояние."""

    ACCEPTED = ("===ПРОВЕРКА ОБРАЩЕНИЯ===\n"
                "Состояние: Принято\n"
                "Основание: статья 14 Регламента подачи обращений\n"
                "Вид решения: О принятии обращения к собственному производству\n"
                "Что проверить: доказательства и тайм-коды\n"
                "===КОНЕЦ ПРОВЕРКИ===\n===КОНЕЦ ОТВЕТА===")
    REFUSED = ("===ПРОВЕРКА ОБРАЩЕНИЯ===\n"
               "Состояние: Отказано\n"
               "Основание: пункт 13 части 1 статьи 16 Регламента\n"
               "===КОНЕЦ ПРОВЕРКИ===\n===КОНЕЦ ОТВЕТА===")

    def test_accepted_answer_sets_in_progress(self):
        result = gdata.parse_intake_answer(self.ACCEPTED)
        self.assertTrue(result["ok"])
        self.assertEqual(result["state"], gdata.INTAKE_ACCEPTED)
        self.assertIn("14", result["basis"])
        self.assertEqual(result["doc_type"],
                         "О принятии обращения к собственному производству")

    def test_refused_answer_keeps_the_basis(self):
        result = gdata.parse_intake_answer(self.REFUSED)
        self.assertEqual(result["state"], gdata.INTAKE_REFUSED)
        self.assertIn("16", result["basis"])

    def test_free_text_is_not_a_state(self):
        result = gdata.parse_intake_answer("Обычный правовой разбор без блока.")
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "")

    def test_states_are_recognized_in_free_wording(self):
        for text, expected in (
                ("Состояние: обращение принято к производству", gdata.INTAKE_ACCEPTED),
                ("Состояние: в принятии обращения отказано", gdata.INTAKE_REFUSED),
                ("Состояние: переадресовано в IB FIB", gdata.INTAKE_REDIRECTED),
                ("Состояние: производство прекращено", gdata.INTAKE_TERMINATED)):
            self.assertEqual(gdata.normalize_intake_state(text), expected, text)


class V42CaseTests(unittest.TestCase):
    """v42: дело «Обращение №X», этапы и разделение PNG по этапам."""

    APPEAL = ("От: гражданина штата Сан-Андреас Lordik Claw\n"
              "1. Данные о заявителе: Lordik Claw 83461\n"
              "3. Сведения о нарушителе: 301 FIB\n"
              "5. Подробное описание ситуации: Сотрудник FIB не пресёк драку.\n"
              "8. Доказательства правонарушения: Видеофиксация")

    ACCEPTED = ("===ПРОВЕРКА ОБРАЩЕНИЯ===\nСостояние: Принято\n"
                "Основание: статья 14 Регламента\n===КОНЕЦ ПРОВЕРКИ===")
    REFUSED = ("===ПРОВЕРКА ОБРАЩЕНИЯ===\nСостояние: Отказано\n"
               "Основание: пункт 13 части 1 статьи 16\n===КОНЕЦ ПРОВЕРКИ===")

    def setUp(self):
        import gov_case
        self.c = gov_case

    # ── состояния ───────────────────────────────────────────────────────
    def test_accepted_case_opens_the_measure_stage(self):
        case = self.c.GovCase(number="1", appeal_raw=self.APPEAL)
        case.apply_intake(self.c.parse_intake_answer(self.ACCEPTED))
        self.assertEqual(case.status, self.c.STATUS_IN_PROGRESS)
        self.assertTrue(case.accepted())
        self.assertEqual(case.next_stage()["kind"], self.c.STAGE_MEASURE)

    def test_refused_case_blocks_the_measure_stage(self):
        case = self.c.GovCase(number="1", appeal_raw=self.APPEAL)
        case.apply_intake(self.c.parse_intake_answer(self.REFUSED))
        self.assertEqual(case.status, self.c.STATUS_REFUSED)
        self.assertFalse(case.accepted())
        self.assertEqual(case.next_stage(), {})

    def test_prosecutor_can_accept_against_ai_refusal(self):
        case = self.c.GovCase(number="1", appeal_raw=self.APPEAL)
        case.apply_intake(self.c.parse_intake_answer(self.REFUSED))
        case.apply_intake(self.c.parse_intake_answer(self.REFUSED), force_accept=True)
        self.assertTrue(case.accepted())
        self.assertIn("вопреки", case.intake_basis)

    def test_extra_stages_extend_the_chain(self):
        case = self.c.GovCase(number="1", appeal_raw=self.APPEAL)
        case.apply_intake(self.c.parse_intake_answer(self.ACCEPTED))
        case.add_stage(self.c.STAGE_RECHECK)
        self.assertIn(self.c.STAGE_RECHECK, case.plan())
        case.mark_stage(self.c.STAGE_MEASURE, doc_number="AG-1")
        self.assertEqual(case.next_stage()["kind"], self.c.STAGE_RECHECK)

    # ── обстоятельства прокурора ────────────────────────────────────────
    def test_prosecutor_facts_have_four_parts(self):
        case = self.c.GovCase(
            number="1", status=self.c.STATUS_IN_PROGRESS,
            facts_video="00:23 — удар по окружающим",
            facts_for="видео подтверждает бездействие",
            facts_against="указаний не было",
            proposal="штраф 20 000 $",
            own_decision="привлечь к ответственности")
        text = case.prosecutor_facts()
        self.assertIn("00:23", text)
        self.assertIn("В пользу обращения", text)
        self.assertIn("Против обращения", text)
        self.assertIn("Прокурор полагает возможным", text)
        self.assertIn("Решение прокурора", text)
        self.assertNotIn("null", text)

    def test_prosecutor_facts_are_sentences(self):
        case = self.c.GovCase(number="1", status=self.c.STATUS_IN_PROGRESS,
                              facts_video="00:23 — удар",
                              own_decision="привлечь к ответственности")
        text = case.prosecutor_facts()
        self.assertIn("удар.", text)
        self.assertIn("ответственности.", text)

    # ── этапы и файлы ───────────────────────────────────────────────────
    def test_png_names_differ_by_stage(self):
        first = self.c.stage_stem("AG-1", 1, self.c.STAGE_INTAKE)
        second = self.c.stage_stem("AG-1", 2, self.c.STAGE_MEASURE)
        self.assertNotEqual(first, second)
        self.assertIn("эт.1", first)
        self.assertIn("эт.2", second)
        self.assertIn("принятие", first)
        self.assertIn("санкция", second)

    def test_stage_folders_are_separate(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            first = self.c.stage_folder(tmp, "AG-1", 1, self.c.STAGE_INTAKE)
            second = self.c.stage_folder(tmp, "AG-1", 2, self.c.STAGE_MEASURE)
            self.assertNotEqual(first, second)
            self.assertTrue(first.is_dir() and second.is_dir())

    def test_first_stage_document_is_not_overwritten(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            doc = gdata.PostanovlenieProkurora(doc_number="AG-1")
            case = self.c.GovCase(number="1", appeal_raw=self.APPEAL)
            first = self.c.stage_artifacts(tmp, case, self.c.STAGE_INTAKE, 1, doc, "ответ 1")
            second = self.c.stage_artifacts(tmp, case, self.c.STAGE_MEASURE, 2, doc, "ответ 2")
            self.assertNotEqual(first["document"], second["document"])
            self.assertTrue(first["document"].exists())
            self.assertTrue(second["document"].exists())
            self.assertEqual(first["document"].read_text(encoding="utf-8"),
                             second["document"].read_text(encoding="utf-8"))

    # ── хранилище дел ───────────────────────────────────────────────────
    def test_store_numbers_cases_and_reopens_them(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            store = self.c.GovCaseStore(tmp)
            first = store.open_or_create(self.APPEAL)
            first.apply_intake(self.c.parse_intake_answer(self.ACCEPTED))
            store.save(first)
            self.assertEqual(first.number, "1")
            again = store.open_or_create(self.APPEAL)
            self.assertEqual(again.number, "1")          # то же обращение — то же дело
            reopened = store.load("1")
            self.assertEqual(reopened.status, self.c.STATUS_IN_PROGRESS)
            self.assertIn("Lordik Claw", reopened.label())

    def test_intake_request_asks_for_the_service_block(self):
        case = self.c.GovCase(number="1", appeal_raw=self.APPEAL)
        text = self.c.intake_request(case, "Marcus Nomad")
        self.assertIn("===ПРОВЕРКА ОБРАЩЕНИЯ===", text)
        self.assertIn("Состояние:", text)
        self.assertIn("Основание:", text)
        self.assertIn(self.APPEAL, text)

    # ── форма ───────────────────────────────────────────────────────────
    def test_form_has_check_button_and_no_preview(self):
        src = (_P("postanovlenie_prokuror_ui.py")).read_text(encoding="utf-8")
        self.assertIn('QPushButton("Проверка ИИ")', src)
        self.assertIn("_intake_check", src)
        self.assertIn("_force_accept", src)
        # v42: лишние кнопки убраны
        self.assertNotIn('QPushButton("Предпросмотр")', src)
        self.assertNotIn('QPushButton("Сохранить без отправки")', src)
        self.assertNotIn("def _make_preview", src)
        self.assertNotIn("def _save_only", src)

    def test_form_has_four_facts_fields(self):
        src = (_P("postanovlenie_prokuror_ui.py")).read_text(encoding="utf-8")
        for key in ("facts_video", "facts_for", "facts_against", "proposal",
                    "own_decision"):
            self.assertIn(f'self.texts["{key}"]', src)

    def test_form_opens_case_by_number(self):
        src = (_P("postanovlenie_prokuror_ui.py")).read_text(encoding="utf-8")
        self.assertIn("Открыть обращение №", src)
        self.assertIn("def open_case", src)
        self.assertIn("GovCaseStore", src)

    def test_form_has_no_verbose_stage_hints(self):
        src = (_P("postanovlenie_prokuror_ui.py")).read_text(encoding="utf-8")
        self.assertNotIn("STAGE_HINTS", src)
        self.assertNotIn("Санкция не назначается.", src)

    def test_default_type_is_ai_choice(self):
        """v43: по умолчанию в «Вид решения» — «Определит ИИ по обращению»."""
        src = (_P("postanovlenie_prokuror_ui.py")).read_text(encoding="utf-8")
        block = src[src.index("def _fill_types"):]
        block = block[:block.index("def _on_stage_changed")]
        self.assertIn("DEFAULT_DOC_TYPE", block)
        self.assertIn("setCurrentIndex", block)
        self.assertIn("is_auto_type", block)
        self.assertEqual(gdata.type_choices(gdata.GOV_STAGE_PRIMARY)[0],
                         "Определит ИИ по обращению")


class V42PreambleTests(unittest.TestCase):
    """v42: нормы вводной части берутся из ответа ИИ, а не зашиты в бланк."""

    def test_custom_preamble_basis_is_used(self):
        doc = gdata.PostanovlenieProkurora(
            author_name="Marcus Nomad", author_rank="Прокурор",
            author_id="85028", applicant_name="Lordik Claw",
            preamble_basis="частью 2 статьи 36 Конституции штата Сан-Андреас и "
                           "статьёй 33 Закона «О деятельности офиса Генерального "
                           "прокурора штата Сан-Андреас»")
        preamble = doc.preamble()
        self.assertIn("статьёй 33 Закона", preamble)
        self.assertNotIn("частями 1, 2 и 6 статьи 8", preamble)

    def test_default_basis_is_used_without_answer_norms(self):
        doc = gdata.PostanovlenieProkurora(
            author_name="Marcus Nomad", applicant_name="Lordik Claw")
        self.assertIn("частями 1, 2 и 6 статьи 8", doc.preamble())

    def test_fields_survive_history_roundtrip(self):
        doc = gdata.PostanovlenieProkurora(case_number="3", case_stage_kind="measure",
                                           preamble_basis="статьёй 23 Регламента")
        again = gdata.PostanovlenieProkurora.from_dict(doc.to_dict())
        self.assertEqual(again.case_number, "3")
        self.assertEqual(again.case_stage_kind, "measure")
        self.assertIn("23", again.preamble())


class V42PromptTests(unittest.TestCase):
    """v42: промт читает ИИ — без ссылок на версии и с отдельной проверкой."""

    def setUp(self):
        root = _P("..")
        self.path = root / "Для новой версии" / "Текущий промт" / "promt 1.28.txt"

    def test_prompt_exists_and_has_no_version_notes(self):
        text = self.path.read_text(encoding="utf-8")
        self.assertNotIn("замечание v", text)
        self.assertNotIn("выгрузк", text)
        self.assertNotIn("v41", text)

    def test_prompt_describes_the_service_block(self):
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("===ПРОВЕРКА ОБРАЩЕНИЯ===", text)
        self.assertIn("===КОНЕЦ ПРОВЕРКИ===", text)

    def test_prompt_does_not_hardcode_the_preamble_norms(self):
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("руководствуясь <НОРМЫ>", text)
        self.assertNotIn("а также статьями 11, 12, 13 и 14 Регламента", text)

    def test_project_rules_are_not_ignored_but_go_last(self):
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("Дополнительно по правилам проекта", text)
        self.assertIn("если вопрос прямо касается правил проекта", text)


class V43IntakeTriggerTests(unittest.TestCase):
    """v43: «Состояние: Отказать» и другие формулировки ИИ должны распознаваться."""

    @staticmethod
    def _answer(state_line: str) -> str:
        return ("===ПРОВЕРКА ОБРАЩЕНИЯ===\n" + state_line +
                "\nОснование: пункт 13 части 1 статьи 16 Регламента\n"
                "===КОНЕЦ ПРОВЕРКИ===\n===КОНЕЦ ОТВЕТА===")

    def test_infinitive_refusal_is_recognized(self):
        """Баг: ИИ написал «Состояние: Отказать» — дело осталось «В процессе»."""
        result = gdata.parse_intake_answer(self._answer("Состояние: Отказать"))
        self.assertTrue(result["ok"])
        self.assertEqual(result["state"], gdata.INTAKE_REFUSED)
        self.assertIn("16", result["basis"])

    def test_other_refusal_wordings(self):
        for wording in ("Отказано", "отказ", "Отказать в принятии",
                        "отказ в принятии обращения", "в принятии отказать",
                        "обращение не подлежит принятию", "ОТКАЗАТЬ"):
            result = gdata.parse_intake_answer(self._answer(f"Состояние: {wording}"))
            self.assertEqual(result["state"], gdata.INTAKE_REFUSED, wording)

    def test_acceptance_wordings(self):
        for wording in ("Принято", "Принять", "принимается",
                        "Принято к производству"):
            result = gdata.parse_intake_answer(self._answer(f"Состояние: {wording}"))
            self.assertEqual(result["state"], gdata.INTAKE_ACCEPTED, wording)

    def test_other_states_still_recognized(self):
        self.assertEqual(
            gdata.parse_intake_answer(self._answer("Состояние: Переадресовать"))["state"],
            gdata.INTAKE_REDIRECTED)
        self.assertEqual(
            gdata.parse_intake_answer(self._answer("Состояние: Прекратить"))["state"],
            gdata.INTAKE_TERMINATED)

    def test_block_written_on_one_line(self):
        text = ("=== ПРОВЕРКА ОБРАЩЕНИЯ === Состояние: Отказано; "
                "Основание: пункт 1 части 1 статьи 16 === КОНЕЦ ПРОВЕРКИ ===")
        result = gdata.parse_intake_answer(text)
        self.assertEqual(result["state"], gdata.INTAKE_REFUSED)
        self.assertIn("16", result["basis"])

    def test_answer_without_markers_is_still_read(self):
        result = gdata.parse_intake_answer(
            "Состояние: Отказать. Основание: пункт 13 части 1 статьи 16")
        self.assertEqual(result["state"], gdata.INTAKE_REFUSED)
        self.assertTrue(gdata.has_intake_block(
            "Состояние: Отказать. Основание: пункт 13 части 1 статьи 16"))

    def test_markdown_answer_is_read(self):
        text = ("**===ПРОВЕРКА ОБРАЩЕНИЯ===**\n**Состояние:** **Отказать**\n"
                "**Основание:** пункт 1 части 1 статьи 16\n"
                "**===КОНЕЦ ПРОВЕРКИ===**")
        result = gdata.parse_intake_answer(text)
        self.assertEqual(result["state"], gdata.INTAKE_REFUSED)

    def test_plain_text_is_still_rejected(self):
        result = gdata.parse_intake_answer("Обычный разбор без служебного блока.")
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "")

    def test_refusal_reflected_in_case_status(self):
        import gov_case as c
        case = c.GovCase(number="1", appeal_raw="обращение")
        case.apply_intake(c.parse_intake_answer(self._answer("Состояние: Отказать")))
        self.assertEqual(case.status, c.STATUS_REFUSED)
        self.assertEqual(case.status_label(), "Отказано")


class V43DefaultDocTypeTests(unittest.TestCase):
    """v43: «Вид решения» по умолчанию всегда «Определит ИИ по обращению»."""

    def test_default_value_constant(self):
        self.assertEqual(gdata.DEFAULT_DOC_TYPE, "Определит ИИ по обращению")
        self.assertEqual(gdata.DEFAULT_DOC_TYPE, gdata.GOV_DOC_TYPE_AUTO_APPEAL)

    def test_first_choice_is_default_on_every_stage(self):
        for stage in (gdata.GOV_STAGE_PRIMARY, gdata.GOV_STAGE_FINAL):
            self.assertEqual(gdata.type_choices(stage)[0], gdata.DEFAULT_DOC_TYPE)

    def test_concrete_types_stay_available(self):
        primary = gdata.type_choices(gdata.GOV_STAGE_PRIMARY)
        final = gdata.type_choices(gdata.GOV_STAGE_FINAL)
        self.assertEqual(primary[1:], list(gdata.GOV_PRIMARY_TYPES))
        self.assertIn(gdata.GOV_DOC_TYPE_AUTO, final)      # «по обстоятельствам»
        for kind in gdata.GOV_FINAL_TYPES:
            self.assertIn(kind, final)
        self.assertNotIn("О привлечении к административной ответственности", primary)

    def test_form_defaults_to_the_constant(self):
        src = (_P("postanovlenie_prokuror_ui.py")).read_text(encoding="utf-8")
        block = src[src.index("def _fill_types"):]
        block = block[:block.index("def _on_stage_changed")]
        self.assertIn("gdata.DEFAULT_DOC_TYPE", block)


class V43StageStabilityTests(unittest.TestCase):
    """v43: после «Проверки ИИ» и «Сформировать» этап не переключается сам."""

    def setUp(self):
        import gov_case as c
        self.c = c

    def test_stage_number_is_fixed_per_kind(self):
        case = self.c.GovCase(number="1", appeal_raw="обращение")
        self.assertEqual(case.stage_number(self.c.STAGE_INTAKE), 1)
        self.assertEqual(case.stage_number(self.c.STAGE_MEASURE), 2)

    def test_stage_number_does_not_change_after_intake(self):
        case = self.c.GovCase(number="1", appeal_raw="обращение")
        before = case.stage_number(self.c.STAGE_INTAKE)
        case.apply_intake(self.c.parse_intake_answer(
            "===ПРОВЕРКА ОБРАЩЕНИЯ===\nСостояние: Принято\n===КОНЕЦ ПРОВЕРКИ==="))
        self.assertEqual(case.stage_number(self.c.STAGE_INTAKE), before)
        self.assertEqual(before, 1)

    def test_stage_number_stable_after_forming_document(self):
        case = self.c.GovCase(number="1", appeal_raw="обращение")
        case.mark_stage(self.c.STAGE_INTAKE, doc_number="AG-1")
        self.assertEqual(case.stage_number(self.c.STAGE_MEASURE), 2)
        case.mark_stage(self.c.STAGE_MEASURE, doc_number="AG-2")
        self.assertEqual(case.stage_number(self.c.STAGE_MEASURE), 2)
        self.assertEqual(case.stage_number(self.c.STAGE_INTAKE), 1)

    def test_extra_stage_gets_its_own_number(self):
        case = self.c.GovCase(number="1", appeal_raw="обращение")
        case.add_stage(self.c.STAGE_RECHECK)
        self.assertEqual(case.stage_number(self.c.STAGE_INTAKE), 1)
        self.assertEqual(case.stage_number(self.c.STAGE_MEASURE), 2)
        self.assertEqual(case.stage_number(self.c.STAGE_RECHECK), 3)

    def test_files_of_different_stages_do_not_clash(self):
        case = self.c.GovCase(number="1", appeal_raw="обращение")
        first = self.c.stage_stem("AG-1", case.stage_number(self.c.STAGE_INTAKE),
                                  self.c.STAGE_INTAKE)
        second = self.c.stage_stem("AG-1", case.stage_number(self.c.STAGE_MEASURE),
                                   self.c.STAGE_MEASURE)
        self.assertEqual(first, "AG-1 — эт.1 принятие")
        self.assertEqual(second, "AG-1 — эт.2 санкция")
        self.assertNotEqual(first, second)

    def test_open_stage_kind_is_first_unfinished(self):
        case = self.c.GovCase(number="1", appeal_raw="обращение")
        self.assertEqual(case.open_stage_kind(), self.c.STAGE_INTAKE)
        case.apply_intake(self.c.parse_intake_answer(
            "===ПРОВЕРКА ОБРАЩЕНИЯ===\nСостояние: Принято\n===КОНЕЦ ПРОВЕРКИ==="))
        self.assertEqual(case.open_stage_kind(), self.c.STAGE_MEASURE)

    def test_form_selects_stage_only_when_opening_case(self):
        src = (_P("postanovlenie_prokuror_ui.py")).read_text(encoding="utf-8")
        block = src[src.index("def open_case("):]
        block = block[:block.index("def _collect_case")]
        # v43.2: этап берётся у дела, но для статуса «Отказано» — всегда этап 1
        # (документ об отказе печатается как первичное решение).
        self.assertIn("_select_stage(cases.primary_stage_kind(case))", block)
        # в применении ответа ИИ этап не выбирается
        intake = src[src.index("def apply_intake_answer("):]
        intake = intake[:intake.index("def _open_case_dialog")]
        self.assertNotIn("_select_stage", intake)
        self.assertNotIn("setCurrentIndex(", intake.replace("tabs.setCurrentIndex", "tabs."))

    def test_form_stage_index_uses_case_numbering(self):
        src = (_P("postanovlenie_prokuror_ui.py")).read_text(encoding="utf-8")
        block = src[src.index("def _stage_index"):]
        block = block[:block.index("def _select_stage")]
        self.assertIn("stage_number", block)
        self.assertNotIn('len([s for s in self.case.stages if s.get("done")]) + 1', block)


class V43FormGateTests(unittest.TestCase):
    """v43: без «Проверки ИИ» кнопка «Сформировать» недоступна."""

    def setUp(self):
        import gov_case as c
        self.c = c

    def test_new_case_is_not_checked(self):
        case = self.c.GovCase(number="1", appeal_raw="обращение")
        self.assertFalse(case.intake_checked())

    def test_checked_after_intake_answer(self):
        case = self.c.GovCase(number="1", appeal_raw="обращение")
        case.apply_intake(self.c.parse_intake_answer(
            "===ПРОВЕРКА ОБРАЩЕНИЯ===\nСостояние: Отказать\n===КОНЕЦ ПРОВЕРКИ==="))
        self.assertTrue(case.intake_checked())          # отказ — тоже проверка

    def test_checked_after_stage_one_document(self):
        case = self.c.GovCase(number="1", appeal_raw="обращение")
        case.mark_stage(self.c.STAGE_INTAKE, doc_number="AG-1", accepted=True)
        self.assertTrue(case.intake_checked())

    def test_button_disabled_until_checked_in_source(self):
        src = (_P("postanovlenie_prokuror_ui.py")).read_text(encoding="utf-8")
        self.assertIn("setEnabled(checked)", src)
        self.assertIn("self.case.intake_checked()", src)
        # и в сборке документа проверка тоже требуется
        block = src[src.index("def _form("):]
        block = block[:block.index("def showEvent")]
        self.assertIn("intake_checked", block)


class V43CasesDialogTests(unittest.TestCase):
    """v43: стартовое окно «Обращения» со списком дел и состояний."""

    APPEAL = ("От: гражданина штата Сан-Андреас Lordik Claw\n"
              "1. Данные о заявителе: Lordik Claw 83461\n"
              "5. Подробное описание ситуации: Сотрудник FIB не пресёк драку.\n")

    def setUp(self):
        import gov_case as c
        self.c = c

    def test_rows_have_status_and_progress(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            store = self.c.GovCaseStore(tmp)
            case = store.open_or_create(self.APPEAL)
            rows = self.c.case_rows(store)
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["number"], "1")
            self.assertEqual(row["status"], "Новое")
            self.assertIn("Lordik Claw", row["title"])
            self.assertIn("проверка ИИ не выполнена", row["progress"])

    def test_status_changes_after_intake(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            store = self.c.GovCaseStore(tmp)
            case = store.open_or_create(self.APPEAL)
            case.apply_intake(self.c.parse_intake_answer(
                "===ПРОВЕРКА ОБРАЩЕНИЯ===\nСостояние: Отказать\n"
                "Основание: пункт 13 части 1 статьи 16\n===КОНЕЦ ПРОВЕРКИ==="))
            store.save(case)
            row = self.c.case_rows(store)[0]
            self.assertEqual(row["status"], "Отказано")
            self.assertEqual(row["status_key"], self.c.STATUS_REFUSED)
            self.assertIn("16", row["title"] + row["progress"] + case.intake_basis)

    def test_progress_shows_next_stage(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            store = self.c.GovCaseStore(tmp)
            case = store.open_or_create(self.APPEAL)
            case.apply_intake(self.c.parse_intake_answer(
                "===ПРОВЕРКА ОБРАЩЕНИЯ===\nСостояние: Принято\n===КОНЕЦ ПРОВЕРКИ==="))
            store.save(case)
            progress = self.c.case_rows(store)[0]["progress"]
            self.assertIn("следующий", progress)
            self.assertIn("Этап 2", progress)     # после принятия — санкция

    def test_new_cases_sorted_newest_first(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            store = self.c.GovCaseStore(tmp)
            first = store.open_or_create(self.APPEAL)
            first.updated_at = 1000.0
            store.save(first)
            second = store.open_or_create(self.APPEAL + "Другое обращение\n")
            rows = self.c.case_rows(store)
            self.assertEqual([row["number"] for row in rows], [second.number, "1"])

    def test_start_dialog_exists_and_is_used(self):
        ui = (_P("postanovlenie_prokuror_ui.py")).read_text(encoding="utf-8")
        main = (_P("main.py")).read_text(encoding="utf-8")
        self.assertIn("class GovCasesDialog", ui)
        self.assertIn("Открыть", ui)
        self.assertIn("Новое обращение", ui)
        self.assertIn("_choose_gov_case", main)
        self.assertIn("GovCasesDialog", main)
        # список открывается сам при первом показе формы, до всякой работы
        show = ui[ui.index("def showEvent"):]
        self.assertIn("_open_case_dialog", show[:show.index("def mousePressEvent")])
        self.assertIn("ask_case_on_show", ui)


class V43StageMemoryTests(unittest.TestCase):
    """v43: выбранный прокурором этап запоминается в деле и не «прыгает»."""

    def setUp(self):
        import gov_case as c
        self.c = c

    def test_chosen_stage_reopens_as_is(self):
        case = self.c.GovCase(number="1", appeal_raw="обращение")
        case.apply_intake(self.c.parse_intake_answer(
            "===ПРОВЕРКА ОБРАЩЕНИЯ===\nСостояние: Принято\n===КОНЕЦ ПРОВЕРКИ==="))
        self.assertEqual(case.open_stage_kind(), self.c.STAGE_MEASURE)
        case.set_stage_kind(self.c.STAGE_INTAKE)      # прокурор вернулся на этап 1
        self.assertEqual(case.open_stage_kind(), self.c.STAGE_INTAKE)

    def test_stage_survives_saving_and_reloading(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            store = self.c.GovCaseStore(tmp)
            case = store.open_or_create("обращение")
            case.apply_intake(self.c.parse_intake_answer(
                "===ПРОВЕРКА ОБРАЩЕНИЯ===\nСостояние: Принято\n===КОНЕЦ ПРОВЕРКИ==="))
            case.set_stage_kind(self.c.STAGE_RECHECK)
            store.save(case)
            again = store.load(case.number)
            self.assertEqual(again.stage_kind, self.c.STAGE_RECHECK)
            self.assertEqual(again.open_stage_kind(), self.c.STAGE_RECHECK)
            self.assertEqual(again.stage_number(self.c.STAGE_RECHECK), 3)

    def test_forming_document_does_not_switch_stage(self):
        case = self.c.GovCase(number="1", appeal_raw="обращение")
        case.apply_intake(self.c.parse_intake_answer(
            "===ПРОВЕРКА ОБРАЩЕНИЯ===\nСостояние: Принято\n===КОНЕЦ ПРОВЕРКИ==="))
        case.set_stage_kind(self.c.STAGE_INTAKE)
        case.mark_stage(self.c.STAGE_INTAKE, doc_number="AG-1", accepted=True)
        self.assertEqual(case.open_stage_kind(), self.c.STAGE_INTAKE)

    def test_unknown_stage_is_ignored(self):
        case = self.c.GovCase(number="1", appeal_raw="обращение")
        case.set_stage_kind("неизвестный")
        self.assertEqual(case.stage_kind, "")
        self.assertEqual(case.open_stage_kind(), self.c.STAGE_INTAKE)

    def test_form_saves_stage_choice(self):
        src = (_P("postanovlenie_prokuror_ui.py")).read_text(encoding="utf-8")
        self.assertIn("set_stage_kind", src)
        self.assertIn("self.case.stage_kind = self._stage_kind()", src)


class V43RenderFolderTests(unittest.TestCase):
    """v43: PNG этапа попадает в папку своего этапа, номера совпадают."""

    def setUp(self):
        import gov_case as c
        self.c = c

    def test_form_folder_and_main_folder_match(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            case = self.c.GovCase(number="1", appeal_raw="обращение")
            case.apply_intake(self.c.parse_intake_answer(
                "===ПРОВЕРКА ОБРАЩЕНИЯ===\nСостояние: Принято\n===КОНЕЦ ПРОВЕРКИ==="))
            case.mark_stage(self.c.STAGE_INTAKE, doc_number="AG-1", accepted=True)
            # так считает форма перед отправкой заявки
            form_index = case.stage_number(self.c.STAGE_MEASURE)
            # и так считает main после ответа ИИ
            main_index = case.stage_number(self.c.STAGE_MEASURE)
            self.assertEqual(form_index, main_index)
            self.assertEqual(
                self.c.stage_folder(tmp, "AG-1", form_index, self.c.STAGE_MEASURE),
                self.c.stage_folder(tmp, "AG-1", main_index, self.c.STAGE_MEASURE))
            # и это не папка принятия
            self.assertNotEqual(
                self.c.stage_folder(tmp, "AG-1", 1, self.c.STAGE_INTAKE),
                self.c.stage_folder(tmp, "AG-1", form_index, self.c.STAGE_MEASURE))

    def test_both_pngs_survive_side_by_side(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            case = self.c.GovCase(number="1", appeal_raw="обращение")
            doc = gdata.PostanovlenieProkurora(doc_number="AG-1")
            first = self.c.stage_artifacts(tmp, case, self.c.STAGE_INTAKE, 1, doc, "1")
            second = self.c.stage_artifacts(tmp, case, self.c.STAGE_MEASURE, 2, doc, "2")
            first["document"].write_text("этап 1", encoding="utf-8")
            second["document"].write_text("этап 2", encoding="utf-8")
            self.assertEqual(first["document"].parent,
                             self.c.stage_folder(tmp, "AG-1", 1, self.c.STAGE_INTAKE))
            self.assertEqual(second["document"].parent,
                             self.c.stage_folder(tmp, "AG-1", 2, self.c.STAGE_MEASURE))
            self.assertNotEqual(first["document"].parent,
                                second["document"].parent)


class V43FactsOnlyWhenNeededTests(unittest.TestCase):
    """v43: на этапе принятия «Обстоятельства» в бланк не попадают."""

    def test_facts_gate_in_form(self):
        src = (_P("postanovlenie_prokuror_ui.py")).read_text(encoding="utf-8")
        block = src[src.index("def _collect(self)"):]
        block = block[:block.index("def set_document")]
        self.assertIn('stage_meta(kind_now)["needs_facts"]', block)
        self.assertIn("self.case.has_facts()", block)

    def test_stage_meta_flags(self):
        import gov_case as c
        for kind in (c.STAGE_INTAKE, c.STAGE_REDIRECT, c.STAGE_CLOSE):
            self.assertFalse(c.stage_meta(kind)["needs_facts"], kind)
        for kind in (c.STAGE_MEASURE, c.STAGE_ADDITIONAL, c.STAGE_RECHECK,
                     c.STAGE_APPEAL):
            self.assertTrue(c.stage_meta(kind)["needs_facts"], kind)
