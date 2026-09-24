import re
import time
import ollama
import openai
from google import genai
from google.genai import types as gtypes
import anthropic
from typing import Optional, List
from .base import AIProvider, ThinkingResult

import logging
logger = logging.getLogger(__name__)

DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"


class GeminiProvider(AIProvider):
    def __init__(self, api_key: str, model_name: str = "gemini-2.5-flash"):
        self.client = genai.Client(api_key=api_key)

        raw_name = model_name.lower() if model_name else ""
        # Google AI Studio / google-genai SDK - Temmuz 2026 (Minimum desteklenen: v2.0)
        if "3.8-flash" in raw_name:
            self.model_id = "gemini-3.8-flash"
        elif "3.7-flash" in raw_name:
            self.model_id = "gemini-3.7-flash"
        elif "3.6-flash" in raw_name:
            self.model_id = "gemini-3.6-flash"
        elif "3.5-flash-lite" in raw_name:
            self.model_id = "gemini-3.5-flash-lite"
        elif "3.5-flash" in raw_name:
            self.model_id = "gemini-3.5-flash"
        elif "3.1-pro" in raw_name:
            self.model_id = "gemini-3.1-pro"
        elif "3.1-flash-lite" in raw_name:
            self.model_id = "gemini-3.1-flash-lite"
        elif "3-flash" in raw_name or "3.0-flash" in raw_name:
            self.model_id = "gemini-3-flash"
        elif "2.5-pro" in raw_name:
            self.model_id = "gemini-2.5-pro"
        elif "2.0-flash" in raw_name:
            self.model_id = "gemini-2.0-flash"
        elif "pro" in raw_name:
            self.model_id = "gemini-3.1-pro"
        else:
            self.model_id = model_name if model_name else "gemini-3.6-flash"

    # gemini-3.x (3.8/3.7/3.6/3.5/3.1/3.0) and 2.5 support thinking
    _THINKING_MODELS = ("gemini-3", "gemini-2.5")
    # Numeric thinking_budget was REMOVED for this family; only the
    # thinking_level enum (low|medium|high) is accepted, a budget returns 400.
    _THINKING_LEVEL_ONLY = ("gemini-3.7", "gemini-3.8")

    def _supports_thinking(self) -> bool:
        return any(self.model_id.startswith(m) for m in self._THINKING_MODELS)

    def _thinking_level_only(self) -> bool:
        return any(self.model_id.startswith(m) for m in self._THINKING_LEVEL_ONLY)

    def analyze_code(self, prompt: str, max_tokens: int = 4096, images: Optional[List[str]] = None) -> str:
        try:
            contents = [prompt]
            if images:
                parts = [gtypes.Part(text=prompt)]
                for img_data in images:
                    if "," in img_data:
                        header, base64_str = img_data.split(",", 1)
                        mime_type = header.split(":")[1].split(";")[0]
                        parts.append(gtypes.Part(inline_data=gtypes.Blob(mime_type=mime_type, data=base64_str)))
                contents = [gtypes.Content(role="user", parts=parts)]

            response = self.client.models.generate_content(
                model=self.model_id,
                contents=contents,
                config=gtypes.GenerateContentConfig(max_output_tokens=max_tokens),
            )
            if response and response.text:
                return response.text
            return "AI yanıt üretti ancak içerik boş döndü."
        except Exception as e:
            err_str = str(e)
            if "429" in err_str:
                return "SİSTEM MESAJI: Google API ücretsiz kota sınırına ulaşıldı. Lütfen 60 saniye bekleyip tekrar deneyin veya Ollama (Yerel) moduna geçin."
            if "404" in err_str:
                return f"SİSTEM MESAJI: '{self.model_id}' modeli bulunamadı. Ayarlar'dan farklı bir Gemini modeli seçin."
            raise Exception(f"Gemini API Hatası: {err_str}")

    def analyze_code_with_thinking(self, prompt: str, max_tokens: int = 4096, images: Optional[List[str]] = None, thinking_level: str = "medium") -> ThinkingResult:
        if not self._supports_thinking():
            return self.analyze_code(prompt, max_tokens, images), None, None

        try:
            start = time.time()
            contents = [prompt]
            if images:
                parts = [gtypes.Part(text=prompt)]
                for img_data in images:
                    if "," in img_data:
                        header, base64_str = img_data.split(",", 1)
                        mime_type = header.split(":")[1].split(";")[0]
                        parts.append(gtypes.Part(inline_data=gtypes.Blob(mime_type=mime_type, data=base64_str)))
                contents = [gtypes.Content(role="user", parts=parts)]

            if self._thinking_level_only():
                lvl = thinking_level if thinking_level in ("low", "medium", "high") else "medium"
                thinking_config = gtypes.ThinkingConfig(thinking_level=lvl)
            else:
                # Düşünme seviyesine göre bütçe belirle
                budget_map = {"low": 4096, "medium": 16384, "high": 65536}
                thinking_config = gtypes.ThinkingConfig(
                    thinking_budget=budget_map.get(thinking_level, 16384))

            response = self.client.models.generate_content(
                model=self.model_id,
                contents=contents,
                config=gtypes.GenerateContentConfig(
                    max_output_tokens=max_tokens,
                    thinking_config=thinking_config,
                ),
            )
            duration_ms = int((time.time() - start) * 1000)

            thinking_text = None
            response_text = ""
            for part in response.candidates[0].content.parts:
                if getattr(part, "thought", False):
                    thinking_text = (thinking_text or "") + (part.text or "")
                else:
                    response_text += part.text or ""

            return response_text.strip() or "", thinking_text, duration_ms
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"[Gemini thinking] Fallback: {e}")
            return self.analyze_code(prompt, max_tokens), None, None


class OllamaProvider(AIProvider):
    def __init__(self, model_name: str = "qwen2.5-coder:7b"):
        self.model_name = model_name if model_name else "qwen2.5-coder:7b"

    def analyze_code(self, prompt: str, max_tokens: int = 4096,
                     images: Optional[List[str]] = None,
                     thinking_level: str = "medium", cwd: Optional[str] = None) -> str:
        # images/thinking_level/cwd: AIProvider arayüzüyle imza uyumu için kabul edilir
        # ama Ollama chat akışında kullanılmaz (agentic loop'tan gelen TypeError fix'i).
        try:
            response = ollama.chat(
                model=self.model_name,
                messages=[{'role': 'user', 'content': prompt}],
                options={'num_predict': max_tokens}
            )
            return self._clean_response(response['message']['content'])
        except Exception as e:
            raise Exception(f"Ollama Hatası: {str(e)}")


class OpenAICompatibleProvider(AIProvider):
    # OpenAI reasoning modelleri (Mayıs 2026 güncel)
    # gpt-6: sending temperature/top_p/logprobs makes the request FAIL.
    _REASONING_MODELS = ("gpt-6", "gpt-5.6", "gpt-5.5", "gpt-5.4", "o3", "o4", "o5")
    # Kimi thinking modelleri
    _KIMI_THINKING_MODELS = ("kimi-k3", "kimi-k2")
    # Kimi model name normalization
    _KIMI_ALIASES = {"kimi-k2.6": "kimi-k2.6", "kimi-k2.5": "kimi-k2.5"}

    def __init__(self, api_key: str, base_url: str, model_name: str):
        self.client = openai.OpenAI(api_key=api_key, base_url=base_url)
        self.model_name = model_name
        self.base_url = base_url

    def _is_openai_reasoning(self) -> bool:
        lower_name = self.model_name.lower()
        return any(m in lower_name for m in self._REASONING_MODELS)

    def _is_kimi(self) -> bool:
        lower_name = self.model_name.lower()
        # "kimi-k" tüm nesilleri (k2, k2.6, k2.7-code, k3, gelecekteki k4…) kapsar
        return "moonshot" in self.base_url or "moonshotai" in lower_name or "kimi-k" in lower_name

    def _is_kimi_k3(self) -> bool:
        # K3'te düşünme HER ZAMAN açık — thinking.enabled göndermek 400 döndürür.
        return "kimi-k3" in self.model_name.lower()

    def analyze_code(self, prompt: str, max_tokens: int = 4096, images: Optional[List[str]] = None) -> str:
        try:
            user_content = [{"type": "text", "text": prompt}]
            if images:
                for img_data in images:
                    user_content.append({
                        "type": "image_url",
                        "image_url": {"url": img_data}
                    })

            kwargs = dict(
                model=self.model_name,
                messages=[{"role": "user", "content": user_content}],
                max_tokens=max_tokens,
            )
            # Reasoning ve Kimi modelleri temperature parametresini desteklemiyor
            if not self._is_openai_reasoning() and not self._is_kimi():
                kwargs["temperature"] = 0.3
            response = self.client.chat.completions.create(**kwargs)
            return self._clean_response(response.choices[0].message.content)
        except Exception as e:
            raise Exception(f"API Hatası: {str(e)}")

    def analyze_code_with_thinking(self, prompt: str, max_tokens: int = 4096) -> ThinkingResult:
        # OpenAI GPT-5.x reasoning
        if self._is_openai_reasoning():
            try:
                start = time.time()
                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=max_tokens,
                    reasoning={"effort": "medium"},
                )
                duration_ms = int((time.time() - start) * 1000)
                thinking = getattr(response.choices[0].message, "reasoning_content", None)
                text = self._clean_response(response.choices[0].message.content)
                return text, thinking or None, duration_ms
            except Exception:
                # reasoning parametresi desteklenmiyorsa (örn. bazı OpenRouter proxy'leri) standart call
                try:
                    start = time.time()
                    response = self.client.chat.completions.create(
                        model=self.model_name,
                        messages=[{"role": "user", "content": prompt}],
                        max_tokens=max_tokens,
                    )
                    duration_ms = int((time.time() - start) * 1000)
                    text = self._clean_response(response.choices[0].message.content)
                    return text, None, duration_ms
                except Exception:
                    return self.analyze_code(prompt, max_tokens), None, None

        # Kimi K2.x / K3 thinking
        if self._is_kimi():
            try:
                start = time.time()
                kwargs = dict(
                    model=self.model_name,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=max_tokens,
                )
                # K3: thinking always-on → enable flag YOK (400 verir). K2.x: extra_body ile aç.
                if not self._is_kimi_k3():
                    kwargs["extra_body"] = {"thinking": {"enabled": True}}
                response = self.client.chat.completions.create(**kwargs)
                duration_ms = int((time.time() - start) * 1000)
                thinking = getattr(response.choices[0].message, "reasoning_content", None)
                text = self._clean_response(response.choices[0].message.content)
                return text, thinking or None, duration_ms
            except Exception:
                # thinking desteklenmiyorsa (K2.6 gibi yeni versiyonlar) standart call
                try:
                    start = time.time()
                    response = self.client.chat.completions.create(
                        model=self.model_name,
                        messages=[{"role": "user", "content": prompt}],
                        max_tokens=max_tokens,
                    )
                    duration_ms = int((time.time() - start) * 1000)
                    text = self._clean_response(response.choices[0].message.content)
                    return text, None, duration_ms
                except Exception:
                    return self.analyze_code(prompt, max_tokens), None, None

        return self.analyze_code(prompt, max_tokens), None, None


class AnthropicProvider(AIProvider):
    def __init__(self, api_key: str, model_name: str = "claude-sonnet-4-6"):
        self.client = anthropic.Anthropic(api_key=api_key)

        raw_name = model_name.lower() if model_name else ""
        if "sonnet-5" in raw_name:
            self.model_name = "claude-sonnet-5"
        elif "sonnet-4-6" in raw_name or "sonnet" in raw_name:
            self.model_name = "claude-4-6-sonnet"
        elif "fable-5-1" in raw_name:
            self.model_name = "claude-fable-5-1"
        elif "fable" in raw_name:
            self.model_name = "claude-fable-5"
        elif "opus-5-5" in raw_name:
            self.model_name = "claude-opus-5-5"
        elif "opus-5" in raw_name:
            self.model_name = "claude-opus-5"
        elif "opus" in raw_name:
            self.model_name = "claude-opus-4-8"
        elif "haiku-4-5" in raw_name or "haiku" in raw_name:
            self.model_name = "claude-4-5-haiku"
        else:
            self.model_name = "claude-4-6-sonnet"

    def analyze_code(self, prompt: str, max_tokens: int = 4096, images: Optional[List[str]] = None) -> str:
        try:
            if max_tokens > 16384:
                return self._stream_response(prompt, max_tokens)  # Stream mode doesn't support images yet in our simple implementation

            user_content = [{"type": "text", "text": prompt}]
            if images:
                for img_data in images:
                    if "," in img_data:
                        header, base64_str = img_data.split(",", 1)
                        media_type = header.split(":")[1].split(";")[0]
                        user_content.append({
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": base64_str
                            }
                        })

            response = self.client.messages.create(
                model=self.model_name,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": user_content}]
            )
            text = ""
            for block in response.content:
                if block.type == "text":
                    text += block.text
            return self._clean_response(text)
        except Exception as e:
            return f"❌ Anthropic API Hatası: Sistemsel bir ret veya model hatası oluştu. Mesaj: {str(e)}"

    def analyze_code_with_thinking(self, prompt: str, max_tokens: int = 4096, images: Optional[List[str]] = None) -> ThinkingResult:
        try:
            start = time.time()
            user_content = [{"type": "text", "text": prompt}]
            if images:
                for img_data in images:
                    if "," in img_data:
                        header, base64_str = img_data.split(",", 1)
                        media_type = header.split(":")[1].split(";")[0]
                        user_content.append({
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": base64_str
                            }
                        })

            response = self.client.messages.create(
                model=self.model_name,
                max_tokens=max_tokens + 8000,
                thinking={"type": "enabled", "budget_tokens": 8000},
                messages=[{"role": "user", "content": user_content}]
            )
            duration_ms = int((time.time() - start) * 1000)

            thinking_text = None
            response_text = ""
            for block in response.content:
                if block.type == "thinking":
                    thinking_text = block.thinking
                elif block.type == "text":
                    response_text += block.text

            return self._clean_response(response_text), thinking_text, duration_ms
        except Exception:
            return self.analyze_code(prompt, max_tokens), None, None

    def _stream_response(self, prompt: str, max_tokens: int) -> str:
        try:
            text = ""
            with self.client.messages.stream(
                model=self.model_name,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}]
            ) as stream:
                for chunk in stream.text_stream:
                    text += chunk
            return self._clean_response(text)
        except Exception as e:
            return f"❌ Anthropic API Hatası: {str(e)}"
