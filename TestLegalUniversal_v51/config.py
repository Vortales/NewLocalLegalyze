import diagnostics as diag
from diagnostics import stage
import json
import os
import shutil
import sys
from pathlib import Path


def _is_compiled() -> bool:
    """
    True, если приложение собрано в exe.
    """
    return getattr(sys, "frozen", False) or "__compiled__" in globals()


def _script_dir() -> Path:
    """
    Директория исходников при обычном запуске через python.
    """
    try:
        return Path(os.path.dirname(os.path.abspath(__file__)))
    except Exception:
        diag.exception("config.py:24")
        return Path.cwd()


def _bundle_dir() -> Path:
    """
    Директория, где лежат ресурсы приложения.

    Для Nuitka --onefile это обычно временная папка, куда распакованы ресурсы.
    Для обычного запуска это папка исходников.
    """
    if _is_compiled():
        try:
            return Path(os.path.dirname(os.path.abspath(sys.executable)))
        except Exception:
            diag.exception("config.py:39")
            return _script_dir()

    return _script_dir()


def _writable_app_dir() -> Path:
    from storage_paths import app_dir
    return app_dir()


def _legacy_local_dir():
    """
    Старое расположение данных рядом с exe/скриптом.

    Используется только для одноразовой миграции старого config.json,
    если он уже существовал рядом с приложением.
    """
    try:
        if _is_compiled():
            return Path(os.path.dirname(os.path.abspath(sys.executable)))

        return _script_dir()
    except Exception:
        diag.exception("config.py:73")
        return None


APP_DIR = _writable_app_dir()

DATA_DIR = APP_DIR / "data"
CONFIG_FILE = APP_DIR / "config.json"
PROMPTS_DIR = DATA_DIR / "prompts"

# ТЗ 1.1–1.2: Шаблон.txt лежит ВСЕГДА рядом с config.json, не шифруется.
# Имя файла статично и никогда не меняется.
TEMPLATE_FILE = APP_DIR / "Шаблон.txt"

# RESOURCE_DIR / BUNDLE_DATA_DIR нужны только для чтения встроенных ресурсов,
# например шрифта, который распаковывается во временную папку --onefile.
RESOURCE_DIR = _bundle_dir()
BUNDLE_DATA_DIR = RESOURCE_DIR / "data"
ENCRYPTION_KEY = b"legalyze-fernet-key-32bytes-long!"
# v26: серверной части больше нет. Строка оставлена только для совместимости
# импортов (`from config import SERVER_URL`); ни один запрос по ней не идёт.
SERVER_URL = ""
CURRENT_VERSION = "2.7.0"

# Порядок полей в Шаблон.txt (фиксированный, менять нельзя).
TEMPLATE_FIELDS = ("Name", "ID", "Fraction", "Rang", "Department", "JobTitle")

# v37: в шаблоне больше НЕТ отдельных реквизитов для бланков.
#
# Замечание: «Убери лишние строки ввода. Звание соответствует должности,
# должность полностью и должность одинаковы, ведомство в бланке — это
# фракция, город всегда Лос-Сантос, должность прокурора — это должность,
# удостоверение — это ID, а реквизиты Прокурора должны вводиться в самом
# конструкторе, а не в шаблоне».
#
# Поэтому бланк постановления собирается из шести основных полей персонажа:
#   Имя и фамилия (Name)    -> подписант;
#   ID (ID)                 -> номер удостоверения;
#   Фракция (Fraction)      -> ведомство в шапке бланка;
#   Ранг (Rang)             -> звание в бланке;
#   Отдел (Department)      -> подразделение в шапке;
#   Должность (JobTitle)    -> должность подписанта (она же «Должность
#                              полностью» и «Должность прокурора»).
# Город в бланке всегда «Лос-Сантос» (см. `postanovlenie_prokuror_data`).
# Оставшиеся ключи можно задать вручную в конструкторе — они попадают в
# форму только если реально заполнены, поэтому «лишних строк ввода» нет.
EXTRA_TEMPLATE_FIELDS = (
    "DocPrefix",       # префикс номера, например: LSCSD-IAD
    "GovDocPrefix",    # префикс номера постановления Прокурора, например: AG
    # Устаревшие ключи (v27–v36): оставлены только для чтения старых
    # Шаблон.txt/конфигов, в окне «Шаблон» больше не показываются.
    "RankTitle",
    "PositionFull",
    "Authority",
    "City",
    "GovRank",
    "GovID",
)

# Шаблон по умолчанию (v25: поля чистые, без предустановленных значений).
DEFAULT_TEMPLATE = {
    "Name": "",
    "ID": "",
    "Fraction": "",
    "Rang": "",
    "Department": "",
    "JobTitle": "",
}

DEFAULT_CONFIG = {
    "login": "",
    "password": "",
    "remember": False,
    "token": "",
    "selected_prompt": "",
    "hotkey_toggle": "",
    "hotkey_mic": "",
    "hotkeys_configured": False,
    "template": DEFAULT_TEMPLATE.copy(),
    # v28: куда сохранять готовые PNG-постановления. Пусто — папка
    # postanovleniya/png рядом с приложением (поведение v27).
    "postanovlenie_dir": "",
}


def postanovlenie_dir(cfg: dict | None = None) -> Path:
    """Корневая папка постановлений: выбранная пользователем или папка программы.

    v30.2: по умолчанию — `postanovleniya` РЯДОМ С ПРОГРАММОЙ, а не в
    %%APPDATA%%/Legalyze и не на рабочем столе. Внутри корня каждое
    постановление лежит в своей подпапке `<номер>/`.
    """
    cfg = cfg or {}
    custom = str(cfg.get("postanovlenie_dir", "") or "").strip()
    default = _script_dir() / "postanovleniya"
    folder = Path(custom) if custom else default
    # v32: если выбранная папка недоступна (нет прав, отключён диск), пишем
    # рядом с программой и говорим об этом в лог — «Бланк не сформирован»
    # из-за прав доступа больше не появится.
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except Exception:
        diag.event("config.postanovlenie_dir.not_writable", path=str(folder))
        folder = default
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except Exception:
            diag.exception("config.py:postanovlenie_dir.default")
    return folder


def _find_icon_file() -> Path:
    candidates = [
        RESOURCE_DIR / "icon.ico",
        _script_dir() / "icon.ico",
        APP_DIR / "icon.ico",
        Path(__file__).resolve().parent / "icon.ico",
    ]
    for p in candidates:
        try:
            if p.is_file():
                return p
        except Exception:
            pass
    return RESOURCE_DIR / "icon.ico"


ICON_FILE = _find_icon_file()


def _migrate_legacy_config() -> None:
    """
    Если рядом со старым exe/скриптом уже был config.json,
    копируем его в новое расположение в %APPDATA%.

    Это выполняется только один раз: если новый config.json уже есть,
    ничего не делаем.
    """
    try:
        if CONFIG_FILE.exists():
            return

        legacy_dir = _legacy_local_dir()

        if not legacy_dir:
            return

        legacy_config = legacy_dir / "config.json"

        if legacy_config.exists() and legacy_config.is_file():
            shutil.copy2(legacy_config, CONFIG_FILE)
    except Exception:
        diag.exception("config.py:142")
        pass


def _copy_bundle_font() -> None:
    """
    Копирует DejaVuSans.ttf из ресурсов приложения в постоянную DATA_DIR.

    Это важно для --onefile, потому что включённый через --include-data-file
    шрифт находится во временной папке, а PDF-генератор будет искать его в
    постоянной папке данных.
    """
    try:
        dst = DATA_DIR / "DejaVuSans.ttf"

        if dst.exists():
            return

        candidates = []

        # Если пользователь вдруг положил data рядом с оригинальным exe
        try:
            if _is_compiled() and sys.argv and sys.argv[0]:
                argv_dir = Path(sys.argv[0]).parent
                candidates.append(argv_dir / "data" / "DejaVuSans.ttf")
        except Exception:
            diag.exception("config.py:168")
            pass

        # Основной вариант для --onefile: ресурс рядом с распакованным бинарником
        candidates.append(BUNDLE_DATA_DIR / "DejaVuSans.ttf")

        # Вариант для обычного запуска из исходников
        candidates.append(_script_dir() / "data" / "DejaVuSans.ttf")

        for src in candidates:
            try:
                if not src.exists():
                    continue

                if not src.is_file():
                    continue

                if os.path.abspath(src) == os.path.abspath(dst):
                    continue

                shutil.copy2(src, dst)
                return
            except Exception:
                diag.exception("config.py:191")
                continue
    except Exception:
        diag.exception("config.py:194")
        pass


@stage
def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PROMPTS_DIR.mkdir(parents=True, exist_ok=True)

    _migrate_legacy_config()
    _copy_bundle_font()


@stage
def load_config() -> dict:
    ensure_dirs()

    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)

            for k, v in DEFAULT_CONFIG.items():
                if k not in cfg:
                    cfg[k] = v.copy() if isinstance(v, dict) else v

            # Если первичная настройка горячих клавиш не завершена — поля строго пустые
            if not cfg.get("hotkeys_configured"):
                cfg["hotkey_toggle"] = ""
                cfg["hotkey_mic"] = ""
                cfg["hotkeys_configured"] = False

            cfg["template"] = normalize_template(cfg.get("template"))
            return cfg
        except Exception:
            diag.exception("config.py:223")
            pass

    cfg = DEFAULT_CONFIG.copy()
    cfg["template"] = DEFAULT_TEMPLATE.copy()

    try:
        save_config(cfg)
    except Exception:
        diag.exception("config.py:232")
        pass

    return cfg


def save_config(cfg: dict):
    ensure_dirs()

    tmp_file = CONFIG_FILE.with_name(CONFIG_FILE.name + ".tmp")

    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

    os.replace(tmp_file, CONFIG_FILE)


# --------------------------------------------------------------------------- #
#  Несколько персонажей (v35)
# --------------------------------------------------------------------------- #
#
#  Раньше шаблон был один: один пользователь — один персонаж. Теперь профилей
#  может быть сколько угодно: каждый со своим набором полей, а активный
#  выбирается в любой момент.
#
#  Совместимость: `cfg["template"]` ОСТАЁТСЯ активным шаблоном (его читают
#  существующие места — промт, бланки, Шаблон.txt), а список профилей лежит
#  в `cfg["templates"]` = [{"name": "...", "template": {...}}, ...]
#  с указателем `cfg["template_active"]` (имя профиля).

DEFAULT_PROFILE_NAME = "Основной"


def template_profiles(cfg: dict) -> list:
    """Список профилей персонажей: [{"name": ..., "template": {...}}, …].

    Если профилей ещё нет — создаётся один из текущего `template`, чтобы
    старое поведение (один шаблон) работало без миграции вручную.
    """
    cfg = cfg if isinstance(cfg, dict) else {}
    raw = cfg.get("templates")
    profiles = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            profiles.append({"name": name,
                             "template": normalize_template(item.get("template"))})
    if profiles:
        return profiles
    # Миграция: единственный шаблон -> единственный профиль.
    legacy = normalize_template(cfg.get("template"))
    name = str(cfg.get("template_active") or "").strip() or DEFAULT_PROFILE_NAME
    return [{"name": name, "template": legacy}]


def active_profile_name(cfg: dict) -> str:
    """Имя активного персонажа (профиля шаблона)."""
    profiles = template_profiles(cfg)
    want = str((cfg or {}).get("template_active") or "").strip()
    for profile in profiles:
        if profile["name"] == want:
            return want
    return profiles[0]["name"]


def active_template(cfg: dict) -> dict:
    """Шаблон активного персонажа (а не «один на всех»)."""
    name = active_profile_name(cfg)
    for profile in template_profiles(cfg):
        if profile["name"] == name:
            return dict(profile["template"])
    return normalize_template((cfg or {}).get("template"))


#: Город в бланке постановления всегда один и тот же (замечание v37).
DOC_CITY = "г. Лос-Сантос"

#: Отдел -> подразделение в шапке бланка. ИИ и раньше писал полное название,
#: но брать его было неоткуда: в шаблоне был только код отдела.
DIVISION_BY_DEPARTMENT = {
    "iad": "INTERNAL AFFAIRS DIVISION",
    "db": "DETECTIVE BUREAU",
    "spd": "SPECIAL PATROL DIVISION",
    "hp": "HIGHWAY PATROL",
    "seb": "SPECIAL ENFORCEMENT BUREAU",
    "af": "AIR FORCE",
    "sai": "SHERIFF ACADEMY INSTRUCTORS",
    "sa": "SHERIFF ACADEMY",
    "swat": "SPECIAL WEAPONS AND TACTICS",
    "k-9": "K-9 UNIT",
    "k9": "K-9 UNIT",
    "ndiu": "NARCOTICS DETECTION & INTERVENTION UNIT",
    "mcs": "MARINE & COASTAL SECURITY",
    "fsd": "FISHING SHERIFF DIVISION",
    "mp": "MILITARY POLICE",
}


def division_for(department: str) -> str:
    """«iad» -> «INTERNAL AFFAIRS DIVISION»; неизвестное вернёт как есть."""
    text = str(department or "").strip()
    if not text:
        return ""
    return DIVISION_BY_DEPARTMENT.get(text.lower().strip(" ."), text.upper())


def document_defaults(cfg: dict | None = None) -> dict:
    """Реквизиты бланка из ОСНОВНЫХ полей активного персонажа (v37).

    Замечание пользователя: «Звание соответствует Должности, Должность
    полностью и Должность одинаковы, Ведомство в бланке — это Фракция, город
    всегда Лос-Сантос, Должность прокурора — это Должность, Удостоверение —
    это ID». Значит, второго набора полей быть не должно: все реквизиты
    документа выводятся из `Name`, `ID`, `Fraction`, `Rang`, `Department`,
    `JobTitle`.
    """
    template = active_template(cfg or {})
    job = str(template.get("JobTitle", "") or "").strip()
    rank = str(template.get("Rang", "") or "").strip()
    return {
        "author_name": str(template.get("Name", "") or "").strip(),
        "author_id": str(template.get("ID", "") or "").strip(),
        "vedomstvo": str(template.get("Fraction", "") or "").strip(),
        "rank": rank,
        "department": str(template.get("Department", "") or "").strip(),
        "division": division_for(template.get("Department", "")),
        # «Должность полностью» = «Должность»: второй строки ввода нет.
        "position": job,
        "city": DOC_CITY,
        # Старые ключи (v27–v36) читаются, если их успели заполнить вручную.
        "author_rank": job or str(template.get("PositionFull", "") or "").strip()
        or str(template.get("GovRank", "") or "").strip(),
        "doc_prefix": str(template.get("DocPrefix", "") or "").strip(),
        "gov_doc_prefix": str(template.get("GovDocPrefix", "") or "").strip(),
    }


def add_profile(cfg: dict, name: str, template: dict | None = None) -> str:
    """Добавляет персонажа. Возвращает итоговое имя (уникальное)."""
    cfg = cfg if isinstance(cfg, dict) else {}
    profiles = template_profiles(cfg)
    base = str(name or "").strip() or DEFAULT_PROFILE_NAME
    existing = {p["name"].lower() for p in profiles}
    unique = base
    index = 2
    while unique.lower() in existing:
        unique = f"{base} {index}"
        index += 1
    profiles.append({"name": unique, "template": normalize_template(template or {})})
    cfg["templates"] = profiles
    return unique


def select_profile(cfg: dict, name: str) -> bool:
    """Переключает активного персонажа и подставляет его шаблон."""
    for profile in template_profiles(cfg):
        if profile["name"] == str(name or "").strip():
            cfg["template_active"] = profile["name"]
            cfg["template"] = dict(profile["template"])
            return True
    return False


def rename_profile(cfg: dict, old: str, new: str) -> bool:
    new_name = str(new or "").strip()
    if not new_name:
        return False
    profiles = template_profiles(cfg)
    for profile in profiles:
        if profile["name"] == new_name and new_name != old:
            return False                      # имя занято
    for profile in profiles:
        if profile["name"] == old:
            profile["name"] = new_name
            cfg["templates"] = profiles
            if cfg.get("template_active") == old:
                cfg["template_active"] = new_name
            return True
    return False


def delete_profile(cfg: dict, name: str) -> bool:
    """Удаляет персонажа. Последний удалить нельзя — иначе нечего выбирать."""
    profiles = template_profiles(cfg)
    if len(profiles) <= 1:
        return False
    remaining = [p for p in profiles if p["name"] != name]
    if len(remaining) == len(profiles):
        return False
    cfg["templates"] = remaining
    if cfg.get("template_active") == name:
        cfg["template_active"] = remaining[0]["name"]
        cfg["template"] = dict(remaining[0]["template"])
    return True


def get_template_filled(cfg: dict) -> bool:
    """Заполнен ли Шаблон полностью (v26).

    Раньше хватало любого непустого поля — и пользователь оставался с
    полупустым шаблоном. Теперь «заполнен» значит заполнены ВСЕ поля:
    иначе при старте открывается окно «Шаблон», а работа ждёт его сохранения.
    """
    t = active_template(cfg)          # v35: проверяем активного персонажа
    return all(str(t.get(f, "")).strip() for f in TEMPLATE_FIELDS)


def get_hotkeys_configured(cfg: dict) -> bool:
    """Проверяет, прошёл ли пользователь первичную настройку горячих клавиш (v25)."""
    return bool(cfg.get("hotkeys_configured") and cfg.get("hotkey_toggle") and cfg.get("hotkey_mic"))


# --------------------------------------------------------------------------- #
#  Шаблон.txt — открытый файл рядом с config.json (ТЗ 1.1, 1.2, 1.4)
# --------------------------------------------------------------------------- #

def normalize_template(template: dict | None) -> dict:
    """Приводит шаблон к каноническому виду: все поля, значения — строки.

    v27: если в Шаблон.txt уже лежат дополнительные реквизиты для бланков
    (`RankTitle`, `PositionFull`, `Authority`, `DocPrefix`, `City`), они
    сохраняются — иначе они бы стирались при каждом сохранении шаблона.
    """
    template = template or {}
    result = {}
    for field in TEMPLATE_FIELDS:
        val = template.get(field, DEFAULT_TEMPLATE.get(field, ""))
        result[field] = str(val if val is not None else "")
    for field in EXTRA_TEMPLATE_FIELDS:
        val = template.get(field, "")
        if str(val or "").strip():
            result[field] = str(val)
    return result


def read_template_file() -> dict:
    """Читает Шаблон.txt (JSON). Нет файла / битый — возвращает шаблон по умолчанию."""
    try:
        if TEMPLATE_FILE.exists():
            data = json.loads(TEMPLATE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return normalize_template(data)
    except Exception:
        diag.exception("config.py:276")
        pass
    return normalize_template(DEFAULT_TEMPLATE)


def write_template_file(template: dict | None) -> Path:
    """Перезаписывает Шаблон.txt из словаря шаблона. Файл НЕ шифруется."""
    ensure_dirs()
    data = normalize_template(template)
    tmp_file = TEMPLATE_FILE.with_name(TEMPLATE_FILE.name + ".tmp")
    tmp_file.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(tmp_file, TEMPLATE_FILE)
    return TEMPLATE_FILE


def ensure_template_file(template: dict | None = None) -> Path:
    """Создаёт Шаблон.txt с базовыми параметрами, если его нет."""
    ensure_dirs()
    if not TEMPLATE_FILE.exists():
        return write_template_file(template or DEFAULT_TEMPLATE)
    return TEMPLATE_FILE


def save_template(cfg: dict, template: dict | None) -> dict:
    """Единая точка сохранения шаблона: config.json + Шаблон.txt (ТЗ 1.2).

    v35: сохраняем в профиль АКТИВНОГО персонажа, чтобы правки одного не
    затирали данные другого.
    """
    data = normalize_template(template)
    name = active_profile_name(cfg)
    cfg["template"] = data
    cfg["template_active"] = name
    profiles = template_profiles(cfg)
    for profile in profiles:
        if profile["name"] == name:
            profile["template"] = data
    cfg["templates"] = profiles
    save_config(cfg)
    try:
        write_template_file(data)
    except Exception:
        diag.exception("config.py:309")
        pass
    return cfg
