import sqlite3
import json
import logging
import os
from contextlib import closing
from datetime import datetime, timedelta
import bcrypt
from typing import Callable, List, Dict, Any, Optional, Tuple
from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)


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
            for col_def in ("parent_id INTEGER", "fork_at INTEGER",
                            "hidden INTEGER NOT NULL DEFAULT 0", "side_of INTEGER"):
                try:
                    cursor.execute(f"ALTER TABLE conversations ADD COLUMN {col_def}")
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
    def create_conversation(self, user_id: int, title: str = "Yeni Sohbet") -> int:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            cursor = conn.execute(
                'INSERT INTO conversations (user_id, title, created_at, updated_at) VALUES (?, ?, ?, ?)',
                (user_id, title, now, now)
            )
            conn.commit()
            return cursor.lastrowid

    def get_user_conversations(self, user_id: int) -> List[Dict[str, Any]]:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            rows = conn.execute(
                'SELECT id, title, created_at, updated_at, parent_id, hidden FROM conversations '
                'WHERE user_id = ? AND side_of IS NULL ORDER BY updated_at DESC',
                (user_id,)
            ).fetchall()
            return [{"id": r[0], "title": r[1], "created_at": r[2], "updated_at": r[3],
                     "parent_id": r[4], "hidden": bool(r[5])} for r in rows]

    def get_conversation_owner(self, conv_id: int) -> Optional[int]:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            row = conn.execute('SELECT user_id FROM conversations WHERE id = ?', (conv_id,)).fetchone()
            return row[0] if row else None

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
                'SELECT user_id, title, memory_summary, parent_id, side_of FROM conversations WHERE id = ?',
                (source_id,)
            ).fetchone()
            if not src or src[4] is not None:
                return None
            user_id, title, memory_summary, parent_id, _ = src
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
                'parent_id, fork_at, hidden) VALUES (?, ?, ?, ?, ?, ?, ?, 0)',
                (user_id, new_title, now, now, memory_summary or "", root_id, fork_at)
            )
            new_id = cur.lastrowid
            if fork_at is not None:
                conn.execute(
                    'INSERT INTO messages (conversation_id, role, content, smells_json, timestamp) '
                    'SELECT ?, role, content, smells_json, timestamp FROM messages '
                    'WHERE conversation_id = ? AND id <= ? ORDER BY id',
                    (new_id, source_id, fork_at)
                )
            conn.execute('UPDATE conversations SET updated_at = ? WHERE id = ?', (now, root_id))
            conn.commit()
        return {"id": new_id, "title": new_title, "parent_id": root_id, "hidden": False,
                "created_at": now, "updated_at": now}

    def set_conversation_hidden(self, conv_id: int, hidden: bool) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute('UPDATE conversations SET hidden = ? WHERE id = ?', (1 if hidden else 0, conv_id))
            conn.commit()

    def rename_conversation(self, conv_id: int, new_title: str) -> None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute('UPDATE conversations SET title = ? WHERE id = ?', (new_title, conv_id))
            self._touch(conn, conv_id, now)
            conn.commit()

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
                'SELECT user_id, side_of FROM conversations WHERE id = ?', (main_id,)
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
                'parent_id, hidden, side_of) VALUES (?, ?, ?, ?, NULL, 1, ?)',
                (user_id, "Yan soru", now, now, main_id)
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

    # ===================== CHAT MAILBOX =====================
    _MAIL_COLS = 'id, from_conv, to_conv, body, status, gate_id, depth, created_at, delivered_at'

    @staticmethod
    def _mail_row(r) -> Dict[str, Any]:
        return {"id": r[0], "from_conv": r[1], "to_conv": r[2], "body": r[3], "status": r[4],
                "gate_id": r[5], "depth": r[6], "created_at": r[7], "delivered_at": r[8]}

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
                 gate_id: Optional[str] = None, depth: int = 0) -> Optional[int]:
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
                'INSERT INTO mailbox (from_conv, to_conv, body, status, gate_id, depth, created_at) '
                'VALUES (?, ?, ?, ?, ?, ?, ?)',
                (from_conv, to_conv, body, status, gate_id, depth, now))
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

    def claim_queued_mail(self, to_conv: int,
                          note_of: Optional[Callable[[List[Dict[str, Any]]], str]] = None
                          ) -> List[Dict[str, Any]]:
        """Mark every queued note of `to_conv` delivered and return them, oldest
        first, with the sender's title; one transaction, so a note is handed
        out once. With `note_of`, the recipient's message is written in the
        same transaction: a failed write leaves the notes queued instead of
        delivered to nobody (Codex mailaudit, 27 Sep 2026)."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            rows = conn.execute(
                f'SELECT {", ".join("m." + c.strip() for c in self._MAIL_COLS.split(","))}, c.title '
                'FROM mailbox m LEFT JOIN conversations c ON c.id = m.from_conv '
                'WHERE m.to_conv = ? AND m.status = ? ORDER BY m.id ASC',
                (to_conv, "queued")).fetchall()
            out = []
            for r in rows:
                item = self._mail_row(r)
                item["from_title"] = r[9] or ""
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

    def queued_mail_targets(self) -> List[int]:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            return [r[0] for r in conn.execute(
                'SELECT DISTINCT to_conv FROM mailbox WHERE status = ? ORDER BY to_conv',
                ("queued",))]

    def reject_pending_mail(self) -> int:
        """Startup: a card that waited in the previous process can no longer be
        answered, so its note is refused rather than left pending forever."""
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            cur = conn.execute('UPDATE mailbox SET status = ? WHERE status = ?',
                               ("rejected", "pending_approval"))
            conn.commit()
            return cur.rowcount

    # ===================== YENİ: MESAJLAR =====================
    def add_message(self, conversation_id: int, role: str, content: str, smells: list = None) -> int:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        smells_json = json.dumps(smells) if smells else "[]"
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            cursor = conn.execute(
                'INSERT INTO messages (conversation_id, role, content, smells_json, timestamp) VALUES (?, ?, ?, ?, ?)',
                (conversation_id, role, content, smells_json, now)
            )
            # Sohbetin updated_at'ini güncelle
            self._touch(conn, conversation_id, now)
            conn.commit()
            return cursor.lastrowid

    def get_conversation_messages(self, conversation_id: int) -> List[Dict[str, Any]]:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            rows = conn.execute(
                'SELECT id, role, content, smells_json, timestamp FROM messages WHERE conversation_id = ? ORDER BY id ASC',
                (conversation_id,)
            ).fetchall()
            return [
                {"id": r[0], "role": r[1], "content": r[2], "smells": json.loads(r[3]), "timestamp": r[4]}
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
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
