"""SQLite 房间：四人联机共享牌局（轮询同步）。"""

import json
import os
import secrets
import sqlite3
import string
import threading
import time

DB_PATH = os.environ.get("HUPAI_DB", os.path.join(os.path.dirname(__file__), "hupai.db"))
_lock = threading.Lock()


def _conn():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _lock, _conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS rooms (
                code TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'waiting',
                state_json TEXT NOT NULL DEFAULT '{}',
                version INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS seats (
                room_code TEXT NOT NULL,
                seat INTEGER NOT NULL,
                player_token TEXT NOT NULL,
                joined_at REAL NOT NULL,
                PRIMARY KEY (room_code, seat),
                UNIQUE (room_code, player_token)
            );
            """
        )


def _gen_code():
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(6))


def create_room():
    init_db()
    for _ in range(30):
        code = _gen_code()
        try:
            with _lock, _conn() as conn:
                conn.execute(
                    "INSERT INTO rooms (code, status, state_json, version, updated_at) VALUES (?,?,?,?,?)",
                    (code, "waiting", "{}", 0, time.time()),
                )
            return code
        except sqlite3.IntegrityError:
            continue
    raise RuntimeError("无法生成房间号")


def room_exists(code):
    init_db()
    with _lock, _conn() as conn:
        row = conn.execute("SELECT 1 FROM rooms WHERE code=?", (code,)).fetchone()
        return row is not None


def get_room_meta(code):
    init_db()
    with _lock, _conn() as conn:
        row = conn.execute(
            "SELECT status, version, updated_at FROM rooms WHERE code=?", (code,)
        ).fetchone()
        if not row:
            return None
        return {"status": row["status"], "version": row["version"], "updated_at": row["updated_at"]}


def get_seats(code):
    init_db()
    with _lock, _conn() as conn:
        rows = conn.execute(
            "SELECT seat, player_token FROM seats WHERE room_code=? ORDER BY seat",
            (code,),
        ).fetchall()
        return {int(r["seat"]): r["player_token"] for r in rows}


def seat_for_token(code, token):
    init_db()
    with _lock, _conn() as conn:
        row = conn.execute(
            "SELECT seat FROM seats WHERE room_code=? AND player_token=?",
            (code, token),
        ).fetchone()
        return int(row["seat"]) if row else None


def take_seat(code, seat, token):
    init_db()
    seat = int(seat)
    if seat < 0 or seat > 3:
        return None, "座位无效"
    with _lock, _conn() as conn:
        room = conn.execute("SELECT status FROM rooms WHERE code=?", (code,)).fetchone()
        if not room:
            return None, "房间不存在"
        if room["status"] != "waiting":
            return None, "对局已开始"
        existing = conn.execute(
            "SELECT seat FROM seats WHERE room_code=? AND player_token=?",
            (code, token),
        ).fetchone()
        if existing:
            return int(existing["seat"]), None
        taken = conn.execute(
            "SELECT 1 FROM seats WHERE room_code=? AND seat=?", (code, seat)
        ).fetchone()
        if taken:
            return None, "该座位已有人"
        conn.execute(
            "INSERT INTO seats (room_code, seat, player_token, joined_at) VALUES (?,?,?,?)",
            (code, seat, token, time.time()),
        )
        return seat, None


def load_state(code):
    init_db()
    with _lock, _conn() as conn:
        row = conn.execute(
            "SELECT state_json, status, version FROM rooms WHERE code=?", (code,)
        ).fetchone()
        if not row:
            return None, None, None
        raw = json.loads(row["state_json"] or "{}")
        return raw, row["status"], int(row["version"])


def save_state(code, state, *, status=None):
    init_db()
    js = json.dumps(state, ensure_ascii=False)
    with _lock, _conn() as conn:
        if status:
            conn.execute(
                "UPDATE rooms SET state_json=?, status=?, version=version+1, updated_at=? WHERE code=?",
                (js, status, time.time(), code),
            )
        else:
            conn.execute(
                "UPDATE rooms SET state_json=?, version=version+1, updated_at=? WHERE code=?",
                (js, time.time(), code),
            )


def get_version(code):
    meta = get_room_meta(code)
    return meta["version"] if meta else 0
