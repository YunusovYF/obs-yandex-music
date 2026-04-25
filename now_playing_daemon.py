import asyncio
import base64
import sys
import time
from pathlib import Path

from winsdk.windows.media.control import (
    GlobalSystemMediaTransportControlsSessionManager as MM,
)
from winsdk.windows.storage.streams import (
    Buffer,
    DataReader,
    InputStreamOptions,
)


if len(sys.argv) > 1:
    OUT_DIR = Path(sys.argv[1])
else:
    OUT_DIR = Path(__file__).resolve().parent

TITLE_FILE = OUT_DIR / "title.txt"
COVER_FILE = OUT_DIR / "Cover.png"
LOG_FILE = OUT_DIR / "daemon.log"

APP_FILTER = "Яндекс Музыка"
POLL_INTERVAL = 2.0

PLAYBACK_STATUS_PLAYING = 4

BLANK_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGMAAQAABQABDQottAAAAABJRU5ErkJggg=="
)


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n"
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def atomic_write_text(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


async def find_yandex_session(mgr):
    sessions = mgr.get_sessions()
    for i in range(sessions.size):
        s = sessions.get_at(i)
        if APP_FILTER in (s.source_app_user_model_id or ""):
            return s
    return None


async def read_thumbnail(thumb_ref) -> bytes | None:
    stream = await thumb_ref.open_read_async()
    size = stream.size
    if size == 0:
        return None
    buf = Buffer(size)
    await stream.read_async(buf, size, InputStreamOptions.READ_AHEAD)
    reader = DataReader.from_buffer(buf)
    return bytes(reader.read_buffer(buf.length))


def clear_outputs(reason: str) -> None:
    atomic_write_bytes(COVER_FILE, BLANK_PNG)
    atomic_write_text(TITLE_FILE, "")
    log(f"cleared ({reason})")


async def main_loop() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    LOG_FILE.write_text("", encoding="utf-8")
    log("daemon started")

    mgr = await MM.request_async()
    last_key: tuple | None = None
    cleared = False

    while True:
        try:
            session = await find_yandex_session(mgr)

            if session is None:
                if not cleared:
                    clear_outputs("no Yandex Music session")
                    cleared = True
                    last_key = None
            else:
                pb = session.get_playback_info()
                is_playing = pb.playback_status == PLAYBACK_STATUS_PLAYING

                if not is_playing:
                    if not cleared:
                        clear_outputs(f"playback status={pb.playback_status}")
                        cleared = True
                        last_key = None
                else:
                    info = await session.try_get_media_properties_async()
                    artist = (info.artist or "").strip()
                    title = (info.title or "").strip()
                    key = (artist, title)

                    if key != last_key or cleared:
                        text = f"{artist} – {title}" if title else ""

                        cover_data: bytes | None = None
                        if info.thumbnail is not None:
                            try:
                                cover_data = await read_thumbnail(info.thumbnail)
                            except Exception as e:
                                log(f"thumbnail error: {e!r}")

                        if cover_data:
                            atomic_write_bytes(COVER_FILE, cover_data)
                            log(f"cover updated ({len(cover_data)} bytes)")
                        atomic_write_text(TITLE_FILE, text)
                        log(f"track changed -> {text!r}")

                        last_key = key
                        cleared = False
        except Exception as e:
            log(f"loop error: {e!r}")

        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        log("daemon stopped (KeyboardInterrupt)")
        sys.exit(0)
