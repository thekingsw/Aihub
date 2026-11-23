"""One durable routing decision per room message, shared by the PC workers."""
import json
import re
import sqlite3
import time
import unicodedata
import urllib.request
from contextlib import contextmanager
from urllib.parse import urlencode

PROFILES = {
    "emi": ("emi|sol", r"\b(ai hub|codex|worker|python|kiểm tra code|phòng chung)\b"),
    "tinker": ("tinker", r"\b(tinkerapp|máy ảo|cron|dashboard|ubuntu|sso)\b"),
}
ACK = re.compile(
    r"^(?:(?:@?(?:emi|sol|tinker)[,:]?\s+)?(?:ok(?:ay)?|đã xong|xong rồi|"
    r"đã nhận|đã xử lý xong|hoàn tất|đã hoàn thành|cảm ơn(?: anh| em| tinker| emi| sol)?|"
    r"done|thanks|confirmed))[.!✅\s]*$"
)
REQUEST = re.compile(r"\?|(?:^|\s)(?:nhờ|giúp|hãy|giao|kiểm tra|xem|sửa|làm|"
                     r"chạy|viết|tiếp tục|thử|giải thích|cho anh|gửi|cần|nhân đôi)\b")
FOLLOWUP = re.compile(r"^(?:còn|vậy|tiếp tục|làm tiếp|sửa tiếp|thế|phần này)\b")
CLOSE = re.compile(r"^(?:đóng việc|hủy việc|đã xong|xong rồi|hoàn tất|đã hoàn thành)[.! ]*$")


def normalized(text):
    return " ".join(unicodedata.normalize("NFC", text).casefold().split())


def decide(message, open_tasks, recent, quoted_targets):
    """Return the destinations and a log-safe reason for the decision."""
    sender = message["from"]
    body = normalized(message["body"])
    kind = message.get("kind")
    if kind in {"ack", "result"} or ACK.fullmatch(body) or (sender == "anh" and CLOSE.fullmatch(body)):
        return [], "xac-nhan/ket-qua"
    if sender != "anh" and message.get("agent_turns", 0) >= 8:
        return [], "chot-8"
    direct = []
    for agent, (alias, _) in PROFILES.items():
        if agent != sender and re.search(
                rf'(?:(?<![\w@])[@\"“](?:{alias})\b|^(?:{alias})[,:]|'
                rf'\b(?:nhờ|gọi)\s+(?:{alias})\b)', body):
            direct.append(agent)
    if sender == "anh" and re.search(r"(?<!\w)@tất cả\b", body):
        return list(PROFILES), "goi-ca-hai"
    if direct:
        return direct, "goi-truc-tiep"
    legacy = message.get("to")
    if legacy in PROFILES and legacy != sender:
        return [legacy], "dia-chi-cu"
    if sender != "anh":
        return [], "agent-khong-goi-dong-su"
    if legacy == "all":
        return list(PROFILES), "dia-chi-cu-ca-hai"
    if quoted_targets:
        return quoted_targets[:1], "tiep-viec-trich-tin"
    if not REQUEST.search(body) and not FOLLOWUP.search(body):
        return [], "khong-thay-yeu-cau"
    matched = [agent for agent, (_, pattern) in PROFILES.items() if re.search(pattern, body)]
    if len(matched) == 1:
        return matched, "chu-de-ho-so"
    if len(matched) > 1:
        for task in open_tasks:
            if task["agent"] in matched:
                return [task["agent"]], "chu-de-viec-dang-mo"
        for row in reversed(recent):
            for agent in row["targets"]:
                if agent in matched:
                    return [agent], "chu-de-viec-gan-day"
    if FOLLOWUP.search(body):
        if open_tasks:
            return [open_tasks[0]["agent"]], "viec-dang-mo"
        for row in reversed(recent):
            if row["targets"]:
                return row["targets"][:1], "viec-gan-day"
    return ["emi"], "yeu-cau-chung"


PHASE_RULES = """Việc lớn hoặc anh yêu cầu giai đoạn: chỉ lập kế hoạch trước, không làm gộp.
Trả HANDOFF và dòng đầu /phase plan <task-id> <JSON mảng tiêu chí xong từng phase>.
Ví dụ /phase plan race-01 ["P1: tạo mẫu, test A pass", "P2: tích hợp, test B pass"].
Khi ngữ cảnh phase được cấp, Emi chỉ làm đúng phase current; xong trả HANDOFF,
dòng đầu /phase done <task-id> <số-phase>, sau đó tóm tắt và nơi xem chi tiết.
Tinker kiểm tra file/kết quả thật theo criteria; trả HANDOFF, dòng đầu
/phase pass <task-id> <số-phase> hoặc /phase fail <task-id> <số-phase>, kèm bằng chứng ngắn.
Không pass nếu chưa kiểm tra; fail quay lại cùng phase. Không tự sang phase sau.
Tin thường 2-5 câu, không log/diff; chi tiết lưu file hoặc DB và luôn ghi nơi tra.
"""


def brief_report(text, reference, ok=True):
    """Never publish technical dumps; preserve the full original separately."""
    text = text.strip()
    if len(text) > 1200 or '```' in text or re.search(r"(?m)^(?:diff --git|Traceback|[+@-]{3})", text):
        text = "Đã lưu kết quả đầy đủ; cần đọc chi tiết để đối chiếu kiểm tra và phần còn lại."
    if not text:
        text = "Đã lưu báo cáo; xem chi tiết để đối chiếu kết quả."
    if not ok:
        text = "Chưa hoàn thành: công cụ thực thi lỗi. Cần kiểm tra trước khi giao lại."
    return text + "\nChi tiết: " + reference


class Dispatch:
    def __init__(self, path):
        self.path = path
        with self.transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
            db.execute("""CREATE TABLE IF NOT EXISTS decisions
                (id TEXT PRIMARY KEY, targets TEXT, reason TEXT, author TEXT)""")
            db.execute("""CREATE TABLE IF NOT EXISTS jobs
                (id TEXT, agent TEXT, state TEXT, closed INTEGER DEFAULT 0, task TEXT,
                 result TEXT, PRIMARY KEY (id, agent))""")

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                db.execute("BEGIN IMMEDIATE")
                yield db
        finally:
            db.close()

    @staticmethod
    def get(db, key, default):
        row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    @staticmethod
    def put(db, key, value):
        db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, json.dumps(value, ensure_ascii=False)))

    def phase_event(self, db, msg):
        first = msg['body'].splitlines()[0]
        if not first.startswith('/phase '):
            return None
        if msg.get('agent_turns', 0) >= 8:
            return [], 'phase-paused-8', None
        try:
            _, action, tid, value = first.split(' ', 3)
            if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', tid):
                raise ValueError()
            key = 'phase:' + tid
            phase = self.get(db, key, None)
            if action == 'plan' and msg['from'] == 'emi' and phase is None:
                criteria = json.loads(value)
                if not isinstance(criteria, list) or not 1 <= len(criteria) <= 20 or not all(
                        isinstance(x, str) and 1 <= len(x) <= 500 for x in criteria):
                    raise ValueError()
                phase = dict(task_id=tid, criteria=criteria, current=1, state='working')
                targets = ['emi']
            else:
                number = int(value)
                if not phase or number != phase['current']:
                    raise ValueError()
                if action == 'done' and msg['from'] == 'emi' and phase['state'] == 'working':
                    phase['state'] = 'review'
                    phase['report'] = msg.get('ref', msg['id'])
                    targets = ['tinker']
                elif action in ('pass', 'fail') and msg['from'] == 'tinker' and phase['state'] == 'review':
                    if action == 'pass':
                        phase['current'] += 1
                    phase['state'] = 'complete' if phase['current'] > len(phase['criteria']) else 'working'
                    targets = [] if phase['state'] == 'complete' else ['emi']
                else:
                    raise ValueError()
            phase['last_message'] = msg['id']
            self.put(db, key, phase)
            return targets, 'phase-' + action, phase
        except (ValueError, TypeError, KeyError):
            return [], 'phase-invalid', None

    def cursor(self):
        with self.transaction() as db:
            return self.get(db, "cursor", "")

    def ingest(self, cursor, messages):
        decisions = []
        with self.transaction() as db:
            if self.get(db, "cursor", "") != cursor:
                return decisions
            recent = self.get(db, "recent", [])
            summary = self.get(db, "summary", [])
            for msg in messages:
                mid, sender = msg["id"], msg["from"]
                if "agent_turns" not in msg:
                    targets, reason = [], "lich-su-truoc-khi-doi"
                else:
                    quoted = msg.get("reply_to")
                    owners = db.execute("SELECT targets,author FROM decisions WHERE id=?", (quoted,)).fetchone()
                    quoted_targets = json.loads(owners[0]) if owners else []
                    if owners and not quoted_targets and owners[1] in PROFILES:
                        quoted_targets = [owners[1]]
                    if quoted and sender == "anh" and CLOSE.fullmatch(normalized(msg["body"])):
                        db.execute("""UPDATE jobs SET closed=1,
                                   state=CASE WHEN state='pending' THEN 'done' ELSE state END
                                   WHERE id=?""", (quoted,))
                    elif quoted and sender in PROFILES and msg.get("kind") in {"result", "handoff", "ack"}:
                        db.execute("""UPDATE jobs SET closed=1,
                                   state=CASE WHEN state='pending' THEN 'done' ELSE state END
                                   WHERE id=? AND agent=?""", (quoted, sender))
                    open_tasks = [
                        {"id": job_id, "agent": agent, "body": json.loads(task)["message"]["body"][:240],
                         "state": state}
                        for job_id, agent, task, state in db.execute(
                            "SELECT id,agent,task,state FROM jobs WHERE closed=0 ORDER BY rowid DESC LIMIT 12"
                        )
                    ]
                    event = self.phase_event(db, msg)
                    phase = None
                    if event:
                        targets, reason, phase = event
                        if msg.get('agent_turns', 0) >= 8:
                            targets = []
                    else:
                        targets, reason = decide(msg, open_tasks, recent, quoted_targets)
                    for agent in targets:
                        task = {"message": msg, "recent": recent[:], "summary": summary[:],
                                "open_tasks": open_tasks, "reason": reason, "phase": phase, "workflow_rules": PHASE_RULES}
                        db.execute("INSERT INTO jobs (id,agent,state,task) VALUES (?,?,'pending',?)",
                                   (mid, agent, json.dumps(task, ensure_ascii=False)))
                db.execute("INSERT INTO decisions VALUES (?,?,?,?)", (mid, json.dumps(targets), reason, sender))
                decisions.append((mid, targets, reason))
                recent.append({"id": mid, "from": sender, "body": msg["body"][:480], "targets": targets})
                if len(recent) > 8:
                    older = recent.pop(0)
                    summary = (summary + [f"{older['from']}: {older['body'][:180]}"])[-16:]
            if messages:
                self.put(db, "cursor", messages[-1]["id"])
                self.put(db, "agent_turns", messages[-1].get("agent_turns", 0))
                self.put(db, "recent", recent)
                self.put(db, "summary", summary)
        return decisions

    def next_job(self, agent, reserve=False):
        with self.transaction() as db:
            paused = self.get(db, "agent_turns", 0) >= 8
            row = db.execute("""SELECT id,state,task,result FROM jobs WHERE agent=?
                                AND state IN ('pending','outbox')
                                AND COALESCE(json_extract(result, '$._retry_at'),0)<=? AND
                                (NOT ? OR state='outbox' OR json_extract(task, '$.message.from')='anh')
                                ORDER BY state<>'outbox', rowid LIMIT 1""", (agent, time.time(), paused)).fetchone()
            if not row:
                return None
            mid, state, task, result = row
            if reserve and state == "pending":
                db.execute("UPDATE jobs SET state='running' WHERE id=? AND agent=?", (mid, agent))
            return mid, state, json.loads(task), json.loads(result) if result else None

    def finish(self, mid, agent, state, result=None):
        with self.transaction() as db:
            db.execute("""UPDATE jobs SET state=?, result=?,
                       closed=CASE WHEN ? IN ('silent','done','blocked') THEN 1 ELSE closed END
                       WHERE id=? AND agent=?""",
                       (state, json.dumps(result, ensure_ascii=False), state, mid, agent))

    def stranded(self, agent):
        with self.transaction() as db:
            return [row[0] for row in db.execute(
                "SELECT id FROM jobs WHERE agent=? AND state='running'", (agent,))]


def request_json(url, token, payload=None, timeout=25):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url, data=data, headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout) as response:
        return json.load(response)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def sync_room(dispatch, api, token, log, wait=0):
    cursor = dispatch.cursor()
    query = {"wait": wait}
    if cursor:
        query["since"] = cursor
    data = request_json(api + "/internal/messages?" + urlencode(query), token)
    for mid, targets, reason in dispatch.ingest(cursor, data["messages"]):
        if reason != "lich-su-truoc-khi-doi":
            log(f"room id={mid} targets={','.join(targets) or '-'} reason={reason}")
