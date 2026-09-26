from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class AuthRequest(BaseModel):
    username: str
    password: str


class AnalysisRequest(BaseModel):
    code: str
    language: str = "tr"
    user_id: int


class AIConfigRequest(BaseModel):
    user_id: int
    provider_type: str
    model_name: str
    api_key: str


class UpdateFileRequest(BaseModel):
    file_path: str
    new_code: str


class RenameRequest(BaseModel):
    title: str


class HiddenRequest(BaseModel):
    hidden: bool


class APIKeySaveRequest(BaseModel):
    provider_type: str
    api_key: str


class NewConversationRequest(BaseModel):
    user_id: int
    title: str = "Yeni Sohbet"


class ChatRequest(BaseModel):
    # Sınırlar yerel bir uç için de gerekli: gövde doğrudan süreç belleğine, oradan
    # prompt'a giriyor; sınırsız girdi hem RAM'i hem sağlayıcı isteğini şişiriyor.
    # Sayıların gerekçesi tek tek aşağıda; hepsi "normal kullanımın çok üstünde ama
    # süreç için hâlâ zararsız" bandında seçildi.
    conversation_id: int
    # 200k karakter: conversation_routes'taki bağlam tavanıyla (max_context_chars)
    # aynı — tek bir mesaj bütün sohbetin bağlamından büyük olamaz.
    message: str = Field(max_length=200_000)
    language: str = "tr"
    user_id: int
    mode: str = "analysis"
    # 400k karakter: editörde açık dosyanın TAMAMI geliyor; 10 bin satırlık bir Unity
    # C# scripti (~40 karakter/satır) bile bu sınırın altında kalır.
    editor_code: str = Field(default="", max_length=400_000)
    use_thinking: bool = False
    thinking_level: str = "medium" # off | low | medium | high
    generation_confirmed: bool = False
    generation_mode: str = "auto"  # auto | plan | step
    # 20 görsel: her eleman base64 data-URI, yani tanesi MB'larca olabiliyor. Tek
    # turda 20'den fazla ekran görüntüsü gönderen gerçek bir akış yok.
    images: Optional[List[str]] = Field(default=None, max_length=20)
    # Video ekleri: [{"kind":"path","path":...} | {"kind":"url","url":...}]. Backend
    # bunları kare data-URI'leri + transkripte çevirip images'a katar (video_extract).
    # 5 video: her biri kare çıkarma + transkripsiyon tetikliyor, pahalı taraf orası.
    videos: Optional[List[dict]] = Field(default=None, max_length=5)
    # Claude-only (subscription + model_name "claude-" ile başlar). Diğer sağlayıcılarda
    # yok sayılır. effort_level → ClaudeAgentOptions.effort; ultracode → mesaj keyword'ü.
    effort_level: str = "medium"  # low | medium | high | max
    ultracode: bool = False
    # AUTO-WAKE: who started the turn. "user" is real human input; "wake" is a
    # continuation turn the client starts BY ITSELF once a background job
    # finishes. The distinction matters for visibility: a wake message is
    # written to the chat with the `system` role (so a sentence the user never
    # said isn't attributed to them), and the consecutive-wake counter only
    # resets on a real user message.
    origin: Literal["user", "wake"] = "user"


class WorkspaceRequest(BaseModel):
    user_id: int
    path: str


class WriteFileRequest(BaseModel):
    file_path: str
    content: str
    workspace_path: str
