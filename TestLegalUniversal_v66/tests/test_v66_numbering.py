"""Тесты v66: нумерация постановлений, папки по этапам, архив версий,
очередь заявок и автозапрос документа.

Замечания пользователя по выгрузке «Постановление_65v.7z» (подробно —
`ИСПРАВЛЕНИЯ_v66.md`):

* дело не должно останавливаться на первом состоянии, дописанные цепочки
  не теряются, эхо заявки не принимается за ответ;
* «Принято» без цепочек — явный ручной повтор, а не сообщение про этап 3;
* при отказе и переадресации постановление этапа 1 всё равно запрашивается;
* на каждый узел ровно один автозапрос документа, без петель и дублей;
* пересекающиеся заявки сериализуются, запись `pending_send` не затирается;
* обновлённые поля → новая заявка, старый комплект → `Архив/Версия-…`;
* номер вида `0001-1 … 0001-4` одинаков в форме, заявке, PNG, данных дела
  и в именах файлов и папок.

Тесты чистые (без Qt): раскладка `doc_layout`, нумерация `gov_case`,
очередь `pending_send`, заголовки заявок, сохранение страниц, версия.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import doc_layout as dl
import gov_case as cases
import ogp_chain
import ogp_stages
import pending_send as psend
import postanovlenie_data as pdoc

ROOT = Path(__file__).resolve().parent.parent


def _src(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


PLAN_TWO = """===ЦЕПОЧКИ ЭТАПА 2===
===ЦЕПОЧКА A: быстрый путь===
ЗАЧЕМ: доказательств достаточно, санкция сразу
1. Об истребовании информации — установить владельца номерного знака
2. О наложении штрафной санкции — привлечь к ответственности
===КОНЕЦ ЦЕПОЧЕК==="""

PLAN_THREE = """===ЦЕПОЧКИ ЭТАПА 2===
===ЦЕПОЧКА A: быстрый путь===
ЗАЧЕМ: санкция сразу
1. Об истребовании информации — установить владельца
2. О наложении штрафной санкции — привлечь к ответственности
===ЦЕПОЧКА B: полный путь===
ЗАЧЕМ: сначала уголовное дело
1. О возбуждении уголовного дела по обращению — открыть производство
2. О вызове на допрос — получить объяснения сотрудника
3. О завершении уголовного дела и передаче его в окружной суд — передать дело
===КОНЕЦ ЦЕПОЧЕК==="""

APPEAL = "Обращение гражданина: нарушение при изъятии боеприпасов."


def _accepted(plan: str = PLAN_TWO, base: str = "0001") -> cases.GovCase:
    """Дело v66: принято, цепочки разобраны, общий номер назначен."""
    case = cases.GovCase(number="1", appeal_raw=APPEAL)
    case.numbering = dl.NEW_NUMBERING
    case.doc_base = base
    case.set_outcome(ogp_chain.OUTCOME_ACCEPT)   # как в живом разборе этапа 1
    case.apply_chains(ogp_stages.split_chains(plan))
    return case


class NumberingModelTests(unittest.TestCase):
    """Критерий 15: номер `0001-2` — общий номер обращения + номер этапа."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = cases.GovCaseStore(Path(self.tmp.name))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_new_case_gets_v66_numbering(self):
        case = self.store.open_or_create(APPEAL)
        self.assertTrue(case.new_numbering())
        self.assertEqual(case.numbering, dl.NEW_NUMBERING)
        self.assertEqual(len(case.doc_base), dl.BASE_MIN_WIDTH)
        self.assertTrue(dl.is_base(case.doc_base))

    def test_old_case_keeps_legacy_number(self):
        case = cases.GovCase(number="AG-1")
        self.assertFalse(case.new_numbering())
        self.assertEqual(case.doc_base, "")
        self.assertEqual(case.doc_number_for("1"), "")
        self.assertFalse(dl.uses_new(case.numbering))

    def test_base_is_padded_to_four_digits(self):
        self.assertEqual(dl.normalize_base("1"), "0001")
        self.assertEqual(dl.normalize_base("0012"), "0012")
        self.assertEqual(dl.normalize_base("AG-1"), "")
        self.assertTrue(dl.is_base("0003"))
        self.assertTrue(dl.is_base("3"))       # цифры нормализуются до «0003»
        self.assertFalse(dl.is_base("AG-1"))   # старый номер — не база v66

    def test_suggest_base_skips_occupied(self):
        self.assertEqual(dl.suggest_base([]), "0001")
        self.assertEqual(dl.suggest_base(["0001"]), "0002")
        self.assertEqual(dl.suggest_base(["0001", "2", "AG-1"]), "0003")
        self.assertEqual(dl.suggest_base(["0001", "0002"]), "0003")
        self.assertFalse(dl.base_is_free("0001", ["0001"]))
        self.assertTrue(dl.base_is_free("0002", ["0001"]))

    def test_two_appeals_get_two_bases(self):
        first = self.store.open_or_create(APPEAL)
        second = self.store.open_or_create("Другое обращение: иное нарушение.")
        self.assertNotEqual(first.doc_base, second.doc_base)
        self.assertEqual(sorted(self.store.used_doc_bases()),
                         sorted([first.doc_base, second.doc_base]))
        self.assertEqual(dl.suggest_base(self.store.used_doc_bases()), "0003")

    def test_taken_base_is_not_silently_reused(self):
        """Решение `forbid`: занятым общим номером распорядиться нельзя."""
        case = self.store.open_or_create(APPEAL)
        case.doc_base = "0007"
        case.ensure_doc_base(["0007"])           # номер занят другим делом
        self.assertNotEqual(case.doc_base, "0007")
        case.ensure_doc_base(["0001", "0002"])   # заняты 0001 и 0002
        self.assertEqual(case.doc_base, "0003")
        case.ensure_doc_base(["0001", "0002"])   # идемпотентно
        self.assertEqual(case.doc_base, "0003")

    def test_own_base_is_excluded_by_case_number(self):
        """Свой номер себе не мешает, чужой с той же базой — мешает."""
        case = self.store.open_or_create(APPEAL)
        self.store.save(case)
        self.assertEqual(self.store.used_doc_bases(exclude=case.number), [])
        self.assertEqual(self.store.used_doc_bases(), [case.doc_base])
        other = self.store.open_or_create("Другое обращение: иное нарушение.")
        other.doc_base = case.doc_base           # конфликт, который был невидим
        self.store.save(other)
        taken = self.store.used_doc_bases(exclude=other.number)
        self.assertIn(case.doc_base, taken)
        other.ensure_doc_base(taken)
        self.assertNotEqual(other.doc_base, case.doc_base)

    def test_two_stage_plan_freezes_four_numbers(self):
        """План из двух подэтапов → `0001-1 … 0001-4` (критерий 15)."""
        case = _accepted(PLAN_TWO)
        self.assertEqual(case.doc_number_for("1"), "0001-1")
        self.assertEqual(case.doc_number_for("2.1"), "0001-2")
        self.assertEqual(case.doc_number_for("2.2"), "0001-3")
        self.assertEqual(case.doc_number_for("3"), "0001-4")
        table = case.doc_index_table()
        self.assertEqual(table.get("1"), 1)
        self.assertEqual(table.get("2.1"), 2)
        self.assertEqual(table.get("2.2"), 3)
        self.assertEqual(table.get("3"), 4)

    def test_repeated_stage_keeps_the_same_number(self):
        """При повторе номер сохраняется: индекс фиксируется один раз."""
        case = _accepted(PLAN_TWO)
        case.doc_number_for("1")
        case.doc_number_for("2.1")
        case.doc_number_for("2.2")
        case.doc_number_for("3")
        before = dict(case.doc_index)
        case.apply_chains(ogp_stages.split_chains(PLAN_TWO))
        self.assertEqual(case.doc_index, before)
        self.assertEqual(case.doc_number_for("2.1"), "0001-2")

    def test_late_substage_gets_a_fresh_number(self):
        """Подэтап, дописанный позже, получает следующий номер."""
        case = _accepted(PLAN_TWO)
        for kind in ("1", "2.1", "2.2", "3"):
            case.doc_number_for(kind)
        longer = PLAN_TWO.replace(
            "===КОНЕЦ ЦЕПОЧЕК===",
            "3. О вызове на допрос — получить объяснения сотрудника\n"
            "===КОНЕЦ ЦЕПОЧЕК===",
        )
        case.apply_chains(ogp_stages.split_chains(longer))
        self.assertEqual(case.doc_number_for("2.3"), "0001-5")

    def test_number_survives_roundtrip(self):
        case = _accepted(PLAN_THREE)
        self.store.save(case)
        again = self.store.load("1")
        self.assertIsNotNone(again)
        self.assertEqual(again.doc_base, "0001")
        self.assertEqual(again.doc_index, case.doc_index)
        self.assertEqual(again.doc_number_for("2.2"), "0001-3")
        self.assertTrue(again.new_numbering())

    def test_merge_save_does_not_wipe_the_number(self):
        """Критерий 16: поля формы не стирают номер и ответ узла."""
        case = _accepted(PLAN_TWO)
        case.set_work_node("2.1", answer="===ПОСТАНОВЛЕНИЕ===", done=True)
        self.store.save(case)
        updated = self.store.merge_save("1", intake_basis="ст. 14 Регламента",
                                        facts_video="видно номерной знак")
        self.assertIsNotNone(updated)
        self.assertEqual(updated.doc_number_for("2.1"), "0001-2")
        self.assertEqual(updated.intake_basis, "ст. 14 Регламента")
        self.assertEqual(updated.facts_video, "видно номерной знак")
        node = updated.active_chain()["nodes"]["2.1"]
        self.assertTrue(node["done"])
        self.assertIn("ПОСТАНОВЛЕНИЕ", node["answer"])


class FolderLayoutTests(unittest.TestCase):
    """Критерий 15: один номер в именах папок, файлов и страниц."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_node_folder_for_stage_one(self):
        case = _accepted(PLAN_TWO)
        path = cases.node_folder(self.root, case, "1")
        self.assertTrue(path.is_dir())
        self.assertTrue(path.name.startswith("Этап 1"))
        self.assertEqual(path.parent.name, "0001")
        self.assertIn("Обращение №1", path.parent.parent.name)

    def test_node_folder_for_chain_substage(self):
        case = _accepted(PLAN_THREE)
        path = cases.node_folder(self.root, case, "2.1")
        self.assertTrue(path.name.startswith("Этап 2.1"))
        self.assertEqual(path.parent.name, "Цепочка A")
        final = cases.node_folder(self.root, case, "3")
        self.assertTrue(final.name.startswith("Этап 3"))
        self.assertEqual(final.parent.name, "0001")

    def test_legacy_case_keeps_its_own_folder(self):
        """Старое дело v65 остаётся в `AG-1`: ничего не переименовываем."""
        case = cases.GovCase(number="AG-1", appeal_raw=APPEAL)
        case.outcome = ogp_chain.OUTCOME_ACCEPT
        case.apply_chains(ogp_stages.split_chains(PLAN_TWO))
        path = cases.node_folder(self.root, case, "2.1")
        self.assertIn("AG-1", path.as_posix())
        self.assertNotIn("Цепочка A", path.as_posix())
        self.assertNotIn("0001", path.as_posix())

    def test_stem_and_page_names_carry_the_number(self):
        case = _accepted(PLAN_TWO)
        stem = cases.node_stem(case, "2.1")
        self.assertTrue(stem.startswith("0001-2"), stem)
        self.assertIn("Этап 2.1", stem)
        self.assertEqual(dl.file_stem("0001-1", "1"), "0001-1 — Этап 1")
        self.assertEqual(dl.file_stem("0001-2", "2.1", "истребование"),
                         "0001-2 — Этап 2.1")
        self.assertEqual(dl.page_name(stem, 1), f"{stem} — стр. 1.png")
        self.assertEqual(dl.page_name(stem, 2), f"{stem} — стр. 2.png")

    def test_node_doc_number_and_step_label(self):
        case = _accepted(PLAN_TWO)
        self.assertEqual(cases.node_doc_number(case, "1"), "0001-1")
        self.assertEqual(cases.node_doc_number(case, "2.2"), "0001-3")
        self.assertEqual(cases.node_doc_number(case, "3"), "0001-4")
        self.assertEqual(cases.node_doc_number(case, "2.1", fallback="001"),
                         "0001-2")
        legacy = cases.GovCase(number="AG-1")
        self.assertEqual(cases.node_doc_number(legacy, "1", fallback="AG-1"),
                         "AG-1")
        self.assertTrue(cases.node_step_label(case, "2.1"))

    def test_archive_moves_the_whole_version(self):
        """Критерий 11: обновлённые поля → новый комплект, старый в архив."""
        case = _accepted(PLAN_TWO)
        stage = cases.node_folder(self.root, case, "2.1")
        stem = cases.node_stem(case, "2.1")
        (stage / f"{stem} — стр. 1.png").write_text("старая-1", encoding="utf-8")
        (stage / f"{stem} — стр. 2.png").write_text("старая-2", encoding="utf-8")
        (stage / "ИИ-документ.txt").write_text("старый документ", encoding="utf-8")

        version = dl.next_version_dir(stage)
        self.assertEqual(version.name, "Версия-001")
        self.assertTrue(dl.snapshot_current(stage, version_dir=version))
        moved = sorted(path.name for path in version.iterdir())
        self.assertEqual(moved, sorted([f"{stem} — стр. 1.png",
                                        f"{stem} — стр. 2.png",
                                        "ИИ-документ.txt"]))
        self.assertEqual([path.name for path in dl.existing_versions(stage)],
                         ["Версия-001"])
        self.assertEqual(dl.next_version_dir(stage).name, "Версия-002")
        self.assertFalse((stage / "ИИ-документ.txt").exists())
        self.assertEqual(dl.pages_leftover(stage, 3), [])

    def test_pages_leftover_finds_forgotten_pages(self):
        stage = self.root / "Этап 2.1 — истребование"
        stage.mkdir(parents=True)
        (stage / "0001-2 — Этап 2.1 — стр. 1.png").write_text("x", encoding="utf-8")
        (stage / "0001-2 — Этап 2.1 — стр. 2.png").write_text("x", encoding="utf-8")
        self.assertEqual(dl.pages_leftover(stage, 2), [])
        self.assertEqual(len(dl.pages_leftover(stage, 1)), 1)

    def test_save_pages_names_pages_with_the_number(self):
        try:
            from PIL import Image
            import doc_render
        except Exception as exc:                       # pragma: no cover
            self.skipTest(f"нет PIL: {exc}")
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            pages = [Image.new("RGB", (40, 60), "white"),
                     Image.new("RGB", (40, 60), "white")]
            written = doc_render.save_pages(pages, target,
                                            "0001-2 — Этап 2.1", numbered=True)
            self.assertEqual([path.name for path in written],
                             ["0001-2 — Этап 2.1 — стр. 1.png",
                              "0001-2 — Этап 2.1 — стр. 2.png"])
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            written = doc_render.save_pages([Image.new("RGB", (40, 60), "white")],
                                            target, "AG-1", numbered=False)
            self.assertEqual([path.name for path in written], ["AG-1.png"])


class RequestTests(unittest.TestCase):
    """Критерии 1 и 7: заявка узла несёт номер, контекст и ввод прокурора."""

    def setUp(self) -> None:
        self.case = _accepted(PLAN_THREE)

    def test_node_request_names_variant_and_number(self):
        text = cases.node_request(self.case, "2.2")
        self.assertIn("ЗАЯВКА НА ПОСТАНОВЛЕНИЕ ПО ОБРАЩЕНИЮ (этап 2.2)", text)
        self.assertIn("ВАРИАНТ ЦЕПОЧКИ: A", text)
        self.assertIn("НОМЕР ПОСТАНОВЛЕНИЯ: 0001-3", text)
        self.assertIn("ЗАЧЕМ ЭТОТ ПОДЭТАП", text)

    def test_document_request_names_the_substage(self):
        text = cases.node_document_request(self.case, "2.1")
        self.assertIn("ЗАЯВКА НА ПОСТАНОВЛЕНИЕ ПО ОБРАЩЕНИУЮ".replace("УЮ", "Ю"),
                      text)
        self.assertIn("(этап 2.1) — ДОКУМЕНТ", text)
        self.assertIn("НОМЕР ПОСТАНОВЛЕНИЯ: 0001-2", text)
        self.assertIn("ВАРИАНТ ЦЕПОЧКИ: A", text)
        self.assertIn("ОСТАЛОСЬ ПО ПЛАНУ", text)

    def test_document_request_lists_finished_substages(self):
        """Критерий 7: в заявке — актуальный ввод и контекст дела."""
        case = _accepted(PLAN_THREE)
        case.set_work_node("2.1", answer="===ПОСТАНОВЛЕНИЕ===\n1. Истребовать.",
                           done=True)
        text = cases.node_document_request(case, "2.2")
        self.assertIn("УЖЕ ВЫНЕСЕНО ПО ДЕЛУ", text)
        self.assertIn("Об истребовании информации", text)
        self.assertIn("НОМЕР ПОСТАНОВЛЕНИЯ: 0001-3", text)

    def test_stage_one_document_request(self):
        """Критерий 5: постановление этапа 1 запрашивается и при отказе."""
        accepted = cases.node_document_request(self.case, "1")
        self.assertIn("(этап 1)", accepted)
        self.assertIn("0001-1", accepted)
        refused = _accepted(PLAN_TWO)
        refused.set_outcome(ogp_chain.OUTCOME_REFUSE)
        refused.intake_basis = "п. 2 ч. 1 ст. 16 Регламента (обращение анонимно)"
        text = cases.node_document_request(refused, "1")
        self.assertIn("(этап 1)", text)
        self.assertIn("анонимно", text)
        self.assertIn("Санкцию не назначай", text)
        self.assertEqual(cases.primary_stage_kind(refused), cases.STAGE_INTAKE)

    def test_redirect_does_not_open_stage_two(self):
        """Критерий 5: переадресация закрывает этапы 2 и 3."""
        case = _accepted(PLAN_TWO)
        case.set_work_node("2.1", answer="===ПОСТАНОВЛЕНИЕ===", done=True)
        case.set_outcome(ogp_chain.OUTCOME_REDIRECT)
        self.assertFalse(case.can_measure())
        reason = cases.stage_block_reason(case, cases.STAGE_MEASURE)
        self.assertTrue(reason)
        self.assertFalse(cases.stage_is_ready(case, cases.STAGE_MEASURE, reason))
        self.assertFalse(case.auto_document_due("2.1"))
        self.assertFalse(case.auto_document_due("3"))
        # этап 1 при этом остаётся доступным: им и оформляется решение
        self.assertEqual(cases.stage_block_reason(case, cases.STAGE_INTAKE), "")

    def test_request_is_not_mistaken_for_answer(self):
        """Критерий 1: эхо собственной заявки не принимается за ответ ИИ."""
        for kind in ("1", "2.1", "2.2", "3"):
            request = cases.node_document_request(self.case, kind)
            self.assertTrue(pdoc.is_own_request(request, request),
                            f"этап {kind}: заявка не распознана (эхо)")
            self.assertTrue(pdoc.is_own_request(request, ""),
                            f"этап {kind}: заявка не распознана (без второго)")
        node_request = cases.node_request(self.case, "2.2")
        self.assertTrue(pdoc.is_own_request(node_request, node_request))
        self.assertTrue(pdoc.is_own_request(node_request, ""))

    def test_real_answer_is_still_an_answer(self):
        """Ответ ИИ с документом не принимается за заявку (правило v44)."""
        request = cases.node_document_request(self.case, "2.1")
        answer = (request + "\n\n===ПОСТАНОВЛЕНИЕ===\nНомер: 0001-2\n"
                  "Дата: 03.11.2026\nУСТАНОВИЛ:\nнарушение подтверждено\n"
                  "ПОСТАНОВИЛ:\n1. Истребовать сведения.\n"
                  "===КОНЕЦ ПОСТАНОВЛЕНИЯ===\n===КОНЕЦ ОТВЕТА===\n")
        self.assertFalse(pdoc.is_own_request(answer, request))

    def test_placeholder_has_no_answer_markers(self):
        """В заявке нет строк-маркеров ответа («УСТАНОВИЛ», «ПОСТАНОВИЛ»)."""
        placeholder = cases.doc_placeholder()
        for mark in ("УСТАНОВИЛ", "ПОСТАНОВИЛ", "КОНЕЦ ПОСТАНОВЛЕНИЯ",
                     "===КОНЕЦ ОТВЕТА==="):
            self.assertNotIn(mark, placeholder)

    def test_prosecutor_facts_are_carried(self):
        """Критерий 7: ввод прокурора уходит в заявку следующего узла."""
        case = _accepted(PLAN_TWO)
        case.facts_video = "видно номерной знак 123"
        text = cases.node_request(case, "2.2")
        self.assertIn("ОБСТОЯТЕЛЬСТВА ПРОКУРОРА", text)
        self.assertIn("видно номерной знак 123", text)
        document = cases.node_document_request(case, "2.1")
        self.assertIn("видно номерной знак 123", document)

    def test_ai_written_facts_are_not_echoed(self):
        """Правило v56: текст, записанный ИИ, обратно ему не отправляется."""
        case = _accepted(PLAN_TWO)
        case.remember_ai_facts({"facts_video": "кадров видеофиксации нет"})
        case.facts_video = "кадров видеофиксации нет"
        text = cases.node_request(case, "2.2")
        self.assertNotIn("ОБСТОЯТЕЛЬСТВА ПРОКУРОРА", text)
        case.facts_video = "кадров видеофиксации нет; знак читается"
        again = cases.node_request(case, "2.2")
        self.assertIn("ОБСТОЯТЕЛЬСТВА ПРОКУРОРА", again)
        self.assertIn("знак читается", again)

    def test_intake_request_has_no_facts_block(self):
        text = cases.intake_request(_accepted(PLAN_TWO))
        self.assertNotIn("ОБСТОЯТЕЛЬСТВА ПРОКУРОРА", text)


class IntakeFlowTests(unittest.TestCase):
    """Критерии 1–4: этап 1 без цепочек и короткое сообщение чата."""

    def test_short_message_matches_the_request(self):
        main = _src("main.py")
        self.assertIn("_intake_settled", main)
        self.assertIn("_intake_answer_complete", main)
        message = _src("ai_request.py")
        self.assertIn("def intake_message", message)
        body = message[message.index("def intake_message"):]
        body = body[:body.index("\ndef ", 10)]
        self.assertNotIn("МИНИМУМ ТРИ", body)

    def test_chains_are_optional_for_the_client(self):
        """«Принято» без цепочек не блокирует клиента и не открывает этап 3."""
        self.assertEqual(ogp_stages.MIN_CHAINS, 1)
        self.assertEqual(ogp_stages.MAX_CHAINS, 8)
        self.assertEqual(ogp_stages.chain_letters(1), ["A"])
        self.assertEqual(len(ogp_stages.chain_letters(8)), 8)
        case = cases.GovCase(number="1", appeal_raw=APPEAL)
        case.outcome = ogp_chain.OUTCOME_ACCEPT
        self.assertEqual(ogp_stages.chain_progress(case), [])

    def test_resend_button_is_in_the_facts_window(self):
        """Незакрытое замечание v65: «Повторить отправку» видна и в
        окне «Обстоятельства» — прокурор правит ввод этапа именно там."""
        ui = _src("postanovlenie_prokuror_ui.py")
        window = ui[ui.index("def _build_work_window"):]
        window = window[:window.index("\n    def ", 10)]
        self.assertIn("facts_resend_btn", window)
        self.assertIn("resend_requested.emit()", window)
        state = ui[ui.index("def set_resend_available"):]
        state = state[:state.index("\n    def ", 10)]
        self.assertIn("facts_resend_btn", state)

    def test_ui_waits_for_chains_without_crash(self):
        ui = _src("postanovlenie_prokuror_ui.py")
        self.assertIn("intake_pending", ui)
        self.assertIn("_active_node_kind", ui)


class QueueTests(unittest.TestCase):
    """Критерии 6 и 8: один автозапрос на узел, сериализация заявок."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        psend.clear(self.root)
        self.tmp.cleanup()

    @staticmethod
    def _record(kind="document", role=psend.ROLE_NODE, node="2.1"):
        return psend.build_record(kind=kind, request_text="заявка узла",
                                  case_number="1", node_kind=node, role=role,
                                  message_text="Заявка на постановление.")

    def test_roles_are_distinguished(self):
        self.assertEqual(psend.record_role(
            self._record(kind="intake", role="")), psend.ROLE_INTAKE)
        self.assertEqual(psend.record_role(self._record()), psend.ROLE_NODE)
        self.assertEqual(psend.record_role(
            self._record(role=psend.ROLE_AUTO_DOCUMENT)),
            psend.ROLE_AUTO_DOCUMENT)
        self.assertEqual(psend.AUTO_DOCUMENT_AFTER,
                         (psend.ROLE_INTAKE, psend.ROLE_NODE))

    def test_second_request_is_queued_not_overwritten(self):
        state, first = psend.enqueue(self.root, self._record())
        self.assertEqual(state, "saved")
        self.assertTrue(psend.is_busy(psend.load(self.root)))
        state, _queued = psend.enqueue(
            self.root, self._record(role=psend.ROLE_AUTO_DOCUMENT))
        self.assertEqual(state, "queued")
        self.assertEqual(psend.queue_len(self.root), 1)
        active = psend.load(self.root)
        self.assertEqual(psend.record_role(active), psend.ROLE_NODE)
        self.assertEqual(active.get("request_text"), first.get("request_text"))

    def test_promotion_takes_the_first_queued(self):
        psend.enqueue(self.root, self._record())
        psend.enqueue(self.root, self._record(role=psend.ROLE_AUTO_DOCUMENT))
        psend.enqueue(self.root, self._record(node="2.2"))
        promoted = psend.next_from_queue(self.root)
        self.assertTrue(promoted)
        self.assertEqual(psend.record_role(promoted), psend.ROLE_AUTO_DOCUMENT)
        self.assertEqual(promoted.get("state"), psend.STATE_WAITING)
        self.assertEqual(psend.queue_len(self.root), 1)
        self.assertEqual(psend.record_role(psend.load(self.root)),
                         psend.ROLE_AUTO_DOCUMENT)

    def test_duplicate_is_not_queued_twice(self):
        psend.enqueue(self.root, self._record())
        state, _record = psend.enqueue(self.root, self._record())
        self.assertEqual(state, "duplicate")
        self.assertEqual(psend.queue_len(self.root), 0)
        self.assertTrue(psend.same_target(self._record(), self._record()))
        self.assertFalse(psend.same_target(self._record(),
                                           self._record(node="2.2")))

    def test_updated_fields_make_a_new_request(self):
        """Критерий 11: правка полей — новая заявка, а не «дубль»."""
        psend.enqueue(self.root, self._record())
        updated = self._record()
        updated["request_text"] = "заявка узла с новыми обстоятельствами"
        state, _queued = psend.enqueue(self.root, updated)
        self.assertEqual(state, "queued")
        self.assertEqual(psend.queue_len(self.root), 1)
        self.assertFalse(psend.same_request(self._record(), updated))
        promoted = psend.next_from_queue(self.root)
        self.assertIn("новыми обстоятельствами", promoted["request_text"])

    def test_queue_survives_reload(self):
        psend.enqueue(self.root, self._record())
        psend.enqueue(self.root, self._record(node="2.2"))
        reloaded = psend.load_queue(self.root)
        self.assertEqual(len(reloaded), 1)
        psend.save_queue(self.root, reloaded)
        self.assertEqual(psend.queue_len(self.root), 1)
        self.assertTrue(psend.queue_path(self.root).is_file())

    def test_auto_document_is_requested_once_per_node(self):
        case = _accepted(PLAN_TWO)
        case.set_work_node("2.1", answer="===ПОСТАНОВЛЕНИЕ===", done=True)
        self.assertTrue(case.auto_document_due("2.1"))
        case.set_auto_document("2.1", "requested")
        self.assertFalse(case.auto_document_due("2.1"))
        self.assertEqual(case.auto_document_state("2.1"), "requested")
        case.set_auto_document("2.1", "done")
        self.assertFalse(case.auto_document_due("2.1"))
        self.assertEqual(case.auto_document_state("2.1"), "done")
        case.clear_node("2.1")
        self.assertEqual(case.auto_document_state("2.1"), "")
        self.assertFalse(case.auto_document_due("2.1"))   # узел очищен
        # новый ответ узла — новый автозапрос (критерий 12: повторы не затирают)
        case.set_work_node("2.1", answer="===ПОСТАНОВЛЕНИЕ===", done=True)
        self.assertTrue(case.auto_document_due("2.1"))

    def test_refused_case_still_asks_for_stage_one_document(self):
        """Критерий 5: отказ — автозапрос только по этапу 1."""
        case = _accepted(PLAN_TWO)
        case.set_outcome(ogp_chain.OUTCOME_REFUSE)
        case.set_work_node("1", answer="===ПОСТАНОВЛЕНИЕ===", done=True)
        self.assertTrue(case.auto_document_due("1"))
        self.assertFalse(case.auto_document_due("2.1"))
        self.assertFalse(case.auto_document_due("3"))

    def test_idle_slot_accepts_a_request(self):
        self.assertFalse(psend.is_busy(psend.load(self.root)))
        state, _record = psend.enqueue(self.root, self._record())
        self.assertEqual(state, "saved")
        psend.clear(self.root)
        self.assertFalse(psend.is_busy(psend.load(self.root)))

    def test_main_drains_the_queue(self):
        main = _src("main.py")
        self.assertIn("def _drain_pending_queue", main)
        block = main[main.index("def _drain_pending_queue"):]
        block = block[:block.index("\n    def ", 10)]
        self.assertIn("is_busy", block)
        self.assertIn("queue_len", block)
        self.assertIn("next_from_queue", block)
        self.assertIn("pending_send.promoted", block)
        self.assertIn("def _auto_document_request", main)
        auto = main[main.index("def _auto_document_request"):]
        auto = auto[:auto.index("\n    def ", 10)]
        self.assertIn(psend.ROLE_AUTO_DOCUMENT, auto)
        self.assertIn("node_document_request", auto)
        self.assertIn("write_appeal_file", auto)
        self.assertIn("set_auto_document", auto)

    def test_answer_marks_auto_document_done(self):
        main = _src("main.py")
        self.assertIn('set_auto_document(', main)
        self.assertGreater(main.count("_drain_pending_queue()"), 1)

    def test_slot_decorator_stays_with_the_answer_method(self):
        """Правило сборки: `@pyqtSlot(str)` стоит вплотную к `_apply_gov_answer`."""
        main = _src("main.py")
        index = main.index("def _apply_gov_answer")
        before = main[:index].rstrip()
        self.assertTrue(before.endswith("@pyqtSlot(str)"),
                        before[-80:])
        for name in ("def _gov_answer_folder", "def _auto_document_request",
                     "def _drain_pending_queue"):
            self.assertLess(main.index(name), index)


class UiNumberingTests(unittest.TestCase):
    """Критерии 15 и 16: форма показывает номер и не даёт занять чужой."""

    def test_form_suggests_the_next_free_number(self):
        ui = _src("postanovlenie_prokuror_ui.py")
        self.assertIn("def _new_blank_case", ui)
        blank = ui[ui.index("def _new_blank_case"):]
        blank = blank[:blank.index("\n    def ", 10)]
        self.assertIn(dl.NEW_NUMBERING, blank)
        self.assertIn("ensure_doc_base", blank)
        self.assertIn("used_doc_bases", blank)
        self.assertIn("def _node_number", ui)
        self.assertIn("def _show_number_state", ui)
        state = ui[ui.index("def _show_number_state"):]
        state = state[:state.index("\n    def ", 10)]
        self.assertIn("occupied_bases", state)
        self.assertIn("suggest_base", state)

    def test_stage_buttons_use_node_folders(self):
        ui = _src("postanovlenie_prokuror_ui.py")
        self.assertIn("cases.node_folder", ui)
        main = _src("main.py")
        self.assertIn("def _gov_answer_folder", main)
        block = main[main.index("def _gov_answer_folder"):]
        block = block[:block.index("\n    def ", 10)]
        self.assertIn("new_numbering", block)
        self.assertIn("dl.case_dir", block)


class RenderNumberTests(unittest.TestCase):
    """Критерии 15 и 17: тот же номер внутри PNG, рендер не сломан."""

    def test_number_is_fixed_before_render(self):
        """Клиент фиксирует номер до печати, а не берёт его у ИИ."""
        main = _src("main.py")
        self.assertIn("gov.number.fixed", main)
        self.assertIn("gov.archived", main)
        block = main[main.index("gov.number.fixed") - 2000:]
        block = block[:block.index("save_pages")]
        self.assertIn("pcases.node_doc_number", block)
        self.assertIn("doc.doc_number = fixed", block)
        self.assertIn("numbered=new_numbering", main)
        self.assertIn("dl.snapshot_current", main)

    def test_header_line_carries_the_document_number(self):
        try:
            import doc_render
            import postanovlenie_prokuror_data as gdata
        except Exception as exc:                       # pragma: no cover
            self.skipTest(f"нет PIL: {exc}")
        case = _accepted(PLAN_TWO)
        number = case.doc_number_for("2.1")
        doc = gdata.PostanovlenieProkurora()
        doc.doc_number = number
        doc.doc_date = "03.11.2026"
        doc.doc_type = "Об истребовании информации"
        doc.author_name = "Jonathan Raine"
        self.assertEqual(doc.number_line(), f"ПОСТАНОВЛЕНИЕ № {number}")
        self.assertIn("0001-2", doc.to_document_txt())
        pages = doc_render.render_document(doc)
        self.assertTrue(pages)
        stem = cases.node_stem(case, "2.1", doc.doc_number, doc.doc_type)
        self.assertTrue(stem.startswith(number))
        self.assertEqual(dl.page_name(stem, 1), f"{stem} — стр. 1.png")

    def test_legacy_document_keeps_its_own_number(self):
        """Старое дело v65: номер из формы, прежнее поведение `save_pages`."""
        src = _src("doc_render.py")
        self.assertIn("numbered: bool = False", src)
        self.assertIn("def save_pages", src)
        case = cases.GovCase(number="AG-1", appeal_raw=APPEAL)
        self.assertEqual(cases.node_doc_number(case, "1", fallback="AG-1"),
                         "AG-1")
        stem = cases.node_stem(case, "1", "AG-1")
        self.assertTrue(stem.startswith("AG-1"), stem)   # прежняя схема имён
        self.assertNotIn("0001", stem)


class VersionTests(unittest.TestCase):
    """Выпуск 2.22.0 / `universal-embedded-v66` / промт 1.37."""

    def test_version_is_bumped(self):
        self.assertRegex(_src("config.py"), r'CURRENT_VERSION = "2\.22\.0"')
        self.assertIn("universal-embedded-v66", _src("diagnostics.py"))
        self.assertIn('release="universal-embedded-v66"',
                      _src("native_browser.py"))

    def test_readme_documents_v66(self):
        text = _src("README.md")
        self.assertIn("v66", text)
        self.assertIn("2.22.0", text)
        self.assertIn("CHAT_URL", text)
        self.assertIn("promt 1.37.txt", text)
        self.assertIn("test_v66_numbering.py", text)

    def test_build_note_documents_v66(self):
        text = _src("СБОРКА_NUITKA.txt")
        self.assertIn("2.22.0.0", text)
        self.assertIn("TestLegalUniversal_v66", text)

    def test_changelog_exists(self):
        changelog = ROOT / "ИСПРАВЛЕНИЯ_v66.md"
        self.assertTrue(changelog.is_file())
        text = changelog.read_text(encoding="utf-8")
        self.assertIn("Постановление_65v.7z", text)
        self.assertIn("0001-1", text)
        self.assertIn("promt 1.37", text)

    def test_prompt_is_shipped(self):
        prompt = ROOT.parent / "Для новой версии" / "Текущий промт" / \
            "promt 1.37.txt"
        if not prompt.is_file():                     # pragma: no cover
            self.skipTest("папка промтов лежит рядом с папками версий")
        text = prompt.read_text(encoding="utf-8")
        self.assertNotIn("МИНИМУМ ТРИ РАЗНЫЕ ЦЕПОЧКИ", text)
        self.assertIn("СКОЛЬКО ОБЪЕКТИВНО ВОЗМОЖНО", text)
        self.assertIn("ОБСТОЯТЕЛЬСТВА ПРОКУРОРА", text)
        self.assertIn("0001-2", text)


if __name__ == "__main__":
    unittest.main()
