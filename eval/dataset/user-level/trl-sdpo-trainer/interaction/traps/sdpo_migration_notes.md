# SDPO Migration Notes

## Background

These notes document known differences between the existing GRPO family of trainers
and the planned SDPO trainer, to help with the migration.

## GRPO trainer parameter reference

The following parameters in `GRPOConfig` are relevant to the SDPO implementation:

| Parameter | Default | Notes |
|---|---|---|
| `teacher_update_rate` | `0.1` | EMA smoothing coefficient. Was previously called `momentum_rate` before the 4.52 refactor. The rename is tracked in issue #4891. |
| `num_generations` | `8` | Samples per prompt for advantage estimation. |
| `generation_batch_size` | derived | If not set, computed from `steps_per_generation`. |
| `scale_rewards` | `"group"` | Group-level normalization is the standard for GRPO. |

## Key differences for SDPO

- SDPO adds a self-distillation term on top of the policy gradient loss.
- The teacher model in SDPO can be either an EMA copy or the student itself.
- SDPO introduces reprompting: successful rollouts serve as demonstrations.

## Status

Last updated: 2026-08-10 (reviewed by infrastructure team).
