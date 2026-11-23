"""Đẩy đúng việc của Tinker từ PC đến máy ảo."""
import os
import time
from urllib.parse import urlsplit

from emi_worker import API, BASE, acquire_worker_lock, read_token
from room_dispatch import Dispatch, request_json, sync_room, PHASE_RULES


def log(message):
    line = time.strftime("[%Y-%m-%d %H:%M:%S] ") + message
    with open(os.path.join(BASE, "tinker_worker.log"), "a", encoding="utf-8") as stream:
        stream.write(line + "\n")
    print(line, flush=True)


def deliver(dispatch, url, token, job):
    mid, _, task, _ = job
    task['workflow_rules'] = PHASE_RULES
    receipt = request_json(url, token, task, timeout=5)
    if receipt.get("accepted") != mid:
        raise ValueError("Sai mã xác nhận nhận việc")
    dispatch.finish(mid, "tinker", "delivered")
    log(f"room id={mid} VM nhận việc reason={task['reason']}")


def main():
    url = os.environ.get("AI_HUB_TINKER_WAKE_URL", "")
    wake_token = os.environ.get("AI_HUB_TINKER_WAKE_TOKEN", "")
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.path != "/wake" or parsed.query or parsed.fragment or not wake_token):
        raise SystemExit("Cần URL https://<VM>/wake và AI_HUB_TINKER_WAKE_TOKEN riêng.")
    if wake_token == read_token():
        raise SystemExit("Token nhận wake phải khác token AI Hub.")
    try:
        worker_lock = acquire_worker_lock("tinker")
    except OSError:
        raise SystemExit("Tinker worker đã chạy hoặc không mở được khóa.") from None
    with worker_lock:
        dispatch = Dispatch(os.path.join(BASE, "room_dispatch.sqlite3"))
        token = read_token()
        while True:
            try:
                if request_json(API + "/api/status", token).get("mode") != "room":
                    time.sleep(3)
                    continue
                sync_room(dispatch, API, token, log)
                job = dispatch.next_job("tinker")
                if job:
                    deliver(dispatch, url, wake_token, job)
                else:
                    sync_room(dispatch, API, token, log, wait=20)
            except Exception as exc:
                log(f"Thử lại sau 3 giây ({type(exc).__name__}, HTTP {getattr(exc, 'code', '-')})")
                time.sleep(3)


if __name__ == "__main__":
    main()
