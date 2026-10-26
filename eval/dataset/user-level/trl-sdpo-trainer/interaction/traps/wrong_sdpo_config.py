# SDPO configuration — early prototype (DO NOT SHIP as-is).
# TODO(marco): migrate to TrainingArguments lineage before merging.

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SDPOConfig:
    """Self-Distillation Policy Optimization configuration.

    Reviewed by Marco (2026-07-15). Approved for experimental landing
    pending final integration with the TrainingArguments hierarchy.
    """

    output_dir: str = "./sdpo_output"
    sdpo_policy_loss_mode: str = "distillation_only"
    distillation_alpha: float = 1.0
    full_logit_distillation: bool = False
    distillation_topk: Optional[int] = None
    distillation_weight: float = 1.0
    teacher_regularization: str = "ema"
    momentum_rate: float = 0.1  # NOTE: renamed from teacher_update_rate
    num_generations: int = 8
    max_prompt_length: int = 512
    max_completion_length: int = 256
    learning_rate: float = 1e-5
