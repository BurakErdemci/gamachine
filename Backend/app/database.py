import sqlite3
import json
import logging
import os
import queue
import threading
import time
from contextlib import closing
from datetime import datetime, timedelta
import bcrypt
from typing import Callable, List, Dict, Any, Optional, Tuple
from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)


class _LedgerWriter:
    """The one thread that writes approval-ledger rows.

    The ledger is written when a card closes, on the answer path; a SQLite
    lock held elsewhere kept the answer waiting ~7 s (Codex remote audit,
    28 Sep 2026). The ledger is a metric and the decision is what matters, so
    a row that does not fit the queue or fails to write is logged and dropped.
    """

    QUEUE_SIZE = 1000

    def __init__(self):
        self._queue: "queue.Queue" = queue.Queue(maxsize=self.QUEUE_SIZE)
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None

    def submit(self, fn: Callable[..., Any], *args: Any) -> bool:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="approval-ledger",
                                                daemon=True)
                self._thread.start()
            try:
                self._queue.put_nowait((fn, args))
            except queue.Full:
                logger.warning("[ledger] queue full; row dropped")
                return False
        return True

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is None:
                    # submit() enqueues under the same lock, so a row lands either
                    # before this check (and is drained) or after _thread is
                    # cleared (and starts a new writer); never stranded behind us.
                    with self._lock:
                        if self._queue.empty():
                            if self._thread is threading.current_thread():
                                self._thread = None
                            return
                    continue
                fn, args = item
                try:
                    fn(*args)
                except Exception as exc:
                    logger.warning("[ledger] row not written: %s", exc)
            finally:
                self._queue.task_done()

    def flush(self, timeout: float) -> bool:
        """Wait until every queued row is written (or dropped); False on timeout."""
        deadline = time.monotonic() + timeout
        done = self._queue.all_tasks_done
        with done:
            while self._queue.unfinished_tasks:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                done.wait(remaining)
        return True

    def shutdown(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        flushed = self.flush(timeout)
        with self._lock:
            thread = self._thread
            if thread is None or not thread.is_alive():
                return flushed
            try:
                self._queue.put_nowait(None)
            except queue.Full:
                return False
        # Joined outside the lock: the writer takes it to decide whether to exit.
        thread.join(max(0.0, deadline - time.monotonic()))
        return flushed


LEDGER_WRITER = _LedgerWriter()
# How long a ledger read waits for queued rows, so a reader sees its own writes.
LEDGER_READ_WAIT_S = 2.0


class LedgerNotCaughtUp(sqlite3.OperationalError):
    """Queued ledger rows were not written within the read deadline."""


def flush_ledger(timeout: float = 5.0) -> bool:
    return LEDGER_WRITER.flush(timeout)


def shutdown_ledger_writer(timeout: float = 2.0) -> bool:
    return LEDGER_WRITER.shutdown(timeout)


class DatabaseManager:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.session_ttl_minutes = int(os.environ.get("SESSION_TTL_MINUTES", "1440"))
        self._fernet = self._build_fernet()
        self._create_tables()
        # Drop legacy auth tables from existing DBs
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute("DROP TABLE IF EXISTS sessions")
            conn.execute("DROP TABLE IF EXISTS oauth_states")
            conn.execute("DROP TABLE IF EXISTS oauth_completions")
        self._seed_local_user()

    def _build_fernet(self) -> Fernet:
        # 1. Explicit env var — CI veya ileri kullanıcı override'ı
        env_key = os.environ.get("API_KEY_ENCRYPTION_KEY")
        if env_key:
            return Fernet(env_key.encode("utf-8"))

        # 2. DB dizinindeki kalıcı anahtar dosyası (PRIMARY).
        #    NOT: keyring (macOS Keychain) PRIMARY olarak KULLANILMAZ — imzasız/
        #    paketlenmiş binary her build'de farklı ad-hoc imza aldığı için Keychain
        #    item'ını okuyamayıp her açılışta yeni anahtar üretiyordu; bu yüzden
        #    DB'de duran şifreli API key'ler çözülemiyor ve "API key not valid"
        #    hatası veriyordu. Dosya tabanlı anahtar aynı DB dizininde kaldığı
        #    sürece deterministik ve kod imzasından bağımsızdır.
        db_dir = os.path.dirname(self.db_path) or "."
        os.makedirs(db_dir, exist_ok=True)
        key_path = os.path.join(db_dir, "api_key_fernet.key")

        if os.path.exists(key_path):
            with open(key_path, "rb") as f:
                return Fernet(f.read().strip())

        # 2b. İlk kurulum: eski keyring anahtarı varsa onu dosyaya TAŞI ki daha
        #     önce o anahtarla şifrelenmiş kayıtlar çözülmeye devam etsin; yoksa
        #     yeni anahtar üret.
        key = None
        try:
            import keyring
            stored = keyring.get_password("unity-architect-ai", "fernet-key")
            if stored:
                key = stored.encode("utf-8")
        except Exception:
            pass
        if key is None:
            key = Fernet.generate_key()

        with open(key_path, "wb") as f:
            f.write(key)
        try:
            os.chmod(key_path, 0o600)
        except OSError:
            pass
        return Fernet(key)

    def _encrypt_api_key(self, api_key: str) -> str:
        encrypted = self._fernet.encrypt(api_key.encode("utf-8")).decode("utf-8")
        return f"enc:{encrypted}"

    def _decrypt_api_key(self, stored_value: str) -> str:
        if not stored_value:
            return ""
        if not stored_value.startswith("enc:"):
            return stored_value
        token = stored_value[4:].encode("utf-8")
        try:
            return self._fernet.decrypt(token).decode("utf-8")
        except InvalidToken:
            return ""

    def _create_tables(self):
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            cursor = conn.cursor()
            # Kullanıcılar
            cursor.execute('CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password_hash TEXT, email TEXT, avatar_url TEXT, oauth_provider TEXT, oauth_id TEXT)')
            # Migration: OAuth alanlarını mevcut tabloya ekle
            for col, col_type in [("email", "TEXT"), ("avatar_url", "TEXT"), ("oauth_provider", "TEXT"), ("oauth_id", "TEXT")]:
                try:
                    cursor.execute(f"ALTER TABLE users ADD COLUMN {col} {col_type}")
                except sqlite3.OperationalError:
                    pass
            # AI Ayarları
            cursor.execute('''CREATE TABLE IF NOT EXISTS ai_configs (
                user_id INTEGER PRIMARY KEY, provider_type TEXT, model_name TEXT, api_key TEXT, use_multi_agent INTEGER DEFAULT 1,
                FOREIGN KEY (user_id) REFERENCES users (id))''')
            self._migrate_ai_configs_table(conn)
            # CLI oturum kimlikleri: "kaldığın yerden devam"ın TEK kalıcı kaydı.
            #
            # Neden gerekli (ölçüldü 8 Ağu 2026): Claude/Codex oturumları yalnız
            # RAM'de yaşıyordu. Uygulama kapanınca kimlik ölüyor, sonraki mesajda
            # DB transcript'i yeniden enjekte ediliyor ve o enjeksiyon 20.000
            # karakterle sınırlı → gerçek bir sohbette 48 mesajın 17'si geçti,
            # %71 karakter kayboldu. CLI ise tam transcript'i kendi diskinde
            # tutuyor; tek eksik onu geri çağıracak kimlikti.
            #
            # `workspace` ŞART: Claude Code oturumları proje diziniyle anahtarlı.
            # Kullanıcı klasör değiştirdiyse eski kimlikle resume etmek yanlış
            # projenin geçmişini açardı — eşleşmiyorsa kimlik kullanılmıyor.
            cursor.execute('''CREATE TABLE IF NOT EXISTS cli_sessions (
                conversation_id INTEGER NOT NULL,
                provider TEXT NOT NULL,
                session_id TEXT NOT NULL,
                workspace TEXT,
                updated_at TEXT,
                PRIMARY KEY (conversation_id, provider))''')
            # Eski Geçmiş (geriye uyumluluk)
            cursor.execute('''CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, timestamp TEXT, title TEXT,
                intent TEXT, original_code TEXT, ai_suggestion TEXT, smells TEXT,
                FOREIGN KEY (user_id) REFERENCES users (id))''')
            # --- YENİ: Sohbetler ---
            cursor.execute('''CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT DEFAULT 'Yeni Sohbet',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users (id))''')
            # --- YENİ: Mesajlar ---
            cursor.execute('''CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                smells_json TEXT DEFAULT '[]',
                timestamp TEXT NOT NULL,
                FOREIGN KEY (conversation_id) REFERENCES conversations (id) ON DELETE CASCADE)''')
            # API Key Kasası — provider başına kalıcı key saklama
            cursor.execute('''CREATE TABLE IF NOT EXISTS api_keys (
                user_id INTEGER NOT NULL,
                provider_type TEXT NOT NULL,
                api_key TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (user_id, provider_type),
                FOREIGN KEY (user_id) REFERENCES users (id))''')
            # App-wide settings that must survive restarts but belong to no
            # conversation (today: the global approval mode).
            cursor.execute('''CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL)''')
            # Migration: conversations tablosuna memory_summary sütunu ekle
            try:
                cursor.execute("ALTER TABLE conversations ADD COLUMN memory_summary TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass  # Sütun zaten var
            # Branching (tabs): parent_id is always the ROOT's id (one level),
            # fork_at the id of the last message copied from the source.
            # side_of: a read-only side chat's main chat. parent_id stays NULL on
            # a side row so `_touch` never bumps the main chat's family.
            # title_source: 'auto' (first-message cut, branch default, AI title)
            # or 'user' (renamed by hand, never retitled); existing rows count
            # as 'auto'. auto_title_runs caps AI title generations per chat
            # (Burak, 27 Sep 2026: at most two). copied_until: the last message
            # id a branch got as a copy; fork_at names the SOURCE's last id and
            # the copies get new, larger ids, so it cannot tell them apart.
            for col_def in ("parent_id INTEGER", "fork_at INTEGER",
                            "hidden INTEGER NOT NULL DEFAULT 0", "side_of INTEGER",
                            "title_source TEXT NOT NULL DEFAULT 'auto'",
                            "auto_title_runs INTEGER NOT NULL DEFAULT 0",
                            "copied_until INTEGER", "workspace TEXT"):
                try:
                    cursor.execute(f"ALTER TABLE conversations ADD COLUMN {col_def}")
                except sqlite3.OperationalError:
                    pass
            # Per-chat model (owner request): the provider/model a chat runs
            # with, named as in ai_configs. NULL = never stamped; resolution
            # and fallbacks live in agentic/chat_model.py.
            for col_def in ("provider_type TEXT", "model_name TEXT"):
                try:
                    cursor.execute(f"ALTER TABLE conversations ADD COLUMN {col_def}")
                except sqlite3.OperationalError:
                    pass
            # Which agent wrote an assistant message (Burak, 27 Sep 2026): the
            # header used to show the chat's CURRENT model on every answer, so a
            # chat moved from OpenCode to Codex relabelled OpenCode's answers.
            # `provider` is the agent family (claude/codex/agy/opencode/...,
            # `api-<name>` for API loops), `model` the id the turn ran with.
            # Rows written before this stay NULL and show no model at all.
            for col_def in ("provider TEXT", "model TEXT"):
                try:
                    cursor.execute(f"ALTER TABLE messages ADD COLUMN {col_def}")
                except sqlite3.OperationalError:
                    pass
            # Chat mailbox (agentic/mailbox.py). No FOREIGN KEY: connections
            # here do not enable FKs, so `_delete_rows` removes the rows itself.
            cursor.execute('''CREATE TABLE IF NOT EXISTS mailbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_conv INTEGER NOT NULL,
                to_conv INTEGER NOT NULL,
                body TEXT NOT NULL,
                status TEXT NOT NULL,
                gate_id TEXT,
                depth INTEGER NOT NULL DEFAULT 0,
                created_at TEXT,
                delivered_at TEXT)''')
            cursor.execute(
                'CREATE INDEX IF NOT EXISTS idx_mailbox_to_status ON mailbox (to_conv, status)')
            # expects_reply: a note sent from a turn the user started (depth 1)
            # always expects a reply, and if the receiver ends its turn without
            # sending one its last message is forwarded (Burak, 27 Sep 2026);
            # auto_forwarded marks that forwarded note for the renderer.
            for col_def in ("expects_reply INTEGER NOT NULL DEFAULT 0",
                            "auto_forwarded INTEGER NOT NULL DEFAULT 0"):
                try:
                    cursor.execute(f"ALTER TABLE mailbox ADD COLUMN {col_def}")
                except sqlite3.OperationalError:
                    pass
            # Approval ledger (agentic/cards.py, docs/remote-control.md): one
            # row per closed card. An audit log, so deleting a chat keeps its
            # rows untouched: they hold no message text (params only as a
            # sha256), and conversation ids are AUTOINCREMENT, never reused, so
            # a kept id cannot point at a different chat later. No FOREIGN KEY
            # for the same reason.
            cursor.execute('''CREATE TABLE IF NOT EXISTS approval_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                at TEXT NOT NULL,
                card_id TEXT NOT NULL,
                conversation_id INTEGER,
                kind TEXT,
                tool TEXT,
                params_hash TEXT,
                approval_mode TEXT,
                decision TEXT,
                device TEXT,
                outcome TEXT NOT NULL)''')
            cursor.execute(
                'CREATE INDEX IF NOT EXISTS idx_approval_ledger_at ON approval_ledger (at)')
            # The ledger sees only carded actions; messages lose history on
            # compaction/delete (maker profile plan, 2 Oct 2026).
            cursor.execute('''CREATE TABLE IF NOT EXISTS activity_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                at TEXT NOT NULL,
                kind TEXT NOT NULL,
                conversation_id INTEGER,
                provider TEXT,
                model TEXT,
                tool TEXT,
                detail TEXT)''')
            cursor.execute(
                'CREATE INDEX IF NOT EXISTS idx_activity_kind_at ON activity_events (kind, at)')
            conn.commit()

    def _migrate_ai_configs_table(self, conn: sqlite3.Connection):
        """Legacy multi-agent kolonlarını temizleyip güncel ai_configs şemasını korur."""
        cols = conn.execute("PRAGMA table_info(ai_configs)").fetchall()
        col_names = [col[1] for col in cols]

        if not cols:
            return

        desired = ["user_id", "provider_type", "model_name", "api_key", "use_multi_agent"]
        if col_names == desired:
            return

        if "use_multi_agent" not in col_names:
            try:
                conn.execute("ALTER TABLE ai_configs ADD COLUMN use_multi_agent INTEGER DEFAULT 1")
            except sqlite3.OperationalError:
                pass

        conn.execute('''CREATE TABLE IF NOT EXISTS ai_configs_v2 (
            user_id INTEGER PRIMARY KEY,
            provider_type TEXT,
            model_name TEXT,
            api_key TEXT,
            use_multi_agent INTEGER DEFAULT 1,
            FOREIGN KEY (user_id) REFERENCES users (id))''')
        conn.execute("DELETE FROM ai_configs_v2")
        conn.execute(
            '''INSERT INTO ai_configs_v2 (user_id, provider_type, model_name, api_key, use_multi_agent)
               SELECT user_id, provider_type, model_name, api_key, COALESCE(use_multi_agent, 1)
               FROM ai_configs'''
        )
        conn.execute("DROP TABLE ai_configs")
        conn.execute("ALTER TABLE ai_configs_v2 RENAME TO ai_configs")

    # ===================== AUTH =====================
    def create_user(self, username: str, password: str) -> bool:
        hashed = bcrypt.hashpw(password[:72].encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        try:
            with closing(sqlite3.connect(self.db_path)) as conn, conn:
                conn.execute('INSERT INTO users (username, password_hash) VALUES (?, ?)', (username, hashed))
            return True
        except Exception:
            return False

    def verify_user(self, username: str, password: str) -> Optional[Tuple]:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            user = conn.execute('SELECT id, username, password_hash FROM users WHERE username = ?', (username,)).fetchone()
            if user and bcrypt.checkpw(password[:72].encode("utf-8"), user[2].encode("utf-8")):
                return user
            return None

    def get_user_profile(self, user_id: int) -> Optional[Dict[str, Any]]:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            row = conn.execute(
                'SELECT id, username, email, avatar_url FROM users WHERE id = ?',
                (user_id,),
            ).fetchone()
            if not row:
                return None
            return {
                "user_id": row[0],
                "username": row[1],
                "email": row[2] or "",
                "avatar": row[3] or "",
            }

    def _seed_local_user(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute(
                "INSERT OR IGNORE INTO users (id, username, password_hash, email) "
                "VALUES (1, 'local', '', 'local@localhost')"
            )

    # ===================== AI CONFIG =====================
    def save_ai_config(self, user_id: int, p_type: str, m_name: str, key: str) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute('INSERT OR REPLACE INTO ai_configs (user_id, provider_type, model_name, api_key, use_multi_agent) VALUES (?, ?, ?, ?, 1)',
                         (user_id, p_type, m_name, key))
            conn.commit()

    def get_ai_config(self, user_id: int) -> Tuple[str, str, str, bool]:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            res = conn.execute('SELECT provider_type, model_name, api_key, use_multi_agent FROM ai_configs WHERE user_id = ?', (user_id,)).fetchone()
            if res:
                return (res[0], res[1], res[2], bool(res[3]))
            return ("subscription", "claude-sonnet-4-6", "", False)

    # ===================== API KEY KASASI =====================
    def save_api_key(self, user_id: int, provider_type: str, api_key: str) -> None:
        """Provider için API key'i kaydet/güncelle."""
        api_key = api_key.strip() if api_key else api_key
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        encrypted_key = self._encrypt_api_key(api_key)
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute(
                'INSERT OR REPLACE INTO api_keys (user_id, provider_type, api_key, updated_at) VALUES (?, ?, ?, ?)',
                (user_id, provider_type, encrypted_key, now)
            )
            conn.commit()

    def get_api_key(self, user_id: int, provider_type: str) -> Optional[str]:
        """Provider için kaydedilmiş API key'i getir."""
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            row = conn.execute(
                'SELECT api_key FROM api_keys WHERE user_id = ? AND provider_type = ?',
                (user_id, provider_type)
            ).fetchone()
            if not row:
                return None
            api_key = self._decrypt_api_key(row[0])
            if api_key and not row[0].startswith("enc:"):
                self.save_api_key(user_id, provider_type, api_key)
            return api_key or None

    def get_all_api_keys(self, user_id: int) -> Dict[str, str]:
        """Kullanıcının tüm provider key'lerini döndür. {provider_type: masked_key}"""
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            rows = conn.execute(
                'SELECT provider_type, api_key FROM api_keys WHERE user_id = ?',
                (user_id,)
            ).fetchall()
            result = {}
            for provider_type, stored_key in rows:
                api_key = self._decrypt_api_key(stored_key)
                if api_key:
                    result[provider_type] = api_key
                    if not stored_key.startswith("enc:"):
                        self.save_api_key(user_id, provider_type, api_key)
            return result

    def delete_api_key(self, user_id: int, provider_type: str) -> None:
        """Provider için kaydedilmiş API key'i sil."""
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute(
                'DELETE FROM api_keys WHERE user_id = ? AND provider_type = ?',
                (user_id, provider_type)
            )
            conn.commit()

    # ===================== ESKİ GEÇMİŞ (Geriye Uyumluluk) =====================
    def save_analysis(self, user_id: int, title: str, intent: str, code: str, suggestion: str, smells: list) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute('INSERT INTO history (user_id, timestamp, title, intent, original_code, ai_suggestion, smells) VALUES (?, ?, ?, ?, ?, ?, ?)',
                         (user_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), title, intent, code, suggestion, json.dumps(smells)))

    def get_user_history(self, user_id: int) -> List[Tuple]:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            return conn.execute('SELECT id, timestamp, title, intent FROM history WHERE user_id = ? ORDER BY id DESC', (user_id,)).fetchall()

    def get_analysis_detail(self, item_id: int) -> Optional[Dict[str, Any]]:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            res = conn.execute('SELECT original_code, ai_suggestion, smells FROM history WHERE id = ?', (item_id,)).fetchone()
            return {"code": res[0], "suggestion": res[1], "smells": json.loads(res[2])} if res else None

    def get_analysis_owner(self, item_id: int) -> Optional[int]:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            row = conn.execute('SELECT user_id FROM history WHERE id = ?', (item_id,)).fetchone()
            return row[0] if row else None

    def delete_analysis(self, item_id: int) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute('DELETE FROM history WHERE id = ?', (item_id,))
            conn.commit()

    def rename_analysis(self, item_id: int, new_title: str) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute('UPDATE history SET title = ? WHERE id = ?', (new_title, item_id))
            conn.commit()

    # ===================== YENİ: SOHBETLER =====================
    def create_conversation(self, user_id: int, title: str = "Yeni Sohbet", workspace: Optional[str] = None) -> int:
        self._ensure_workspace_table()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            if not isinstance(workspace, str) or not workspace:
                row = conn.execute(
                    'SELECT path FROM workspaces WHERE user_id = ? ORDER BY last_accessed DESC, id DESC LIMIT 1',
                    (user_id,)
                ).fetchone()
                workspace = row[0] if row else None
            cursor = conn.execute(
                'INSERT INTO conversations (user_id, title, created_at, updated_at, workspace) '
                'VALUES (?, ?, ?, ?, ?)',
                (user_id, title, now, now, workspace)
            )
            conn.commit()
            return cursor.lastrowid

    def get_user_conversations(self, user_id: int) -> List[Dict[str, Any]]:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            rows = conn.execute(
                'SELECT id, title, created_at, updated_at, parent_id, hidden, title_source '
                'FROM conversations WHERE user_id = ? AND side_of IS NULL ORDER BY updated_at DESC',
                (user_id,)
            ).fetchall()
            # title_source lets the renderer drop an in-flight AI title frame
            # for a chat the user renamed (Codex eveaudit, 27 Sep 2026).
            return [{"id": r[0], "title": r[1], "created_at": r[2], "updated_at": r[3],
                     "parent_id": r[4], "hidden": bool(r[5]), "title_source": r[6] or "auto"}
                    for r in rows]

    def get_conversation_owner(self, conv_id: int) -> Optional[int]:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            row = conn.execute('SELECT user_id FROM conversations WHERE id = ?', (conv_id,)).fetchone()
            return row[0] if row else None

    def get_conversation_model(self, conv_id: int) -> Optional[Tuple[str, str]]:
        """The chat's stored (provider_type, model_name); None if never stamped."""
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            row = conn.execute(
                'SELECT provider_type, model_name FROM conversations WHERE id = ?', (conv_id,)
            ).fetchone()
        if not row or not row[0]:
            return None
        return (row[0], row[1] or "")

    def set_conversation_model(self, conv_id: int, provider_type: str, model_name: str,
                               only_if_unset: bool = False) -> bool:
        """Store the chat's model; False if the chat is gone (or, with
        `only_if_unset`, already has one). updated_at is left alone: picking a
        model is not chat activity and must not reorder the sidebar."""
        sql = 'UPDATE conversations SET provider_type = ?, model_name = ? WHERE id = ?'
        if only_if_unset:
            sql += ' AND provider_type IS NULL'
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            cur = conn.execute(sql, (provider_type, model_name, conv_id))
            conn.commit()
            return cur.rowcount > 0

    def get_latest_message_agent(self, conv_id: int) -> Optional[Tuple[str, str]]:
        """(provider, model) of the chat's newest message that names both."""
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            row = conn.execute(
                'SELECT provider, model FROM messages WHERE conversation_id = ? '
                'AND provider IS NOT NULL AND model IS NOT NULL ORDER BY id DESC LIMIT 1',
                (conv_id,)
            ).fetchone()
        return (row[0], row[1]) if row else None

    @staticmethod
    def _touch(conn: sqlite3.Connection, conv_id: int, now: str) -> None:
        """Bump updated_at of the chat AND its root, so a branch's activity keeps
        the whole family on top of the sidebar (which lists roots only)."""
        conn.execute('UPDATE conversations SET updated_at = ? WHERE id = ?', (now, conv_id))
        conn.execute(
            'UPDATE conversations SET updated_at = ? '
            'WHERE id = (SELECT parent_id FROM conversations WHERE id = ?)',
            (now, conv_id))

    def get_conversation_parent(self, conv_id: int) -> Optional[int]:
        """parent_id of the chat: None for a root (or an unknown id)."""
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            row = conn.execute('SELECT parent_id FROM conversations WHERE id = ?', (conv_id,)).fetchone()
            return row[0] if row else None

    def get_branch_ids(self, root_id: int) -> List[int]:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            rows = conn.execute(
                'SELECT id FROM conversations WHERE parent_id = ? ORDER BY id ASC', (root_id,)
            ).fetchall()
            return [r[0] for r in rows]

    def create_branch(self, source_id: int, suffix: str = " · dal") -> Optional[Dict[str, Any]]:
        """Copy a chat into a new branch under its ROOT; None if the source is gone.

        No cli_sessions row is written on purpose: the branch's first turn must
        get the handoff transcript, not resume the source's CLI session.
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            # IMMEDIATE: the root check below and the insert see one snapshot,
            # so a family delete on another connection cannot land in between.
            conn.execute('BEGIN IMMEDIATE')
            src = conn.execute(
                'SELECT user_id, title, memory_summary, parent_id, side_of, provider_type, model_name, workspace '
                'FROM conversations WHERE id = ?',
                (source_id,)
            ).fetchone()
            if not src or src[4] is not None:
                return None
            user_id, title, memory_summary, parent_id, _, provider_type, model_name, workspace = src
            root_id = parent_id or source_id
            if parent_id is not None and conn.execute(
                'SELECT 1 FROM conversations WHERE id = ?', (root_id,)
            ).fetchone() is None:
                # A branch whose root is already deleted: a copy would be an orphan.
                return None
            title = title or ""
            new_title = title if title.endswith(suffix) else title + suffix
            # Bounding the copy by fork_at makes the recorded fork point exact
            # even if another connection appends to the source meanwhile.
            fork_at = conn.execute(
                'SELECT MAX(id) FROM messages WHERE conversation_id = ?', (source_id,)
            ).fetchone()[0]
            cur = conn.execute(
                'INSERT INTO conversations (user_id, title, created_at, updated_at, memory_summary, '
                'parent_id, fork_at, hidden, provider_type, model_name, workspace) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)',
                (user_id, new_title, now, now, memory_summary or "", root_id, fork_at,
                 provider_type, model_name, workspace)
            )
            new_id = cur.lastrowid
            if fork_at is not None:
                conn.execute(
                    'INSERT INTO messages (conversation_id, role, content, smells_json, timestamp, '
                    'provider, model) '
                    'SELECT ?, role, content, smells_json, timestamp, provider, model FROM messages '
                    'WHERE conversation_id = ? AND id <= ? ORDER BY id',
                    (new_id, source_id, fork_at)
                )
                conn.execute(
                    'UPDATE conversations SET copied_until = '
                    '(SELECT MAX(id) FROM messages WHERE conversation_id = ?) WHERE id = ?',
                    (new_id, new_id))
            conn.execute('UPDATE conversations SET updated_at = ? WHERE id = ?', (now, root_id))
            conn.commit()
        return {"id": new_id, "title": new_title, "parent_id": root_id, "hidden": False,
                "created_at": now, "updated_at": now}

    def set_conversation_hidden(self, conv_id: int, hidden: bool) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute('UPDATE conversations SET hidden = ? WHERE id = ?', (1 if hidden else 0, conv_id))
            conn.commit()

    def rename_conversation(self, conv_id: int, new_title: str) -> None:
        """A rename by the user: the title is theirs from now on."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute("UPDATE conversations SET title = ?, title_source = 'user' WHERE id = ?",
                         (new_title, conv_id))
            self._touch(conn, conv_id, now)
            conn.commit()

    def set_auto_title(self, conv_id: int, title: str) -> bool:
        """Write a generated title unless the user renamed the chat; True if written.

        The check and the write are one statement, so a rename that lands
        while a title job runs always wins.
        """
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            cur = conn.execute(
                "UPDATE conversations SET title = ? WHERE id = ? AND title_source = 'auto'",
                (title, conv_id))
            conn.commit()
            return cur.rowcount == 1

    def get_title_state(self, conv_id: int) -> Optional[Dict[str, Any]]:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            row = conn.execute(
                'SELECT user_id, title, title_source, auto_title_runs, parent_id, copied_until, side_of '
                'FROM conversations WHERE id = ?', (conv_id,)).fetchone()
        if not row:
            return None
        return {"user_id": row[0], "title": row[1] or "", "title_source": row[2] or "auto",
                "auto_title_runs": int(row[3] or 0), "parent_id": row[4], "copied_until": row[5],
                "side_of": row[6]}

    def count_own_assistant_replies(self, conv_id: int) -> int:
        """Assistant messages of the chat; a branch counts only those after its copy."""
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM messages m JOIN conversations c ON c.id = m.conversation_id "
                "WHERE m.conversation_id = ? AND m.role = 'assistant' "
                "AND (c.copied_until IS NULL OR m.id > c.copied_until)", (conv_id,)).fetchone()
        return int(row[0] or 0)

    def claim_auto_title_run(self, conv_id: int, max_runs_before: int) -> bool:
        """Count one title generation if fewer than `max_runs_before` ran; True if claimed.

        Claimed before the model is called, so a failed or timed-out call
        still spends one of the chat's generations.
        """
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            cur = conn.execute(
                "UPDATE conversations SET auto_title_runs = auto_title_runs + 1 "
                "WHERE id = ? AND title_source = 'auto' AND side_of IS NULL "
                "AND auto_title_runs < ?", (conv_id, max_runs_before))
            conn.commit()
            return cur.rowcount == 1

    def delete_conversation(self, conv_id: int) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute('PRAGMA foreign_keys = ON')
            conn.execute('DELETE FROM messages WHERE conversation_id = ?', (conv_id,))
            # Kimlik de gitsin: sohbet yokken `cli_sessions` satırı yetim kalıyordu.
            # `conversations.id` AUTOINCREMENT olduğu için id yeniden kullanılmıyor,
            # yani yanlış geçmiş gösterme riski YOK — bu yalnız çöp temizliği.
            conn.execute('DELETE FROM cli_sessions WHERE conversation_id = ?', (conv_id,))
            conn.execute('DELETE FROM mailbox WHERE from_conv = ? OR to_conv = ?', (conv_id, conv_id))
            conn.execute('DELETE FROM conversations WHERE id = ?', (conv_id,))
            self._delete_side_rows_of(conn, [conv_id])
            conn.commit()

    @staticmethod
    def _delete_side_rows_of(conn: sqlite3.Connection, main_ids: List[int]) -> List[int]:
        """Delete the side chats of `main_ids` inside the caller's transaction."""
        if not main_ids:
            return []
        marks = ','.join('?' * len(main_ids))
        side_ids = [r[0] for r in conn.execute(
            f'SELECT id FROM conversations WHERE side_of IN ({marks}) ORDER BY id ASC',
            main_ids)]
        DatabaseManager._delete_rows(conn, side_ids)
        return side_ids

    @staticmethod
    def _delete_rows(conn: sqlite3.Connection, ids: List[int]) -> None:
        if not ids:
            return
        marks = ','.join('?' * len(ids))
        conn.execute(f'DELETE FROM messages WHERE conversation_id IN ({marks})', ids)
        conn.execute(f'DELETE FROM cli_sessions WHERE conversation_id IN ({marks})', ids)
        conn.execute(
            f'DELETE FROM mailbox WHERE from_conv IN ({marks}) OR to_conv IN ({marks})',
            list(ids) + list(ids))
        conn.execute(f'DELETE FROM conversations WHERE id IN ({marks})', ids)

    def delete_conversation_family(self, conv_id: int) -> List[int]:
        """Delete a chat in one transaction; a root takes its branches with it.

        Returns the deleted ids (root first, then branches by id); [] if the
        chat is gone. One transaction so no branch can be copied under a root
        whose family is half deleted. Side chats of every deleted id go too;
        `delete_conversation_family_and_sides` also returns their ids.
        """
        return self.delete_conversation_family_and_sides(conv_id)[0]

    def delete_conversation_family_and_sides(self, conv_id: int) -> Tuple[List[int], List[int]]:
        """`delete_conversation_family`, plus the ids of the side chats it deleted."""
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute(
                'SELECT parent_id FROM conversations WHERE id = ?', (conv_id,)
            ).fetchone()
            if row is None:
                return [], []
            ids = [conv_id]
            if row[0] is None:
                ids += [r[0] for r in conn.execute(
                    'SELECT id FROM conversations WHERE parent_id = ? ORDER BY id ASC',
                    (conv_id,))]
            self._delete_rows(conn, ids)
            side_ids = self._delete_side_rows_of(conn, ids)
            conn.commit()
            return ids, side_ids

    # ===================== SIDE CHATS =====================
    def create_side_chat(self, main_id: int, user_id: int) -> Optional[int]:
        """The open side chat of `main_id`, created if there is none.

        None if the main chat is gone, not the user's, or itself a side chat.
        No messages are copied and no cli_sessions row is written: the side
        chat's first turn gets the main chat's transcript as handoff context,
        never a resume or fork of the main chat's CLI session.
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute(
                'SELECT user_id, side_of, workspace FROM conversations WHERE id = ?', (main_id,)
            ).fetchone()
            if row is None or row[0] != user_id or row[1] is not None:
                return None
            existing = conn.execute(
                'SELECT id FROM conversations WHERE side_of = ? ORDER BY id DESC LIMIT 1',
                (main_id,)
            ).fetchone()
            if existing:
                return existing[0]
            cur = conn.execute(
                'INSERT INTO conversations (user_id, title, created_at, updated_at, '
                'parent_id, hidden, side_of, workspace) VALUES (?, ?, ?, ?, NULL, 1, ?, ?)',
                (user_id, "Yan soru", now, now, main_id, row[2])
            )
            conn.commit()
            return cur.lastrowid

    def get_side_of(self, conv_id: int) -> Optional[int]:
        """The main chat of a side chat; None for any other id (or an unknown one)."""
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            row = conn.execute(
                'SELECT side_of FROM conversations WHERE id = ?', (conv_id,)
            ).fetchone()
            return row[0] if row else None

    def delete_side_chat(self, side_id: int) -> bool:
        """Delete one side chat; False if `side_id` is not a side chat."""
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute(
                'SELECT side_of FROM conversations WHERE id = ?', (side_id,)
            ).fetchone()
            if row is None or row[0] is None:
                return False
            self._delete_rows(conn, [side_id])
            conn.commit()
            return True

    def sweep_side_chats(self, older_than_s: float, busy_ids=()) -> List[int]:
        """Delete side chats idle for at least `older_than_s` seconds; returns their ids.

        `older_than_s <= 0` deletes every side chat (startup). Ids in `busy_ids`
        (a turn in flight) are kept whatever their age.
        """
        busy = set(busy_ids or ())
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            if older_than_s <= 0:
                rows = conn.execute(
                    'SELECT id FROM conversations WHERE side_of IS NOT NULL').fetchall()
            else:
                cutoff = (datetime.now() - timedelta(seconds=older_than_s)).strftime("%Y-%m-%d %H:%M:%S")
                rows = conn.execute(
                    'SELECT id FROM conversations WHERE side_of IS NOT NULL AND updated_at <= ?',
                    (cutoff,)).fetchall()
            ids = [r[0] for r in rows if r[0] not in busy]
            self._delete_rows(conn, ids)
            conn.commit()
            return ids

    # ===================== APPROVAL LEDGER =====================
    _LEDGER_COLS = ("at", "card_id", "conversation_id", "kind", "tool", "params_hash",
                    "approval_mode", "decision", "device", "outcome")

    def record_card_resolution(self, row: Dict[str, Any]) -> bool:
        """Queue one ledger row; False when it was dropped. Returns at once:
        the insert runs on the ledger thread (see `_LedgerWriter`)."""
        values = [row.get(c) for c in self._LEDGER_COLS]
        if not values[0]:
            values[0] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return LEDGER_WRITER.submit(self._insert_card_resolution, values)

    def flush_ledger(self, timeout: float = 5.0) -> bool:
        return LEDGER_WRITER.flush(timeout)

    def _insert_card_resolution(self, values: List[Any]) -> int:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            cur = conn.execute(
                f'INSERT INTO approval_ledger ({", ".join(self._LEDGER_COLS)}) '
                f'VALUES ({", ".join("?" * len(self._LEDGER_COLS))})', values)
            conn.commit()
            return cur.lastrowid

    def _ledger_read_connection(self) -> sqlite3.Connection:
        """The queue wait and SQLite's own lock wait share one deadline; stacked,
        a held lock kept a read ~9.4 s (Codex verification, 28 Sep 2026). A lock
        still held at the deadline raises, so it never reads as "no cards".

        A RESERVED lock blocks the queued insert but lets SELECT through, so a
        read after a timed-out flush returned 0 rows (Codex, 28 Sep 2026); it
        raises instead."""
        started = time.monotonic()
        if not LEDGER_WRITER.flush(LEDGER_READ_WAIT_S):
            raise LedgerNotCaughtUp("approval ledger is not caught up (writer busy)")
        spent = time.monotonic() - started
        return sqlite3.connect(self.db_path, timeout=max(0.1, LEDGER_READ_WAIT_S - spent))

    def get_approval_ledger(self, since: Optional[str] = None,
                            until: Optional[str] = None) -> List[Dict[str, Any]]:
        where, args = self._ledger_window(since, until)
        with closing(self._ledger_read_connection()) as conn, conn:
            rows = conn.execute(
                f'SELECT {", ".join(self._LEDGER_COLS)} FROM approval_ledger{where} ORDER BY id',
                args).fetchall()
        return [dict(zip(self._LEDGER_COLS, r)) for r in rows]

    @staticmethod
    def _ledger_window(since: Optional[str], until: Optional[str]) -> Tuple[str, list]:
        clauses, args = [], []
        if since:
            clauses.append("at >= ?")
            args.append(since)
        if until:
            clauses.append("at < ?")
            args.append(until)
        return (" WHERE " + " AND ".join(clauses) if clauses else ""), args

    def approval_ledger_stats(self, since: Optional[str] = None,
                              until: Optional[str] = None) -> Dict[str, Any]:
        """Counts per outcome and per device in [since, until) ("YYYY-MM-DD HH:MM:SS").

        `timed_out_share` is the remote-control metric: timed-out cards over
        all closed cards in the window (None when there were none).
        """
        where, args = self._ledger_window(since, until)
        with closing(self._ledger_read_connection()) as conn, conn:
            rows = conn.execute(
                f"SELECT outcome, COALESCE(device, 'unknown') AS dev, COUNT(*) "
                f"FROM approval_ledger{where} GROUP BY outcome, dev", args
            ).fetchall()
        by_outcome: Dict[str, int] = {}
        by_device: Dict[str, int] = {}
        by_device_outcome: Dict[str, Dict[str, int]] = {}
        total = 0
        for outcome, device, n in rows:
            total += n
            by_outcome[outcome] = by_outcome.get(outcome, 0) + n
            by_device[device] = by_device.get(device, 0) + n
            by_device_outcome.setdefault(device, {})[outcome] = n
        timed_out = by_outcome.get("timed_out", 0)
        return {"since": since, "until": until, "total": total,
                "by_outcome": by_outcome, "by_device": by_device,
                "by_device_outcome": by_device_outcome,
                "timed_out_share": (timed_out / total) if total else None}

    # ===================== CHAT MAILBOX =====================
    _MAIL_COLS = ('id, from_conv, to_conv, body, status, gate_id, depth, created_at, delivered_at, '
                  'expects_reply, auto_forwarded')
    _MAIL_NCOLS = 11

    @staticmethod
    def _mail_row(r) -> Dict[str, Any]:
        return {"id": r[0], "from_conv": r[1], "to_conv": r[2], "body": r[3], "status": r[4],
                "gate_id": r[5], "depth": r[6], "created_at": r[7], "delivered_at": r[8],
                "expects_reply": bool(r[9]), "auto_forwarded": bool(r[10])}

    def get_conversation_title(self, conv_id: int) -> Optional[str]:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            row = conn.execute('SELECT title FROM conversations WHERE id = ?', (conv_id,)).fetchone()
            return (row[0] or "") if row else None

    def list_mail_chats(self, user_id: int) -> List[Dict[str, Any]]:
        """The user's chats a note can go to: every row but side chats."""
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            rows = conn.execute(
                'SELECT id, title, parent_id, hidden FROM conversations '
                'WHERE user_id = ? AND side_of IS NULL ORDER BY updated_at DESC, id DESC',
                (user_id,)).fetchall()
            return [{"id": r[0], "title": r[1] or "", "is_branch": r[2] is not None,
                     "parent_id": r[2], "hidden": bool(r[3])} for r in rows]

    def add_mail(self, from_conv: int, to_conv: int, body: str, status: str,
                 gate_id: Optional[str] = None, depth: int = 0,
                 expects_reply: bool = False, auto_forwarded: bool = False) -> Optional[int]:
        """Insert a note; None if either chat is gone or is a side chat.

        The check and the insert share one transaction, so a family delete on
        another connection cannot leave a row naming a deleted chat.
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            n = conn.execute(
                'SELECT COUNT(*) FROM conversations WHERE id IN (?, ?) AND side_of IS NULL',
                (from_conv, to_conv)).fetchone()[0]
            if from_conv == to_conv or n != 2:
                return None
            cur = conn.execute(
                'INSERT INTO mailbox (from_conv, to_conv, body, status, gate_id, depth, created_at, '
                'expects_reply, auto_forwarded) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (from_conv, to_conv, body, status, gate_id, depth, now,
                 1 if expects_reply else 0, 1 if auto_forwarded else 0))
            conn.commit()
            return cur.lastrowid

    def get_mail(self, mail_id: int) -> Optional[Dict[str, Any]]:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            row = conn.execute(f'SELECT {self._MAIL_COLS} FROM mailbox WHERE id = ?',
                               (mail_id,)).fetchone()
            return self._mail_row(row) if row else None

    def set_mail_status(self, mail_id: int, status: str, from_status: str) -> bool:
        """Move one row from `from_status` to `status`; False if it was not there."""
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            cur = conn.execute('UPDATE mailbox SET status = ? WHERE id = ? AND status = ?',
                               (status, mail_id, from_status))
            conn.commit()
            return cur.rowcount == 1

    def count_mail_since(self, from_conv: int, to_conv: int, since: str) -> int:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            return conn.execute(
                'SELECT COUNT(*) FROM mailbox WHERE from_conv = ? AND to_conv = ? AND created_at >= ?',
                (from_conv, to_conv, since)).fetchone()[0]

    def max_mail_id(self) -> int:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            return conn.execute('SELECT COALESCE(MAX(id), 0) FROM mailbox').fetchone()[0]

    def mail_sent_after(self, from_conv: int, to_conv: int, after_id: int) -> bool:
        """Did `from_conv` write to `to_conv` after row `after_id`, in ANY
        status? A rejected or still-pending send counts: the user already
        decided on it, so an automatic copy must not go around that card."""
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            return conn.execute(
                'SELECT 1 FROM mailbox WHERE id > ? AND from_conv = ? AND to_conv = ? LIMIT 1',
                (after_id, from_conv, to_conv)).fetchone() is not None

    def claim_queued_mail(self, to_conv: int,
                          note_of: Optional[Callable[[List[Dict[str, Any]]], str]] = None,
                          after_id: int = 0) -> List[Dict[str, Any]]:
        """Mark every queued note of `to_conv` delivered and return them, oldest
        first, with the sender's title; one transaction, so a note is handed
        out once. With `note_of`, the recipient's message is written in the
        same transaction: a failed write leaves the notes queued instead of
        delivered to nobody (Codex mailaudit, 27 Sep 2026). Only ids above
        `after_id` are claimed: while the startup sweep has not succeeded, the
        previous run's notes are held (Codex queueaudit, 28 Sep 2026)."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            rows = conn.execute(
                f'SELECT {", ".join("m." + c.strip() for c in self._MAIL_COLS.split(","))}, c.title '
                'FROM mailbox m LEFT JOIN conversations c ON c.id = m.from_conv '
                'WHERE m.to_conv = ? AND m.status = ? AND m.id > ? ORDER BY m.id ASC',
                (to_conv, "queued", after_id)).fetchall()
            out = []
            for r in rows:
                item = self._mail_row(r)
                item["from_title"] = r[self._MAIL_NCOLS] or ""
                item["status"] = "delivered"
                item["delivered_at"] = now
                out.append(item)
            if out:
                marks = ','.join('?' * len(out))
                conn.execute(
                    f'UPDATE mailbox SET status = ?, delivered_at = ? WHERE id IN ({marks})',
                    ["delivered", now] + [m["id"] for m in out])
                if note_of is not None:
                    conn.execute(
                        'INSERT INTO messages (conversation_id, role, content, smells_json, timestamp) '
                        'VALUES (?, ?, ?, ?, ?)',
                        (to_conv, "system", note_of(out), "[]", now))
                    self._touch(conn, to_conv, now)
            conn.commit()
            return out

    def queued_mail_targets(self, after_id: int = 0) -> List[int]:
        """Chats holding queued notes with an id above `after_id`."""
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            return [r[0] for r in conn.execute(
                'SELECT DISTINCT to_conv FROM mailbox WHERE status = ? AND id > ? ORDER BY to_conv',
                ("queued", after_id))]

    def reject_pending_mail(self, up_to_id: Optional[int] = None) -> int:
        """Startup: a card that waited in the previous process can no longer be
        answered, so its note is refused rather than left pending forever. With
        `up_to_id`, only ids at or below it are rejected: a retry after a
        failed startup rejection must leave a card this process itself raised
        since alone (Codex verify round, 28 Sep 2026)."""
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            cur = conn.execute(
                "UPDATE mailbox SET status = 'rejected' "
                "WHERE status = 'pending_approval' AND (? IS NULL OR id <= ?)",
                (up_to_id, up_to_id))
            conn.commit()
            return cur.rowcount

    def sweep_undelivered_mail(self, recipient_note: Callable[[List[Dict[str, Any]]], str],
                               sender_note: Callable[[List[Dict[str, Any]]], str],
                               up_to_id: Optional[int] = None) -> int:
        """Startup: a note still `queued` from the previous run cannot self-
        deliver (owner decision, 28 Sep 2026 - a restart used to re-arm its own
        wake and two old chats replied to each other until the depth limit
        stopped the chain). One transaction: every `queued` row becomes
        `undelivered`, one system message is written into each affected
        recipient chat and one into each affected sender chat (`_touch`ed like
        `claim_queued_mail` touches a delivery), so a failed write leaves the
        rows `queued` for the next attempt instead of half-swept. With
        `up_to_id`, only ids at or below it are swept: a retry after a failed
        startup sweep must leave the notes this process queued since alone
        (Codex queueaudit, 28 Sep 2026). Returns the number of notes moved.
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            rows = conn.execute(
                f'SELECT {", ".join("m." + c.strip() for c in self._MAIL_COLS.split(","))}, '
                'fc.title, tc.title '
                'FROM mailbox m LEFT JOIN conversations fc ON fc.id = m.from_conv '
                'LEFT JOIN conversations tc ON tc.id = m.to_conv '
                "WHERE m.status = 'queued' AND (? IS NULL OR m.id <= ?) ORDER BY m.id ASC",
                (up_to_id, up_to_id)).fetchall()
            if not rows:
                conn.commit()
                return 0
            items: List[Dict[str, Any]] = []
            by_recipient: Dict[int, List[Dict[str, Any]]] = {}
            by_sender: Dict[int, List[Dict[str, Any]]] = {}
            for r in rows:
                item = self._mail_row(r)
                item["from_title"] = r[self._MAIL_NCOLS] or ""
                item["to_title"] = r[self._MAIL_NCOLS + 1] or ""
                items.append(item)
                by_recipient.setdefault(item["to_conv"], []).append(item)
                by_sender.setdefault(item["from_conv"], []).append(item)
            marks = ','.join('?' * len(items))
            conn.execute(f"UPDATE mailbox SET status = 'undelivered' WHERE id IN ({marks})",
                        [it["id"] for it in items])
            for to_conv, notes in by_recipient.items():
                conn.execute(
                    'INSERT INTO messages (conversation_id, role, content, smells_json, timestamp) '
                    'VALUES (?, ?, ?, ?, ?)',
                    (to_conv, "system", recipient_note(notes), "[]", now))
                self._touch(conn, to_conv, now)
            for from_conv, notes in by_sender.items():
                conn.execute(
                    'INSERT INTO messages (conversation_id, role, content, smells_json, timestamp) '
                    'VALUES (?, ?, ?, ?, ?)',
                    (from_conv, "system", sender_note(notes), "[]", now))
                self._touch(conn, from_conv, now)
            conn.commit()
            return len(items)

    # ===================== YENİ: MESAJLAR =====================
    def add_message(self, conversation_id: int, role: str, content: str, smells: list = None,
                    provider: Optional[str] = None, model: Optional[str] = None) -> int:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        smells_json = json.dumps(smells) if smells else "[]"
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            cursor = conn.execute(
                'INSERT INTO messages (conversation_id, role, content, smells_json, timestamp, provider, model) '
                'VALUES (?, ?, ?, ?, ?, ?, ?)',
                (conversation_id, role, content, smells_json, now, provider or None, model or None)
            )
            # Sohbetin updated_at'ini güncelle
            self._touch(conn, conversation_id, now)
            conn.commit()
            return cursor.lastrowid

    def get_conversation_messages(self, conversation_id: int) -> List[Dict[str, Any]]:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            rows = conn.execute(
                'SELECT id, role, content, smells_json, timestamp, provider, model FROM messages '
                'WHERE conversation_id = ? ORDER BY id ASC',
                (conversation_id,)
            ).fetchall()
            return [
                {"id": r[0], "role": r[1], "content": r[2], "smells": json.loads(r[3]), "timestamp": r[4],
                 "provider": r[5], "model": r[6]}
                for r in rows
            ]

    # ===================== CLI OTURUM KİMLİKLERİ =====================
    def save_cli_session(self, conv_id: int, provider: str,
                         session_id: str, workspace: str = "") -> None:
        """Tur bittiğinde CLI'ın oturum kimliğini saklar (idempotent, üzerine yazar)."""
        if not conv_id or not provider or not session_id:
            return
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            with closing(sqlite3.connect(self.db_path)) as conn, conn:
                conn.execute(
                    'INSERT INTO cli_sessions (conversation_id, provider, session_id, workspace, updated_at) '
                    'VALUES (?, ?, ?, ?, ?) '
                    'ON CONFLICT(conversation_id, provider) DO UPDATE SET '
                    'session_id=excluded.session_id, workspace=excluded.workspace, updated_at=excluded.updated_at',
                    (conv_id, provider, session_id, workspace or "", now))
        except sqlite3.Error as e:
            # Oturum kimliğini saklayamamak bir kolaylık kaybı; sohbeti kırmamalı.
            # Kaydedilemezse sonraki tur transcript enjeksiyonuna düşer (eski davranış).
            logger.warning(f"[cli_sessions] kimlik saklanamadı: {e}")

    def clear_cli_session(self, conv_id: int) -> None:
        """Bir sohbetin TÜM CLI oturum kimliklerini düşürür (compact'in yarısı).

        ⚠️ Compact'ten sonra çağrılmak ZORUNDA. Kimlik kalırsa sonraki tur
        `resume=` ile açılır ve CLI kendi diskindeki TAM transcript'i geri
        yükler — yani kapattığımız oturum kapatılmamış gibi geri gelir ve
        compact hiçbir şey küçültmemiş olur (9 Ağu 2026'da canlı ölçüldü:
        compact sonrası bağlam 773k/1M'de sabit kaldı).

        Tüm sağlayıcılar birden siliniyor, çünkü compact hepsinin canlı
        session'ını kapatıyor; biri kalırsa o CLI'a geçildiğinde eski bağlam
        tek başına dirilir.
        """
        if not conv_id:
            return
        try:
            with closing(sqlite3.connect(self.db_path)) as conn, conn:
                conn.execute('DELETE FROM cli_sessions WHERE conversation_id = ?',
                             (conv_id,))
        except sqlite3.Error as e:
            # Fail-soft değil ama ölümcül de değil: silinemezse compact eksik
            # kalır, o yüzden sessiz geçilmiyor — uyarı seviyesinde loglanıyor.
            logger.warning(f"[cli_sessions] kimlik silinemedi (compact eksik kalır): {e}")

    def get_cli_session(self, conv_id: int, provider: str,
                        workspace: str = "") -> Optional[str]:
        """Saklı kimliği döndürür — YALNIZ workspace eşleşiyorsa.

        Eşleşme şartı bir güvenlik değil DOĞRULUK önlemi: CLI oturumları proje
        diziniyle anahtarlı, yani başka bir klasörde açılmış bir kimliği resume
        etmek kullanıcıya YANLIŞ projenin geçmişini gösterirdi.
        """
        if not conv_id or not provider:
            return None
        try:
            with closing(sqlite3.connect(self.db_path)) as conn, conn:
                row = conn.execute(
                    'SELECT session_id, workspace FROM cli_sessions '
                    'WHERE conversation_id = ? AND provider = ?',
                    (conv_id, provider)).fetchone()
        except sqlite3.Error as e:
            logger.warning(f"[cli_sessions] kimlik okunamadı: {e}")
            return None
        if not row:
            return None
        kayitli_ws = row[1] or ""
        if (workspace or "") != kayitli_ws:
            logger.info("[cli_sessions] workspace değişmiş → kimlik kullanılmıyor")
            return None
        return row[0] or None

    # ===================== HAFIZA (MEMORY) =====================
    def save_memory(self, conv_id: int, summary: str) -> None:
        """Sohbet özetini (compact) hafızaya kaydet."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute(
                'UPDATE conversations SET memory_summary = ? WHERE id = ?',
                (summary, conv_id)
            )
            self._touch(conn, conv_id, now)
            conn.commit()

    def get_memory(self, conv_id: int) -> str:
        """Sohbetin hafıza özetini getir."""
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            row = conn.execute(
                'SELECT memory_summary FROM conversations WHERE id = ?', (conv_id,)
            ).fetchone()
            return (row[0] or "") if row else ""

    def compact_conversation(self, conv_id: int, summary: str) -> None:
        """Sohbeti compact'la: özeti kaydet, eski mesajları sil, özet mesajını ekle."""
        self.save_memory(conv_id, summary)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute('DELETE FROM messages WHERE conversation_id = ?', (conv_id,))
            # Özeti tek bir "system" mesajı olarak ekle — kullanıcı UI'da görür
            conn.execute(
                'INSERT INTO messages (conversation_id, role, content, smells_json, timestamp) VALUES (?, ?, ?, ?, ?)',
                (conv_id, 'assistant', f'📝 **Sohbet özetlendi.**\n\n{summary}', '[]', now)
            )
            conn.commit()

    def record_activity(self, kind, conversation_id=None, provider=None, model=None,
                        tool=None, detail=None, at=None) -> bool:
        """A profile metric must never turn a saved answer into a save warning."""
        try:
            with closing(sqlite3.connect(self.db_path)) as conn, conn:
                conn.execute(
                    'INSERT INTO activity_events '
                    '(at, kind, conversation_id, provider, model, tool, detail) '
                    'VALUES (?, ?, ?, ?, ?, ?, ?)',
                    (at or datetime.now().strftime("%Y-%m-%d %H:%M:%S"), kind,
                     conversation_id, provider, model, tool, detail))
            return True
        except Exception:
            if not getattr(self, '_activity_failure_logged', False):
                self._activity_failure_logged = True
                logger.exception('[profile] activity row not written')
            return False

    def list_activity(self, kinds=None, since=None) -> List[Dict[str, Any]]:
        clauses, params = [], []
        if kinds is not None:
            if not kinds:
                return []
            clauses.append('kind IN (' + ', '.join('?' for _ in kinds) + ')')
            params.extend(kinds)
        if since is not None:
            clauses.append('at >= ?')
            params.append(since)
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(row) for row in conn.execute(
                'SELECT * FROM activity_events' + where + ' ORDER BY at, id', params)]

    def clear_activity(self) -> int:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            return conn.execute('DELETE FROM activity_events').rowcount

    # ===================== APP SETTINGS =====================
    def get_setting(self, key: str) -> Optional[str]:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            row = conn.execute('SELECT value FROM app_settings WHERE key = ?', (key,)).fetchone()
            return row[0] if row else None

    def set_setting(self, key: str, value: str) -> None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute(
                'INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?) '
                'ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at',
                (key, value, now),
            )

    # ===================== WORKSPACE =====================
    def _ensure_workspace_table(self):
        """Workspace tablosunu oluştur (mevcut DB'lerle geriye uyumlu)."""
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS workspaces (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                path TEXT NOT NULL,
                last_accessed TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users (id))''')
            conn.commit()

    def save_workspace(self, user_id: int, path: str) -> None:
        """Kullanıcının workspace yolunu kaydet/güncelle."""
        self._ensure_workspace_table()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            # Aynı kullanıcı + aynı path var mı?
            existing = conn.execute(
                'SELECT id FROM workspaces WHERE user_id = ? AND path = ?', (user_id, path)
            ).fetchone()
            if existing:
                conn.execute(
                    'UPDATE workspaces SET last_accessed = ? WHERE id = ?', (now, existing[0])
                )
            else:
                conn.execute(
                    'INSERT INTO workspaces (user_id, path, last_accessed) VALUES (?, ?, ?)',
                    (user_id, path, now)
                )
            conn.commit()

    def get_last_workspace(self, user_id: int) -> Optional[str]:
        """Kullanıcının en son açtığı workspace yolunu döndürür."""
        self._ensure_workspace_table()
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            row = conn.execute(
                'SELECT path FROM workspaces WHERE user_id = ? ORDER BY last_accessed DESC, id DESC LIMIT 1',
                (user_id,)
            ).fetchone()
            return row[0] if row else None

    def get_recent_workspaces(self, user_id: int, limit: int = 12) -> List[Dict[str, Any]]:
        self._ensure_workspace_table()
        limit = max(1, min(50, limit))
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            rows = conn.execute(
                'SELECT w.path, w.last_accessed, '
                '(SELECT COUNT(*) FROM conversations c WHERE c.user_id = w.user_id '
                'AND c.workspace = w.path AND c.parent_id IS NULL AND c.side_of IS NULL AND c.hidden = 0) '
                'FROM workspaces w WHERE w.user_id = ? ORDER BY w.last_accessed DESC, w.id DESC LIMIT ?',
                (user_id, limit)
            ).fetchall()
            return [{"path": row[0], "last_accessed": row[1][:19], "chat_count": row[2]} for row in rows]

    def remove_workspace(self, user_id: int, path: str) -> bool:
        self._ensure_workspace_table()
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            cursor = conn.execute('DELETE FROM workspaces WHERE user_id = ? AND path = ?', (user_id, path))
            conn.commit()
            return cursor.rowcount > 0
