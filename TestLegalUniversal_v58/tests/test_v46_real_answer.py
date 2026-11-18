"""v46: постановление собирается только из документа, без лишних реквизитов.

Замечание пользователя (папка «Постановление_46v»): «текст распознаёт, но с
ошибками… сделай корректную сборку Постановления без лишних реквизитов, только
с теми, что должны там быть» и «во время исполнения функции «Проверка ИИ» после
того как он проверил и ответ был корректно получен, обнови браузер. А то он
завис на «Обращение №1: Отказано (…)»».

Что было не так в выгрузке (файл `AG-1/ИИ-ответ.txt`, 36 307 символов):

1. в «УСТАНОВИЛ» попадал НАШ промт («Обращение уже рассмотрено…», «ОСНОВАНИЕ
   ОТКАЗА…», «ЭТАП 1 — ПЕРВИЧНОЕ РЕШЕНИЕ…») и цитата заявки;
2. в «ПОСТАНОВИЛ» попадали пункты формы обращения («1. Данные о заявителе…»);
3. четыре решения ИИ без номеров склеивались в ОДИН пункт;
4. «Основание:» печаталось с повтором одной и той же статьи;
5. в тексте оставались пустые «Отдел сотрудника:», «Взыскание:» и выдуманное
   «Место события: в месте проведения процессуальных действий»;
6. подпись ИИ «J. Raine» могла подменить автора «Jonathan Raine».

Причина всех пунктов одна: `extract_document_block` не находил заголовок
документа (после «ПОСТАНОВЛЕНИЕ» сразу идут «Номер:», «Дата:», «Вид
документа:», без «Ведомство:»), поэтому в разбор уходило окно 7000 символов от
начала ответа — вместе с промтом и цитатой заявки.
"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import doc_render as prender  # noqa: E402
import postanovlenie_data as pdoc  # noqa: E402
import postanovlenie_prokuror_data as pgov  # noqa: E402

#: Настоящий ответ ИИ из «Постановление_46v» (длинные абзацы не сокращены:
#: именно их границы и проверяются). Начало — как в файле: вложения чата,
#: наш промт, цитата заявки, затем сам документ.
REAL_ANSWER = """Memphis.pdf
PDF
Шаблон.txt
File
Заявка (отправлено в чат).txt
File
ЭТАП: первичное решение по обращению (документ об отказе).
Обращение уже рассмотрено: в принятии обращения отказано. Оформи это решение постановлением Прокурора — вид решения «Об отказе в принятии обращения к производству» с номером конкретного основания из части 1 статьи 16 Регламента подачи обращений.
НЕ назначай санкцию, не возбуждай уголовное дело и не выписывай штраф: отказ в принятии — это первичное решение, а не наказание.
ОСНОВАНИЕ ОТКАЗА (по проверке обращения): пункт 14 части 1 статьи 16 Регламента подачи обращений в Офис Генерального прокурора штата Сан-Андреас
ОТВЕТ ИИ ПО ПРОВЕРКЕ ОБРАЩЕНИЯ (для сверки, дословно):
ПРОВЕРКА ОБРАЩЕНИЯ
Состояние: Отказано
ЗАЯВКА НА ПОСТАНОВЛЕНИЕ ПРОКУРОРА
Данные для подготовки документа:
Этап: 1. Первичное решение по обращению (до 24 часов, ст. 12 Регламента)
Ведомство: STATE OF SAN-ANDREAS
Подразделение: OFFICE OF THE ATTORNEY GENERAL
Вид документа: О принятии обращения к собственному производству
Номер: AG-1
Дата: 09.11.2026
Постановление вынес: Jonathan Raine
Должность: Прокурор
Удостоверение прокурора: 198632
Город: г. Лос-Сантос
ЭТАП 1 — ПЕРВИЧНОЕ РЕШЕНИЕ (принятие, отказ, переадресация, прекращение). Оцени ТОЛЬКО само обращение и его форму, санкцию не назначай и уголовное дело не возбуждай.
ОБРАЩЕНИЕ ГРАЖДАНИНА (текст целиком, форма ОГП):
1. Данные о заявителе: Sae Gucci, 12361
3. Сведения о нарушителе: 33400, FIB, Manual Killaura
5. Подробное описание ситуации: Я стоял, никого не трогал, ко мне подбегает шериф.
6. Ксерокопия идентификационной карты или других документов, удостоверяющих личность заявителя: паспорт
7. Электронную почту и номер телефона: _с418__@ds.com, 17993442
8. Доказательства правонарушения: выписка, видеофиксацию запросите у сотрудника.
Ответ в режиме ИИ, исходные файлы: 3 файлов
ПОСТАНОВЛЕНИЕ
Номер: AG-1
Дата: 09.11.2026
Вид документа: Об отказе в принятии обращения к рассмотрению
Постановление вынес: Jonathan Raine
Должность: Прокурор штата Сан-Андреас
Удостоверение прокурора: 198632
Город: г. Лос-Сантос
Заявитель: Sae Gucci
ID заявителя: 12361
Дата обращения: 06.11.2026
Предмет обращения: заявление о преступлении, совершённом должностным лицом при исполнении либо в связи с исполнением служебных обязанностей
В отношении: Manual Killaura
Удостоверение: 33400
Отдел сотрудника:
Дата и время события: 05.11.2026, 19:19-20:01
Место события: в месте проведения процессуальных действий
Нарушенный акт: Регламент подачи обращений в Офис Генерального прокурора штата Сан-Андреас
Пункт нарушения: пункт 14 части 1 статьи 16
Нормативное основание: часть 1 статьи 16 Регламента подачи обращений в Офис Генерального прокурора штата Сан-Андреас; статьи 27–28 Регламента подачи обращений в Офис Генерального прокурора штата Сан-Андреас
Взыскание:
Обстоятельства: При проведении предварительного изучения обращения гражданина Sae Gucci от 06.11.2026 установлено, что в тексте обращения и приложениях к нему отсутствуют объективные доказательства правонарушения, так как заявителем не представлены видеоматериалы или иные допустимые файлы фиксации. На основании изложенного, обращение не может быть принято к производству ввиду несоответствия установленным требованиям Регламента.
УСТАНОВИЛ:
В Офис Генерального прокурора штата Сан-Андреас поступило обращение от гражданина Sae Gucci от 06.11.2026 в отношении сотрудника FIB Manual Killaura с опознавательным знаком 33400. Согласно пункту 14 части 1 статьи 16 Регламента подачи обращений в Офис Генерального прокурора, отсутствие доказательств в обращении является императивным основанием для отказа в принятии обращения к рассмотрению.
ПОСТАНОВИЛ:
Отказать в принятии обращения гражданина Sae Gucci от 06.11.2026 к рассмотрению Офисом Генерального прокурора штата Сан-Андреас на основании пункта 14 части 1 статьи 16 Регламента подачи обращений.
Прекратить прокурорское производство по настоящему обращению в Офисе Генерального прокурора штата Сан-Андреас.
Разъяснить заявителю, что настоящее решение может быть обжаловано вышестоящему прокурору либо в суд в срок, не превышающий 72 часа с момента публикации, в соответствии со статьями 27–28 Регламента подачи обращений.
Настоящее постановление вступает в законную силу немедленно с момента его подписания и публикации.
J. Raine
Прокурор штата Сан-Андреас
Удостоверение № 198632
КОНЕЦ ПОСТАНОВЛЕНИЯ
===КОНЕЦ ОТВЕТА===
Перейти к предыдущему запросу
Mem
phis.pdf
Ша
блон.txt
"""

#: Тот же ответ, но с пунктами формы обращения внутри «ПОСТАНОВИЛ» — так
#: пришло в реальной выгрузке (v45 склеила их с резолюцией).
ANSWER_WITH_FORM_POINTS = REAL_ANSWER.replace(
    "ПОСТАНОВИЛ:\nОтказать",
    "ПОСТАНОВИЛ:\n"
    "1. Данные о заявителе: Sae Gucci, 12361\n"
    "2. Сведения о нарушителе: 33400, FIB, Manual Killaura\n"
    "3. Подробное описание ситуации: Я стоял, никого не трогал.\n"
    "4. Ксерокопия идентификационной карты или других документов: паспорт\n"
    "5. Электронную почту и номер телефона: с418@ds.com, 17993442\n"
    "6. Доказательства правонарушения: выписка, видеофиксацию запросите у сотрудника.\n"
    "Отказать")


class DocumentBlockTests(unittest.TestCase):
    """Блок документа начинается с самого документа, а не с промта."""

    def test_heading_found_without_vedomstvo(self):
        raw = REAL_ANSWER
        self.assertGreater(pdoc._document_body_start(raw), 0)

    def test_block_starts_at_postanovlenie(self):
        block = pdoc.extract_document_block(REAL_ANSWER).strip()
        self.assertTrue(block.startswith("ПОСТАНОВЛЕНИЕ"), block[:60])
        self.assertNotIn("Данные для подготовки документа", block)
        self.assertNotIn("ЭТАП 1 — ПЕРВИЧНОЕ РЕШЕНИЕ", block)

    def test_prompt_lines_are_not_document_text(self):
        for line in ("Обращение уже рассмотрено: в принятии отказано. Оформи это "
                     "решение постановлением Прокурора",
                     "ОСНОВАНИЕ ОТКАЗА (по проверке обращения): пункт 14",
                     "ОБРАЩЕНИЕ ГРАЖДАНИНА (текст целиком, форма ОГП): От:",
                     "ЭТАП 1 — ПЕРВИЧНОЕ РЕШЕНИЕ (принятие, отказ)"):
            self.assertTrue(pdoc.is_chat_instruction(line), line[:40])


#: Настоящий ответ из папки «Постановление_46v» (лежит рядом с версией).
REAL_FILE = (ROOT.parent / "Постановление_46v" / "AG-1" / "ИИ-ответ.txt")


def real_answer() -> str:
    """Ответ из выгрузки, если папка с ней есть; иначе — копия выше."""
    try:
        return REAL_FILE.read_text(encoding="utf-8")
    except Exception:
        return REAL_ANSWER


class ParsedDocumentTests(unittest.TestCase):
    """Реквизиты и разделы — только те, что есть в документе."""

    def setUp(self):
        self.raw = real_answer()
        self.doc = pgov.parse_gov_answer(self.raw)

    def test_requisites(self):
        doc = self.doc
        self.assertIsNotNone(doc)
        self.assertEqual(doc.get("doc_number"), "AG-1")
        self.assertEqual(doc.get("doc_date"), "09.11.2026")
        self.assertEqual(doc.get("author_name"), "Jonathan Raine")
        self.assertEqual(doc.get("author_id"), "198632")
        self.assertEqual(doc.get("applicant_name"), "Sae Gucci")
        self.assertEqual(doc.get("applicant_id"), "12361")
        self.assertEqual(doc.get("subject_name"), "Manual Killaura")
        self.assertIn("отказе в принятии", doc.get("doc_type").lower())

    def test_ustanovil_has_only_the_document_paragraph(self):
        facts = self.doc.facts_text()
        self.assertIn("В Офис Генерального прокурора", facts)
        self.assertIn("императивным основанием", facts)
        for garbage in ("Оформи это решение постановлением",
                        "ОСНОВАНИЕ ОТКАЗА",
                        "ЭТАП 1 — ПЕРВИЧНОЕ РЕШЕНИЕ",
                        "Данные для подготовки документа",
                        "ЗАЯВКА НА ПОСТАНОВЛЕНИЕ ПРОКУРОРА",
                        "Ответ ИИ по проверке обращения"):
            self.assertNotIn(garbage, facts)
        if self.raw != REAL_ANSWER:
            # В настоящей выгрузке ИИ продублировал пересказ в «Обстоятельствах»
            # и в «УСТАНОВИЛ» (пересечение слов 38 %) — печатается один абзац.
            self.assertNotIn("При проведении предварительного изучения", facts)

    def test_resolution_has_four_points_without_form_items(self):
        points = self.doc.points
        self.assertEqual(len(points), 4, points)
        self.assertTrue(points[0].startswith("Отказать в принятии"))
        self.assertTrue(points[1].startswith("Прекратить"))
        self.assertTrue(points[2].startswith("Разъяснить"))
        self.assertTrue(points[3].startswith("Настоящее постановление"))
        for point in points:
            self.assertNotIn("Данные о заявителе", point)
            self.assertNotIn("Ксерокопия", point)
            self.assertNotIn("Доказательства правонарушения", point)

    def test_form_points_are_dropped_in_any_position(self):
        doc = pgov.parse_gov_answer(ANSWER_WITH_FORM_POINTS)
        self.assertTrue(doc.points)
        for point in doc.points:
            self.assertFalse(pgov.is_appeal_form_point(point), point[:60])
        self.assertTrue(any(p.startswith("Отказать") for p in doc.points))

    def test_basis_has_no_repeated_article(self):
        basis = self.doc.basis_text()
        self.assertEqual(basis.count("части 1 статьи 16"), 1, basis)
        self.assertIn("пункт 14 части 1 статьи 16", basis)
        self.assertIn("27–28", basis)

    def test_placeholders_are_not_printed(self):
        doc = self.doc
        self.assertEqual(doc.get("event_place"), "")
        self.assertEqual(doc.get("subject_department"), "")
        self.assertEqual(doc.get("penalty"), "")
        text = doc.to_document_txt()
        self.assertNotIn("проведения процессуальных действий", text)
        self.assertNotIn("Взыскание:", text)
        self.assertNotIn("Отдел сотрудника:", text)

    def test_signature_is_full_name(self):
        self.assertEqual(self.doc.signature(), "J. Raine")
        self.assertEqual(self.doc.get("author_name"), "Jonathan Raine")

    def test_checks_are_clean(self):
        self.assertEqual(list(pgov.check_gov_document(self.doc)), [])

    def test_png_is_rendered(self):
        out = Path(tempfile.mkdtemp())
        pages = prender.render_document(self.doc, pgov.gov_assets(ROOT))
        paths = prender.save_pages(pages, out, "AG-1")
        self.assertTrue(paths)
        for path in paths:
            self.assertTrue(Path(path).exists())
            self.assertGreater(Path(path).stat().st_size, 10_000)


class IntakeRefreshTests(unittest.TestCase):
    """После корректного ответа «Проверки ИИ» окно обновляется (v46)."""

    def setUp(self):
        self.src = (ROOT / "main.py").read_text(encoding="utf-8")

    def test_refresh_called_right_after_answer_is_saved(self):
        block = self.src[self.src.index("def _apply_gov_intake"):
                          self.src.index("def _gov_intake_watch_stop")]
        self.assertIn("self._refresh_after_intake(case)", block)
        self.assertLess(block.index('"gov.intake.result"'),
                        block.index("self._refresh_after_intake(case)"))

    def test_browser_is_reloaded_through_known_path(self):
        block = self.src[self.src.index("def _reload_chat_after_intake"):
                          self.src.index("def _gov_intake_watch_stop")]
        # Тот же путь, что у штатного восстановления страницы.
        self.assertIn("self._recover_web()", block)

    def test_cases_list_is_reloaded(self):
        block = self.src[self.src.index("def _refresh_after_intake"):
                          self.src.index("def _reload_chat_after_intake")]
        self.assertIn("dialog.reload()", block)
        ui = (ROOT / "postanovlenie_prokuror_ui.py").read_text(encoding="utf-8")
        self.assertIn("def reload(self)", ui)

    def test_reload_not_started_with_pending_request(self):
        block = self.src[self.src.index("def _reload_chat_after_intake"):
                          self.src.index("def _gov_intake_watch_stop")]
        self.assertIn("pending_send", block)


if __name__ == "__main__":
    unittest.main()
