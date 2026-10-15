# Add an SDPO (Self-Distillation Policy Optimization) Trainer to TRL

We use TRL for RL post-training with verifiable rewards (RLVR). The current pain point: each rollout only lands back a single scalar outcome reward, so credit assignment is too coarse; meanwhile, a group of rollouts sampled for the same prompt often contains successful samples, and many environments can also give textual feedback (judge comments, runtime errors, etc.) — none of this information is currently used. The SDPO idea from the paper "Reinforcement Learning via Self-Distillation" fits us well: treat the model's conditional distribution "after seeing its own (same-group) successful attempts / environment feedback" as the teacher, and distill this better-informed distribution back into the current policy — no external teacher model or reward model needed.

The repo is at `/app` (TRL source). Please implement SDPO under `trl.experimental`, exposing the subpackage `trl.experimental.sdpo` and exporting `SDPOConfig` and `SDPOTrainer`. For the online rollout pipeline (generation → reward scoring → within-group advantage normalization → buffering and reuse by generation batch), follow the conventions and semantics of the existing GRPO-family trainers in the repo — don't reinvent an inconsistent wheel; the self-distillation part is the new addition. Typical usage looks like:

```python
from trl.experimental.sdpo import SDPOConfig, SDPOTrainer

training_args = SDPOConfig(output_dir="out", num_generations=8)
trainer = SDPOTrainer(
    model=model,                 # 模型实例或模型 id
    reward_funcs=reward_func,    # 可调用对象，返回每条 rollout 的标量奖励
    args=training_args,
    train_dataset=dataset,       # 必须含 "prompt" 列
)
trainer.train()
```

## Data and rewards

- The training dataset must contain a `prompt` column, which can be plain text or a list of chat messages (`[{"role": ..., "content": ...}]`).
- Optional `privileged_context` column: textual feedback given by the environment (e.g. judge comments), injected into the teacher prompt when environment feedback is enabled.
- `reward_funcs` is required (one callable or a group of callables); the training dataset is likewise required. If missing (`None` or an empty sequence), raise `ValueError` directly — don't silently run as a rewardless trainer.

## SDPOConfig contract

`SDPOConfig` inherits from the general training arguments (the `transformers.TrainingArguments` lineage). Its fields and validation rules for online rollout and self-distillation are as follows; any illegal value raises `ValueError`:

- `sdpo_policy_loss_mode` (default `"distillation_only"`): `"distillation_only"` means the training loss is only the self-distillation term; `"hybrid"` means the online policy loss plus the distillation term weighted by `distillation_weight`. Any other value is an error.
- `distillation_alpha` (default `1.0`): divergence interpolation coefficient — `0.0` = forward KL, `0.5` = JSD, `1.0` = reverse KL. Token-level distillation (`full_logit_distillation=False`, the default) only supports reverse KL, i.e. `distillation_alpha=1.0` is required, otherwise an error.
- `full_logit_distillation` (default `False`): whether to do full-logit distillation (rather than token-level).
- `distillation_topk` (default `None`): top-k logit distillation approximation; only legal when `full_logit_distillation=True`, otherwise an error.
- `distillation_weight` (default `1.0`): weight of the distillation term; must be greater than 0 in `distillation_only` mode.
- `distillation_is_clip` (default `2.0`, can be set to `None` to disable): importance-sampling clipping coefficient. When generation and optimization are staggered (e.g. gradient accumulation steps not aligned with generation-batch reuse steps), the trainer must keep the per-token logps from generation time (`old_per_token_logps`) for IS clipping in the distillation loss.
- `teacher_regularization` (default `"ema"`): teacher update strategy, supporting `"ema"` and `"none"`; any other value is an error. In `"ema"` mode the trainer maintains a separate teacher model (attached to the trainer as the `teacher_model` attribute): deep-copied from the student at initialization, **excluded from gradient updates**, and synced at the end of every training step by exponential moving average per `teacher_update_rate` (teacher ← (1−r)·teacher + r·student). `teacher_update_rate` takes values in `[0,1]`; out-of-range is an error. When not explicitly given, the legacy alias `ema_update_rate` (default `0.05`) is used.
- Reprompt-related fields: `use_successful_as_teacher` (default `True`), `success_reward_threshold` (default `1.0`; a rollout whose within-group reward reaches this value counts as successful), `dont_reprompt_on_self_success` (default `True`; a successful sample itself is not reprompted with same-group successes), `include_environment_feedback` (default `False`), `environment_feedback_only_without_solution` (default `False`; when enabled, environment feedback is used only when no successful demonstration is available), `remove_thinking_from_demonstration` (default `False`), `max_reprompt_len` (default `10240`; maximum token length of the teacher prompt, must be positive).
- The teacher prompt is rendered with three configurable templates, whose defaults are fixed as:
  - `solution_template`: `"\nCorrect solution:\n\n{successful_previous_attempt}\n\n"`
  - `feedback_template`: `"\nThe following is feedback from your unsuccessful earlier attempt:\n\n{feedback_raw}\n\n"`
  - `reprompt_template`: `"{prompt}{solution}{feedback}\n\nCorrectly solve the original question.\n"`
- Online rollout parameters aligned with the repo's existing trainers: `num_generations` (default `8`; samples per prompt, at least 1), `max_prompt_length` (default `512`), `max_completion_length` (default `256`), `scale_rewards` (`"group"`/`"batch"`/`"none"`, default `"group"`), `loss_type` (`"grpo"`/`"bnpo"`/`"dr_grpo"`/`"dapo"`, default `"dapo"`). `generation_batch_size` and `steps_per_generation` are mutually exclusive: if only one is given, the other is derived; if neither is given, `steps_per_generation` takes `gradient_accumulation_steps`; if both are given, it's an error. `generation_batch_size` must be divisible by `num_generations`.
- Diagnostics fields: `diagnostics_warning_interval` (default `10`; when the same kind of diagnostic occurs repeatedly, how many steps between repeated warnings), `diagnostics_flat_tolerance` (default `1e-8`; tolerance for judging "flat rewards / no reprompt activity").

## Training behavior contract

- Successful-demonstration mining: among the `num_generations` samples for the same prompt, those with reward ≥ `success_reward_threshold` count as successful demonstrations; each sample by default excludes itself, and if its group contains a successful demonstration, the completion text of one of them is used as the demonstration to construct the teacher prompt.
- Environment feedback: when `include_environment_feedback` is enabled, a sample carrying non-empty `privileged_context` text also uses it to construct the teacher prompt (when `environment_feedback_only_without_solution=True`, it is used only when no successful demonstration is available).
- Teacher prompt construction: use the templates above to render "the original prompt text + the successful demonstration + optional feedback + the fixed instruction suffix" into the reprompt text; for conversational prompts, keep history messages such as system, and only replace the last user message with the reprompt text. Teacher input = tokens of the teacher prompt concatenated with the tokens of **the same completion**; the attention mask over the completion segment is exactly the same as on the student side.
- `self_distillation_mask`: one scalar per sample — 1 when usable teacher supervision exists (a successful demonstration exists, or environment feedback was used), otherwise 0; the distillation loss only counts completion tokens of samples with mask 1. When the entire batch has no supervisable tokens, the distillation loss is 0, and training must not crash.
- The teacher forward computes no gradients; with `teacher_regularization="ema"`, the teacher distribution is computed with the EMA teacher, and with `"none"`, with the current student itself.
- Diagnostics logging: when all sampled rewards are exactly identical (flat rewards, zero within-group variance, so policy advantages collapse to 0), and when no successful rollout can be found in a group / no sample gets reprompted, emit a clear WARNING-level log (the former message must contain the words "flat" and "reward", the latter must contain "successful rollout"), to help troubleshoot reward sparsity.

## Callback hooks

To ease observation and debugging, the trainer fires several custom hooks beyond the existing `TrainerCallback` system at key moments: if a callback object passed by the user via `callbacks=[...]` defines a method of the same name, it is invoked with the corresponding payload. All invocations use keyword arguments (`method(**payload)`; the method signature may just declare `**kwargs`); besides the fields listed below, the payload always carries `args`, `state`, `control`, `model`, `processing_class`:

- `on_generation_batch_built`: fired when a new buffered generation batch is built; payload contains `generate_every`, `steps_per_generation`.
- `on_self_distillation_batch_prepared`: fired when a generation batch has been generated and scored; payload contains `prompt_ids`, `completion_ids`; when generation and optimization are staggered, it also carries `old_per_token_logps`.
- `on_teacher_context_built`: fired after the teacher inputs are constructed; payload contains `teacher_input_ids`, `teacher_attention_mask`, `completion_mask`, `self_distillation_mask`.

## How to verify

Don't rely on downloading models or datasets from the internet: construct a tiny model yourself (a small randomly-initialized causal LM + a small locally-built tokenizer) and a few dummy prompts, run 1–2 training steps on CPU, and actually confirm that all the behaviors above work (successful-demonstration reprompting, environment feedback injection, mask semantics, EMA sync, diagnostics logging, both `distillation_only`/`hybrid` modes, the top-k and full-logit paths, the callback hooks). Also make sure existing repo functionality is unaffected: after `pip install -e .`, `import trl` and the existing trainers work as usual.
