import os
import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

class MemoryManager:
    """
    AI'nın sohbet bazlı uzun süreli hafızasını yönetir.
    Hafıza dosyaları JSON veya MD olarak saklanır ve chat silindiğinde temizlenir.
    """
    
    def __init__(self, base_dir: str):
        self.base_dir = Path(base_dir) / "memories"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        
    def _get_path(self, chat_id: str) -> Path:
        return self.base_dir / f"memory_{chat_id}.md"
        
    def save_memory(self, chat_id: str, content: str, *, strict: bool = False):
        """Hafızayı chat bazlı kaydeder.

        strict=True re-raises the write error; a caller that must not report
        success on a missing copy (branching) needs to see it.
        """
        path = self._get_path(chat_id)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            logger.info(f"[Memory] Hafıza kaydedildi: {chat_id}")
        except Exception as e:
            logger.error(f"[Memory] Kayıt hatası: {e}")
            if strict:
                raise

    def load_memory(self, chat_id: str) -> Optional[str]:
        """Hafızayı yükler."""
        path = self._get_path(chat_id)
        if path.exists():
            return path.read_text(encoding="utf-8")
        return None

    def delete_memory(self, chat_id: str):
        """Sohbet silindiğinde hafızayı da siler."""
        path = self._get_path(chat_id)
        if path.exists():
            path.unlink()
            logger.info(f"[Memory] Hafıza silindi: {chat_id}")

    def export_memory(self, chat_id: str, export_path: str):
        """Hafızayı dışarı aktarır (Kullanıcı projeye saklayabilir)."""
        content = self.load_memory(chat_id)
        if content:
            with open(export_path, "w", encoding="utf-8") as f:
                f.write(content)
            return True
        return False

    def import_memory(self, source_path: str, chat_id: str):
        """Dışarıdan bir hafıza dosyasını mevcut sohbete yükler."""
        if os.path.exists(source_path):
            with open(source_path, "r", encoding="utf-8") as f:
                content = f.read()
            self.save_memory(chat_id, content)
            return True
        return False

def _resolve_app_data_dir() -> str:
    """
    app_data dizinini yazılabilir bir kullanıcı klasörüne çözer.
    os.getcwd() paketlenmiş app'te install dizinini (ör. C:\\Program Files\\...)
    gösterir ve oraya yazmak PermissionError verir. DB ile aynı kökü kullanırız.
    """
    override = os.environ.get("APP_DATA_DIR")
    if override:
        return override
    return os.path.join(str(Path.home()), ".unity_architect_ai", "app_data")


# Singleton instance
memory_manager = MemoryManager(_resolve_app_data_dir())
