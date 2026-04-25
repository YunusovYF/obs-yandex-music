import asyncio
import sys

from winsdk.windows.media.control import (
    GlobalSystemMediaTransportControlsSessionManager as MM,
)


APP_FILTER = "Яндекс Музыка"


async def find_session(mgr):
    sessions = mgr.get_sessions()
    for i in range(sessions.size):
        s = sessions.get_at(i)
        if APP_FILTER in (s.source_app_user_model_id or ""):
            return s
    return None


async def run(cmd: str) -> int:
    mgr = await MM.request_async()
    s = await find_session(mgr)
    if s is None:
        return 2
    if cmd == "pause":
        await s.try_pause_async()
    elif cmd == "play":
        await s.try_play_async()
    elif cmd == "toggle":
        await s.try_toggle_play_pause_async()
    else:
        return 3
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(1)
    sys.exit(asyncio.run(run(sys.argv[1])))
