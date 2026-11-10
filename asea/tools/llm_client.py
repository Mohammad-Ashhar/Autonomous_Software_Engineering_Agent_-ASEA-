import time
from itertools import count

from openai import OpenAI

from asea.core.config import get_settings

_REQUEST_COUNTER = count(1)


class OllamaClient:

    def __init__(self):
        self.settings = get_settings()

        self.client = OpenAI(
            api_key="ollama",
            base_url=self.settings.ollama_base_url,
            timeout=60.0,
            max_retries=0,
        )

    def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.1,
        max_tokens: int = 512,
    ) -> str:

        request_id = next(_REQUEST_COUNTER)
        start_time = time.perf_counter()

        try:
            response = self.client.chat.completions.create(
                model=self.settings.ollama_model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )

            content = response.choices[0].message.content

            if not content:
                raise RuntimeError("Ollama returned an empty response.")

            return content.strip()

        finally:
            elapsed = time.perf_counter() - start_time

            print(
                f"[OLLAMA] Request #{request_id} "
                f"finished in {elapsed:.2f} seconds"
            )