from harbor.llms.lite_llm import LiteLLM
from harbor.models.task.config import UserSimulationConfig


class UserSimulator:
    """LLM-based simulated user for interactive multi-turn tasks."""

    def __init__(
        self,
        *,
        config: UserSimulationConfig,
        system_prompt: str,
        original_instruction: str,
    ) -> None:
        self._config = config
        self._system_prompt = system_prompt.strip()
        self._original_instruction = original_instruction.strip()
        self._history: list[dict[str, str]] = []
        self._llm = LiteLLM(
            model_name=config.model_name,
            temperature=config.temperature,
            api_base=config.api_base,
        )

    def receive_agent_message(self, content: str) -> None:
        self._history.append({"role": "assistant", "content": content})

    def should_stop(self, user_message: str) -> bool:
        message = user_message.strip()
        stop_phrases = [p.strip() for p in self._config.stop_phrases if p.strip()]
        return any(phrase in message for phrase in stop_phrases)

    def history(self) -> list[dict[str, str]]:
        return [dict(item) for item in self._history]

    async def next_user_message(self) -> str:
        prompt = self._build_next_turn_prompt()
        response = await self._llm.call(
            prompt=prompt,
            message_history=[{"role": "system", "content": self._system_prompt}],
            max_tokens=self._config.max_tokens,
            api_key=self._config.api_key,
        )
        message = response.content.strip()
        if not message:
            stop_phrases = [p.strip() for p in self._config.stop_phrases if p.strip()]
            message = stop_phrases[0] if stop_phrases else "#### STOP"
        self._history.append({"role": "user", "content": message})
        return message

    def _build_next_turn_prompt(self) -> str:
        transcript = self._format_history()
        stop_phrases = ", ".join(self._config.stop_phrases) or "#### STOP"
        return (
            "You are simulating the USER side of a conversation with an agent.\n\n"
            f"Original task:\n{self._original_instruction}\n\n"
            f"Conversation so far:\n{transcript}\n\n"
            "Generate exactly the NEXT user message only.\n"
            "Do not produce explanations, metadata, or role prefixes.\n"
            f"If the task is complete, emit one stop phrase exactly: {stop_phrases}."
        )

    def _format_history(self) -> str:
        if not self._history:
            return "<empty>"

        history = self._history[-self._config.max_history :]
        lines: list[str] = []
        for msg in history:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if len(content) > 8000:
                content = content[:4000] + "\n...[truncated]...\n" + content[-4000:]
            if role == "user":
                lines.append(f"User: {content}")
            elif role == "assistant":
                lines.append(f"Agent: {content}")
            else:
                lines.append(f"{role}: {content}")
        return "\n".join(lines)
