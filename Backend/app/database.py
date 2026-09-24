import sqlite3
import json
import logging
import os
from contextlib import closing
from datetime import datetime
import bcrypt
from typing import List, Dict, Any, Optional, Tuple
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
                'SELECT id, title, created_at, updated_at FROM conversations WHERE user_id = ? ORDER BY updated_at DESC',
                (user_id,)
            ).fetchall()
            return [{"id": r[0], "title": r[1], "created_at": r[2], "updated_at": r[3]} for r in rows]

    def get_conversation_owner(self, conv_id: int) -> Optional[int]:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            row = conn.execute('SELECT user_id FROM conversations WHERE id = ?', (conv_id,)).fetchone()
            return row[0] if row else None

    def rename_conversation(self, conv_id: int, new_title: str) -> None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute('UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?', (new_title, now, conv_id))
            conn.commit()

    def delete_conversation(self, conv_id: int) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute('PRAGMA foreign_keys = ON')
            conn.execute('DELETE FROM messages WHERE conversation_id = ?', (conv_id,))
            # Kimlik de gitsin: sohbet yokken `cli_sessions` satırı yetim kalıyordu.
            # `conversations.id` AUTOINCREMENT olduğu için id yeniden kullanılmıyor,
            # yani yanlış geçmiş gösterme riski YOK — bu yalnız çöp temizliği.
            conn.execute('DELETE FROM cli_sessions WHERE conversation_id = ?', (conv_id,))
            conn.execute('DELETE FROM conversations WHERE id = ?', (conv_id,))
            conn.commit()

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
            conn.execute('UPDATE conversations SET updated_at = ? WHERE id = ?', (now, conversation_id))
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
                'UPDATE conversations SET memory_summary = ?, updated_at = ? WHERE id = ?',
                (summary, now, conv_id)
            )
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
