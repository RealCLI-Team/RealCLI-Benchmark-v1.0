class WebhookPublisher:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def publish(self, topic: str, payload: dict) -> None:
        self.events.append({"topic": topic, "payload": payload})
