import os
import subprocess
import sys

import obspython as obs


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DAEMON = os.path.join(SCRIPT_DIR, "now_playing_daemon.py")
SMTC_CMD = os.path.join(SCRIPT_DIR, "smtc_cmd.py")

CREATE_NO_WINDOW = 0x08000000
TEXT_SOURCE_IDS = ("text_gdiplus", "text_gdiplus_v2", "text_ft2_source", "text_ft2_source_v2")
IMAGE_SOURCE_IDS = ("image_source",)


def _detect_pythonw() -> str:
    candidates: list[str] = []
    for base in (sys.prefix, sys.base_prefix):
        if base:
            candidates.append(os.path.join(base, "pythonw.exe"))
            candidates.append(os.path.join(base, "python.exe"))
    if sys.executable:
        exe_dir = os.path.dirname(sys.executable)
        name = os.path.basename(sys.executable).lower()
        if name in ("python.exe", "pythonw.exe"):
            candidates.append(os.path.join(exe_dir, "pythonw.exe"))
            candidates.append(sys.executable)
    seen: set = set()
    for c in candidates:
        if c and c not in seen and os.path.isfile(c):
            return c
        seen.add(c)
    return ""


PYTHONW_AUTO = _detect_pythonw()

_proc: subprocess.Popen | None = None
_text_source_name: str = ""
_image_source_name: str = ""
_play_scenes: set = set()
_last_cmd: str | None = None
_out_dir: str = ""
_pythonw_override: str = ""
_last_title_mtime: float = 0.0
_last_cover_mtime: float = 0.0


def _pythonw() -> str:
    return _pythonw_override or PYTHONW_AUTO


def _title_path() -> str:
    return os.path.join(_out_dir, "title.txt") if _out_dir else ""


def _cover_path() -> str:
    return os.path.join(_out_dir, "Cover.png") if _out_dir else ""


def _parse_scenes(raw: str) -> set:
    return {s.strip() for s in (raw or "").split(",") if s.strip()}


def script_description():
    return (
        "Yandex Music → title.txt + Cover.png\n"
        "Запускает SMTC-демон и обновляет выбранные источники текста/картинки\n"
        "напрямую через OBS API (без задержки 'Read from file').\n"
        "Путь к pythonw.exe определяется автоматически из настроек Python в OBS;\n"
        "при необходимости его можно переопределить вручную."
    )


def _add_source_list(props, key, label, allowed_ids):
    p = obs.obs_properties_add_list(
        props, key, label,
        obs.OBS_COMBO_TYPE_EDITABLE, obs.OBS_COMBO_FORMAT_STRING,
    )
    sources = obs.obs_enum_sources()
    if sources is not None:
        for s in sources:
            sid = obs.obs_source_get_unversioned_id(s)
            if sid in allowed_ids:
                name = obs.obs_source_get_name(s)
                obs.obs_property_list_add_string(p, name, name)
        obs.source_list_release(sources)


def script_defaults(settings):
    obs.obs_data_set_default_string(settings, "out_dir", "")
    obs.obs_data_set_default_string(settings, "pythonw_path", "")


def script_properties():
    props = obs.obs_properties_create()
    obs.obs_properties_add_path(
        props, "out_dir", "Папка для title.txt / Cover.png",
        obs.OBS_PATH_DIRECTORY, "", SCRIPT_DIR,
    )
    _add_source_list(props, "text_source", "Text source", TEXT_SOURCE_IDS)
    _add_source_list(props, "image_source", "Image source", IMAGE_SOURCE_IDS)
    obs.obs_properties_add_text(
        props, "play_scenes",
        "Сцены с музыкой (через запятую). На остальных — пауза",
        obs.OBS_TEXT_DEFAULT,
    )
    auto_label = PYTHONW_AUTO if PYTHONW_AUTO else "не найден"
    obs.obs_properties_add_path(
        props, "pythonw_path",
        f"pythonw.exe (пусто = авто: {auto_label})",
        obs.OBS_PATH_FILE, "Python (*.exe)", SCRIPT_DIR,
    )
    return props


def script_update(settings):
    global _text_source_name, _image_source_name, _play_scenes
    global _out_dir, _pythonw_override
    global _last_title_mtime, _last_cover_mtime
    _text_source_name = obs.obs_data_get_string(settings, "text_source") or ""
    _image_source_name = obs.obs_data_get_string(settings, "image_source") or ""
    _play_scenes = _parse_scenes(obs.obs_data_get_string(settings, "play_scenes"))
    _pythonw_override = obs.obs_data_get_string(settings, "pythonw_path") or ""

    new_out = obs.obs_data_get_string(settings, "out_dir") or ""
    if new_out != _out_dir:
        _out_dir = new_out
        _last_title_mtime = 0.0
        _last_cover_mtime = 0.0
        if _out_dir:
            _start_daemon()
        else:
            _stop_daemon()


def _set_text(text: str) -> None:
    if not _text_source_name:
        return
    src = obs.obs_get_source_by_name(_text_source_name)
    if src is None:
        return
    try:
        s = obs.obs_data_create()
        obs.obs_data_set_string(s, "text", text)
        obs.obs_source_update(src, s)
        obs.obs_data_release(s)
    finally:
        obs.obs_source_release(src)


def _refresh_image_source() -> None:
    if not _image_source_name:
        return
    src = obs.obs_get_source_by_name(_image_source_name)
    if src is None:
        return
    try:
        settings = obs.obs_source_get_settings(src)
        obs.obs_source_update(src, settings)
        obs.obs_data_release(settings)
    finally:
        obs.obs_source_release(src)


def _tick():
    global _last_title_mtime, _last_cover_mtime
    if not _out_dir:
        return

    if _text_source_name:
        title_file = _title_path()
        try:
            mt = os.path.getmtime(title_file)
        except OSError:
            mt = None
        if mt is not None and mt != _last_title_mtime:
            _last_title_mtime = mt
            try:
                with open(title_file, "r", encoding="utf-8") as f:
                    text = f.read().strip()
                _set_text(text)
            except OSError:
                pass

    if _image_source_name:
        try:
            mt = os.path.getmtime(_cover_path())
        except OSError:
            mt = None
        if mt is not None and mt != _last_cover_mtime:
            _last_cover_mtime = mt
            _refresh_image_source()


def _send_smtc(cmd: str) -> None:
    global _last_cmd
    if cmd == _last_cmd:
        return
    pyw = _pythonw()
    if not pyw:
        obs.script_log(
            obs.LOG_ERROR,
            "pythonw.exe не найден — укажи путь вручную в свойствах скрипта.",
        )
        return
    try:
        subprocess.Popen(
            [pyw, SMTC_CMD, cmd],
            creationflags=CREATE_NO_WINDOW,
            close_fds=True,
        )
        _last_cmd = cmd
        obs.script_log(obs.LOG_INFO, f"smtc -> {cmd}")
    except Exception as e:
        obs.script_log(obs.LOG_ERROR, f"smtc {cmd} failed: {e!r}")


def _current_scene_name() -> str:
    src = obs.obs_frontend_get_current_scene()
    if src is None:
        return ""
    try:
        return obs.obs_source_get_name(src) or ""
    finally:
        obs.obs_source_release(src)


def _apply_scene_state():
    if not _play_scenes:
        return
    name = _current_scene_name()
    if not name:
        return
    _send_smtc("play" if name in _play_scenes else "pause")


def _on_event(event):
    if event == obs.OBS_FRONTEND_EVENT_SCENE_CHANGED:
        _apply_scene_state()


def _stop_daemon():
    global _proc
    if _proc is not None and _proc.poll() is None:
        try:
            _proc.terminate()
            _proc.wait(timeout=3)
        except Exception:
            try:
                _proc.kill()
            except Exception:
                pass
    _proc = None


def _start_daemon():
    global _proc
    _stop_daemon()
    if not _out_dir:
        return
    pyw = _pythonw()
    if not pyw:
        obs.script_log(
            obs.LOG_ERROR,
            "pythonw.exe не найден — укажи путь вручную в свойствах скрипта.",
        )
        return
    try:
        os.makedirs(_out_dir, exist_ok=True)
        _proc = subprocess.Popen(
            [pyw, DAEMON, _out_dir],
            creationflags=CREATE_NO_WINDOW,
            close_fds=True,
        )
        obs.script_log(
            obs.LOG_INFO,
            f"now_playing_daemon started, pid={_proc.pid}, out={_out_dir}",
        )
    except Exception as e:
        obs.script_log(obs.LOG_ERROR, f"failed to start daemon: {e!r}")


def script_load(settings):
    global _out_dir, _pythonw_override
    _out_dir = obs.obs_data_get_string(settings, "out_dir") or ""
    _pythonw_override = obs.obs_data_get_string(settings, "pythonw_path") or ""
    if _out_dir:
        _start_daemon()
    else:
        obs.script_log(
            obs.LOG_WARNING,
            "out_dir не задан — демон не запущен. Укажи папку в свойствах скрипта.",
        )
    obs.timer_add(_tick, 100)
    obs.obs_frontend_add_event_callback(_on_event)


def script_unload():
    obs.obs_frontend_remove_event_callback(_on_event)
    obs.timer_remove(_tick)
    _stop_daemon()
