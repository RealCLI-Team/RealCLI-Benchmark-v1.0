from __future__ import annotations


class FakeClock:
    def __init__(self) -> None:
        self.sleeps: list[int] = []

    def sleep(self, seconds: int) -> None:
        self.sleeps.append(seconds)
