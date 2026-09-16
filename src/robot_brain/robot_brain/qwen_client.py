"""Ollama client for the Qwen2.5 task planner."""

import ollama


class QwenClient:
    """Wraps Ollama chat completions for the Qwen2.5 planning model.

    Args:
        base_url: Ollama server URL.
        model: Model name, e.g. "qwen2.5:7b".
        temperature: Sampling temperature. 0.0 (default) makes planning as
            repeatable as the server allows, which the A/B benchmark (eval/)
            relies on. Not exactly: repeated runs of the same goal were measured
            to differ now and then (rag-analysis.md, threats to validity).
        seed: RNG seed passed to Ollama, for the same reason.
    """

    def __init__(
        self, base_url: str, model: str, temperature: float = 0.0, seed: int = 42,
    ) -> None:
        self._client = ollama.Client(host=base_url)
        self._model = model
        self._options = {'temperature': temperature, 'seed': seed}

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        """Requests a single-turn completion and returns the raw text.

        Args:
            system_prompt: System prompt describing the assistant's role.
            user_prompt: User content for this turn.

        Returns:
            Raw text response from the model.
        """
        response = self._client.chat(
            model=self._model,
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
            options=self._options,
            stream=False,
        )
        return response['message']['content']

    def generate_plan(self, system_prompt: str, user_prompt: str) -> str:
        """Requests a plan completion (expected to be JSON). Alias of chat()."""
        return self.chat(system_prompt, user_prompt)
