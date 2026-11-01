from typing import Protocol


class ContextProvider(Protocol):
    name: str

    def is_available(self, user_config: dict) -> bool: ...
    async def fetch_context(self, transcript: str, user_config: dict) -> dict: ...
