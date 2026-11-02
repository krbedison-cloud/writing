import json
import os
import sqlite3
import threading
from pathlib import Path
from contextlib import contextmanager
from cryptography.fernet import Fernet

def dumps(v):
    return json.dumps(v, ensure_ascii=False, allow_nan=False)

class Database:
    def __init__(self, data_dir):
        self.path = Path(data_dir)
        self.path.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        key_path = self.path / "master.key"
        if not key_path.exists():
            try:
                fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as f:
                    f.write(Fernet.generate_key())
            except FileExistsError:
                pass
        self.cipher = Fernet(key_path.read_bytes())
        with self.connect() as c:
            c.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS providers(id TEXT PRIMARY KEY, config TEXT NOT NULL, secret TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS papers(id TEXT PRIMARY KEY, data TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS attempts(id TEXT PRIMARY KEY, data TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS highlights(id TEXT PRIMARY KEY, question_id TEXT NOT NULL, signature TEXT NOT NULL, data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS highlights_question ON highlights(question_id,signature);
            PRAGMA user_version=2;
            ''')
    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.path / "writing.db", timeout=30)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()
    def execute(self, sql, args=()):
        with self.lock, self.connect() as c:
            c.execute(sql, args)
    def rows(self, sql, args=()):
        with self.lock, self.connect() as c:
            return [dict(r) for r in c.execute(sql, args).fetchall()]
    def put(self, table, id, data, created_at=None):
        if table not in ("papers", "attempts", "jobs"):
            raise ValueError("invalid table")
        if created_at and table != "jobs":
            self.execute(f"INSERT OR REPLACE INTO {table}(id,data,created_at) VALUES(?,?,?)", (id, dumps(data), created_at))
        else:
            self.execute(f"INSERT OR REPLACE INTO {table}(id,data) VALUES(?,?)", (id, dumps(data)))
    def get(self, table, id):
        if table not in ("papers", "attempts", "jobs"):
            raise ValueError("invalid table")
        rows = self.rows(f"SELECT data FROM {table} WHERE id=?", (id,))
        return json.loads(rows[0]["data"]) if rows else None
    def all(self, table):
        if table not in ("papers", "attempts", "jobs"):
            raise ValueError("invalid table")
        order = "created_at DESC, rowid DESC" if table != "jobs" else "rowid DESC"
        return [json.loads(r["data"]) for r in self.rows(f"SELECT data FROM {table} ORDER BY {order}")]
    def setting(self, key, value=None):
        if value is not None:
            self.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (key, dumps(value)))
            return value
        rows = self.rows("SELECT value FROM settings WHERE key=?", (key,))
        return json.loads(rows[0]["value"]) if rows else None
    def provider(self, id, private=False):
        rows = self.rows("SELECT * FROM providers WHERE id=?", (id,))
        if not rows:
            return None
        p = json.loads(rows[0]["config"])
        p.update(id=id, has_key=bool(rows[0]["secret"]))
        if private:
            p["api_key"] = self.cipher.decrypt(rows[0]["secret"].encode()).decode() if rows[0]["secret"] else ""
        return p
    def providers(self):
        return [self.provider(r["id"]) for r in self.rows("SELECT id FROM providers ORDER BY rowid")]
    def save_provider(self, id, p):
        key = p.pop("api_key")
        old = self.rows("SELECT secret FROM providers WHERE id=?", (id,))
        secret = self.cipher.encrypt(key.encode()).decode() if key else (old[0]["secret"] if old else "")
        self.execute("INSERT OR REPLACE INTO providers VALUES(?,?,?)", (id, dumps(p), secret))
