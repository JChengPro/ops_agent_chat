import json
import logging
from typing import Any

from openai import OpenAI

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class DeepSeekClient:
    def __init__(self) -> None:
        settings = get_settings()
        self.settings = settings
        self.enabled = bool(settings.deepseek_api_key)
        self.client = OpenAI(api_key=settings.deepseek_api_key, base_url=settings.deepseek_base_url) if self.enabled else None

    def json_completion(self, system: str, user: str, fallback: dict[str, Any]) -> dict[str, Any]:
        if not self.client:
            logger.warning("DeepSeek API key is not configured; using JSON fallback")
            return fallback
        try:
            response = self.client.chat.completions.create(
                model=self.settings.llm_model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=0.1,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content or "{}"
            return json.loads(content)
        except Exception as exc:
            logger.exception("DeepSeek JSON completion failed: %s", exc)
            return fallback

    def text_completion(self, system: str, user: str, fallback: str) -> str:
        if not self.client:
            logger.warning("DeepSeek API key is not configured; using text fallback")
            return fallback
        try:
            response = self.client.chat.completions.create(
                model=self.settings.llm_model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=0.2,
            )
            return response.choices[0].message.content or fallback
        except Exception as exc:
            logger.exception("DeepSeek text completion failed: %s", exc)
            return fallback
