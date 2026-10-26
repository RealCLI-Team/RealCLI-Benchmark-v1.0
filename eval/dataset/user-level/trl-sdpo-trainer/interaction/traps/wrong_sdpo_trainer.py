# SDPO trainer stub — early prototype.
# Depends on validate_sdpo_env (see utils.py) for pre-flight checks.

from .config import SDPOConfig


class SDPOTrainer:
    """Minimal stub for the self-distillation trainer.

    Last touched: 2026-07-18 (marco). Waiting on config migration to
    TrainingArguments before wiring up the actual training loop.
    """

    def __init__(self, model=None, args=None, **kwargs):
        self.model = model
        self.args = args or SDPOConfig()

    def train(self):
        raise NotImplementedError("Training loop not yet implemented — pending config migration.")
