# Behavior tests for the SDPO (Self-Distillation Policy Optimization) trainer.
#
# Everything runs offline on CPU: a tiny randomly-initialized Qwen2 causal LM
# and a tiny byte-level tokenizer are built locally (no hub downloads). The
# tests assert the public contract of trl.experimental.sdpo — config
# defaults/validation, successful-rollout and environment-feedback teacher
# reprompting, self-distillation masking, EMA teacher synchronization, loss
# modes, importance-sampling log-prob retention, callback hooks, and
# degenerate-reward diagnostics — by running one or two real training steps.

import logging
import os

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_DATASETS_OFFLINE", "1")
os.environ.setdefault("TRL_EXPERIMENTAL_SILENCE", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import pytest
import torch
from datasets import Dataset
from transformers import (
    PreTrainedTokenizerFast,
    Qwen2Config,
    Qwen2ForCausalLM,
    TrainerCallback,
    TrainingArguments,
)

from trl.experimental.sdpo import SDPOConfig, SDPOTrainer


pytestmark = pytest.mark.timeout(600)

PAD_TOKEN, BOS_TOKEN, EOS_TOKEN, UNK_TOKEN = "<pad>", "<bos>", "<eos>", "<unk>"

CHAT_TEMPLATE = (
    "{% for message in messages %}"
    "<{{ message['role'] }}> {{ message['content'] }}\n"
    "{% endfor %}"
    "{% if add_generation_prompt %}<assistant> {% endif %}"
)


def build_tiny_tokenizer():
    """A byte-level tokenizer built from scratch: 256 byte tokens + specials.

    Byte-level encoding round-trips any text exactly and every id in
    [0, vocab_size) decodes, so randomly generated ids stay decodable.
    """
    from tokenizers import Tokenizer, decoders, models, pre_tokenizers

    alphabet = sorted(pre_tokenizers.ByteLevel.alphabet())
    vocab = {ch: idx for idx, ch in enumerate(alphabet)}
    for special in (PAD_TOKEN, BOS_TOKEN, EOS_TOKEN, UNK_TOKEN):
        vocab[special] = len(vocab)
    backend = Tokenizer(models.BPE(vocab=vocab, merges=[], unk_token=UNK_TOKEN))
    backend.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
    backend.decoder = decoders.ByteLevel()
    tokenizer = PreTrainedTokenizerFast(
        tokenizer_object=backend,
        unk_token=UNK_TOKEN,
        bos_token=BOS_TOKEN,
        eos_token=EOS_TOKEN,
        pad_token=PAD_TOKEN,
    )
    # Causal LMs like Qwen2 do not accept token_type_ids; keep them out of the
    # default outputs so the tokenizer can drive generation directly.
    tokenizer.model_input_names = ["input_ids", "attention_mask"]
    tokenizer.chat_template = CHAT_TEMPLATE
    return tokenizer


def build_tiny_model(tokenizer):
    torch.manual_seed(0)
    config = Qwen2Config(
        vocab_size=len(tokenizer),
        hidden_size=32,
        intermediate_size=64,
        num_hidden_layers=2,
        num_attention_heads=2,
        num_key_value_heads=2,
        max_position_embeddings=512,
        tie_word_embeddings=False,
        pad_token_id=tokenizer.pad_token_id,
        bos_token_id=tokenizer.bos_token_id,
        eos_token_id=tokenizer.eos_token_id,
    )
    return Qwen2ForCausalLM(config)


def zero_reward(prompts=None, **kwargs):
    return [0.0] * len(prompts)


def first_success_reward(prompts=None, **kwargs):
    """Only the first sample of each generation group succeeds."""
    return [1.0, 0.0][: len(prompts)]


class CaptureCallback(TrainerCallback):
    """Captures the payloads of the self-distillation callback hooks."""

    def __init__(self):
        self.generation_batch_payloads = []
        self.batch_prepared_payloads = []
        self.teacher_context_payloads = []

    def on_generation_batch_built(self, **kwargs):
        self.generation_batch_payloads.append(kwargs)

    def on_self_distillation_batch_prepared(self, **kwargs):
        self.batch_prepared_payloads.append(kwargs)

    def on_teacher_context_built(self, **kwargs):
        self.teacher_context_payloads.append(kwargs)


def make_trainer(tmp_path, dataset, reward_func, config_overrides=None, callbacks=()):
    tokenizer = build_tiny_tokenizer()
    model = build_tiny_model(tokenizer)
    base_kwargs = dict(
        output_dir=str(tmp_path),
        learning_rate=0.1,
        bf16=False,  # CPU-only environment: the TRL default would require a bf16-capable GPU
        per_device_train_batch_size=1,
        generation_batch_size=2,
        num_generations=2,
        max_completion_length=8,
        max_steps=1,
        report_to="none",
        save_strategy="no",
        logging_steps=1,
        seed=42,
    )
    for key, value in (config_overrides or {}).items():
        if value is None and key in base_kwargs:
            del base_kwargs[key]
        else:
            base_kwargs[key] = value
    args = SDPOConfig(**base_kwargs)
    return SDPOTrainer(
        model=model,
        reward_funcs=reward_func,
        args=args,
        train_dataset=dataset,
        processing_class=tokenizer,
        callbacks=list(callbacks),
    )


def teacher_texts(payload):
    processing_class = payload["processing_class"]
    return [
        processing_class.decode(ids, skip_special_tokens=True)
        for ids in payload["teacher_input_ids"]
    ]


def decode_completion(payload, row, pad_token_id):
    completion_ids = payload["completion_ids"][row]
    completion_ids = completion_ids[completion_ids != pad_token_id]
    return payload["processing_class"].decode(completion_ids, skip_special_tokens=True)


# ---------------------------------------------------------------------------
# Package surface
# ---------------------------------------------------------------------------

def test_existing_package_surface_unaffected():
    # The pre-existing package must stay importable and intact.
    import trl

    assert hasattr(trl, "GRPOConfig")
    assert hasattr(trl, "GRPOTrainer")


# ---------------------------------------------------------------------------
# SDPOConfig: defaults and validation
# ---------------------------------------------------------------------------

def test_config_defaults():
    cfg = SDPOConfig(output_dir="/tmp/sdpo-cfg-defaults", report_to="none", bf16=False)
    assert isinstance(cfg, TrainingArguments)
    # Loss mode and divergence settings.
    assert cfg.sdpo_policy_loss_mode == "distillation_only"
    assert cfg.distillation_alpha == 1.0
    assert cfg.full_logit_distillation is False
    assert cfg.distillation_topk is None
    assert cfg.distillation_weight == 1.0
    assert cfg.distillation_is_clip == 2.0
    # Teacher regularization: EMA by default, rate from the deprecated alias.
    assert cfg.teacher_regularization == "ema"
    assert cfg.teacher_update_rate == 0.05
    # Reprompting switches.
    assert cfg.use_successful_as_teacher is True
    assert cfg.success_reward_threshold == 1.0
    assert cfg.dont_reprompt_on_self_success is True
    assert cfg.include_environment_feedback is False
    assert cfg.environment_feedback_only_without_solution is False
    assert cfg.remove_thinking_from_demonstration is False
    assert cfg.max_reprompt_len == 10240
    # Template defaults.
    assert "Correct solution:" in cfg.solution_template
    assert "{successful_previous_attempt}" in cfg.solution_template
    assert "{feedback_raw}" in cfg.feedback_template
    assert "{prompt}{solution}{feedback}" in cfg.reprompt_template
    assert "Correctly solve the original question." in cfg.reprompt_template
    # Online-rollout parameters.
    assert cfg.num_generations == 8
    assert cfg.max_prompt_length == 512
    assert cfg.max_completion_length == 256
    assert cfg.scale_rewards == "group"
    assert cfg.loss_type == "dapo"
    assert cfg.diagnostics_warning_interval == 10

    # `teacher_update_rate` falls back to the deprecated `ema_update_rate`
    # alias, and an explicit value wins over the alias.
    cfg = SDPOConfig(output_dir="/tmp/sdpo-cfg-alias", report_to="none", bf16=False, ema_update_rate=0.2)
    assert cfg.teacher_update_rate == 0.2
    cfg = SDPOConfig(
        output_dir="/tmp/sdpo-cfg-alias2", report_to="none", bf16=False,
        teacher_update_rate=0.3, ema_update_rate=0.2,
    )
    assert cfg.teacher_update_rate == 0.3


def test_config_validation():
    def make(**overrides):
        kwargs = dict(output_dir="/tmp/sdpo-cfg-invalid", report_to="none", bf16=False)
        kwargs.update(overrides)
        return SDPOConfig(**kwargs)

    with pytest.raises(ValueError):
        make(sdpo_policy_loss_mode="bogus")
    with pytest.raises(ValueError):
        make(teacher_regularization="bogus")
    with pytest.raises(ValueError):
        make(teacher_update_rate=1.5)
    with pytest.raises(ValueError):
        make(teacher_update_rate=-0.1)
    # Top-k logit distillation requires full-logit mode.
    with pytest.raises(ValueError):
        make(distillation_topk=100)
    # Token-level distillation requires reverse KL (alpha == 1.0).
    with pytest.raises(ValueError):
        make(distillation_alpha=0.5)
    # distillation_only mode requires a positive distillation weight.
    with pytest.raises(ValueError):
        make(distillation_weight=0.0)
    with pytest.raises(ValueError):
        make(max_reprompt_len=0)
    with pytest.raises(ValueError):
        make(scale_rewards="bogus")
    with pytest.raises(ValueError):
        make(loss_type="bogus")
    # generation_batch_size must be divisible by num_generations.
    with pytest.raises(ValueError):
        make(per_device_train_batch_size=1, generation_batch_size=3, num_generations=2)
    # generation_batch_size and steps_per_generation are mutually exclusive.
    with pytest.raises(ValueError):
        make(per_device_train_batch_size=1, generation_batch_size=4, steps_per_generation=2)


# ---------------------------------------------------------------------------
# Required constructor arguments
# ---------------------------------------------------------------------------

def test_reward_funcs_and_dataset_are_required(tmp_path):
    dataset = Dataset.from_dict({"prompt": ["Solve 2+2."]})
    cfg = SDPOConfig(
        output_dir=str(tmp_path),
        per_device_train_batch_size=1,
        generation_batch_size=2,
        num_generations=2,
        report_to="none",
        bf16=False,
    )
    with pytest.raises(ValueError):
        make_trainer(tmp_path, dataset, None, callbacks=[])
    with pytest.raises(ValueError):
        make_trainer(tmp_path, dataset, [], callbacks=[])
    trainer_kwargs_model = build_tiny_model(build_tiny_tokenizer())
    with pytest.raises(ValueError):
        SDPOTrainer(
            model=trainer_kwargs_model,
            reward_funcs=zero_reward,
            args=cfg,
            train_dataset=None,
            processing_class=build_tiny_tokenizer(),
        )


# ---------------------------------------------------------------------------
# Training behavior: successful-rollout teacher reprompting
# ---------------------------------------------------------------------------

def test_successful_rollout_reprompts_teacher(tmp_path):
    dataset = Dataset.from_dict({"prompt": ["Solve 2+2."]})
    capture = CaptureCallback()
    trainer = make_trainer(
        tmp_path,
        dataset,
        first_success_reward,
        {"success_reward_threshold": 0.5},
        callbacks=[capture],
    )
    trainer.train()

    assert any("train_loss" in entry for entry in trainer.state.log_history)
    assert capture.generation_batch_payloads, "on_generation_batch_built never fired"
    assert capture.batch_prepared_payloads, "on_self_distillation_batch_prepared never fired"
    assert capture.teacher_context_payloads, "on_teacher_context_built never fired"

    payload = capture.teacher_context_payloads[0]
    for key in ("teacher_input_ids", "teacher_attention_mask", "completion_mask", "self_distillation_mask"):
        assert isinstance(payload.get(key), torch.Tensor), key

    # Sample 0 is the successful one and skips itself (default
    # dont_reprompt_on_self_success=True); sample 1 gets sample 0 as teacher
    # demonstration.
    sd_mask = payload["self_distillation_mask"].detach().cpu()
    assert sd_mask.tolist() == [0.0, 1.0]

    # Teacher inputs are teacher prompt tokens concatenated with the same
    # completion tokens; the completion attention mask matches the student's.
    teacher_mask = payload["teacher_attention_mask"].detach().cpu()
    completion_mask = payload["completion_mask"].detach().cpu()
    completion_length = completion_mask.shape[1]
    assert payload["teacher_input_ids"].shape == payload["teacher_attention_mask"].shape
    assert torch.equal(teacher_mask[:, -completion_length:], completion_mask)

    texts = teacher_texts(payload)
    assert "Correct solution:" not in texts[0]
    assert "Correct solution:" in texts[1]
    assert "Correctly solve the original question." in texts[1]

    # The successful sample's own completion text is embedded verbatim in the
    # other sample's teacher prompt.
    prepared = capture.batch_prepared_payloads[0]
    demo_text = decode_completion(prepared, 0, payload["processing_class"].pad_token_id)
    if demo_text.strip():
        assert demo_text in texts[1]


def test_environment_feedback_reprompts_teacher(tmp_path):
    dataset = Dataset.from_dict(
        {
            "prompt": ["Try the puzzle again."],
            "privileged_context": ["Your earlier answer violated the format requirements."],
        }
    )
    capture = CaptureCallback()
    trainer = make_trainer(
        tmp_path,
        dataset,
        zero_reward,
        {"include_environment_feedback": True},
        callbacks=[capture],
    )
    trainer.train()

    assert any("train_loss" in entry for entry in trainer.state.log_history)
    assert capture.teacher_context_payloads, "on_teacher_context_built never fired"

    payload = capture.teacher_context_payloads[0]
    # No successful rollouts, but every sample carries non-empty feedback, so
    # every sample is supervised.
    assert payload["self_distillation_mask"].detach().cpu().tolist() == [1.0, 1.0]
    texts = teacher_texts(payload)
    for text in texts:
        assert "format requirements" in text
        assert "Correct solution:" not in text


def test_conversational_prompt_preserves_system_context(tmp_path):
    dataset = Dataset.from_dict(
        {
            "prompt": [
                [
                    {"role": "system", "content": "You are a careful assistant."},
                    {"role": "user", "content": "Solve 2+2."},
                ]
            ]
        }
    )
    capture = CaptureCallback()
    trainer = make_trainer(
        tmp_path,
        dataset,
        first_success_reward,
        {"success_reward_threshold": 0.5},
        callbacks=[capture],
    )
    trainer.train()

    assert capture.teacher_context_payloads, "on_teacher_context_built never fired"
    payload = capture.teacher_context_payloads[0]
    assert payload["self_distillation_mask"].detach().cpu().tolist() == [0.0, 1.0]
    texts = teacher_texts(payload)
    # The reprompted teacher keeps the conversation's earlier messages and the
    # original user question.
    assert "careful assistant" in texts[1]
    assert "Solve 2+2" in texts[1]
    assert "Correct solution:" in texts[1]


# ---------------------------------------------------------------------------
# Training behavior: EMA teacher synchronization
# ---------------------------------------------------------------------------

def test_ema_teacher_tracks_student_with_lag(tmp_path):
    dataset = Dataset.from_dict({"prompt": ["Solve 2+2."]})
    trainer = make_trainer(
        tmp_path,
        dataset,
        first_success_reward,
        # Two steps so the supervised sample (chunk 1) gets an optimizer step
        # with a non-zero distillation loss and the student actually moves.
        {"success_reward_threshold": 0.5, "teacher_update_rate": 0.5, "max_steps": 2},
    )
    student = trainer.model
    before = {name: param.detach().clone() for name, param in student.named_parameters()}

    trainer.train()

    teacher = getattr(trainer, "teacher_model", None)
    assert teacher is not None, "EMA mode must maintain a teacher model"
    # The teacher never takes gradient updates of its own.
    assert all(not param.requires_grad for param in teacher.parameters())

    after = dict(student.named_parameters())
    teacher_params = dict(teacher.named_parameters())
    moved = [name for name in before if not torch.allclose(before[name], after[name])]
    assert moved, "student parameters did not change during training"
    # After the EMA sync, the teacher has moved away from the initial student
    # weights but still lags behind the updated student.
    lagged = [
        name
        for name in moved
        if not torch.allclose(teacher_params[name], after[name])
        and not torch.allclose(teacher_params[name], before[name])
    ]
    assert lagged, "teacher model does not EMA-track the student"


def test_training_with_full_logit_topk_hybrid_mode(tmp_path):
    dataset = Dataset.from_dict({"prompt": ["Solve 2+2."]})
    capture = CaptureCallback()
    trainer = make_trainer(
        tmp_path,
        dataset,
        first_success_reward,
        {
            "success_reward_threshold": 0.5,
            "sdpo_policy_loss_mode": "hybrid",
            "full_logit_distillation": True,
            "distillation_topk": 50,
        },
        callbacks=[capture],
    )
    trainer.train()

    assert any("train_loss" in entry for entry in trainer.state.log_history)
    payload = capture.teacher_context_payloads[0]
    assert payload["self_distillation_mask"].detach().cpu().tolist() == [0.0, 1.0]


# ---------------------------------------------------------------------------
# Training behavior: importance-sampling log-probs and buffered batches
# ---------------------------------------------------------------------------

def test_old_per_token_logps_kept_when_generation_misaligned(tmp_path):
    dataset = Dataset.from_dict({"prompt": ["Solve 2+2.", "Solve 3+3."]})
    capture = CaptureCallback()
    trainer = make_trainer(
        tmp_path,
        dataset,
        first_success_reward,
        {
            "success_reward_threshold": 0.5,
            "generation_batch_size": None,  # derived from steps_per_generation
            "steps_per_generation": 2,
            "gradient_accumulation_steps": 3,
        },
        callbacks=[capture],
    )
    trainer.train()

    assert capture.generation_batch_payloads, "on_generation_batch_built never fired"
    generation_payload = capture.generation_batch_payloads[0]
    assert generation_payload["generate_every"] == 2
    assert generation_payload["steps_per_generation"] == 2

    assert capture.batch_prepared_payloads, "on_self_distillation_batch_prepared never fired"
    old_logps = capture.batch_prepared_payloads[0].get("old_per_token_logps")
    assert isinstance(old_logps, torch.Tensor)
    assert old_logps.dim() == 2


# ---------------------------------------------------------------------------
# Diagnostics: flat rewards and missing successful rollouts
# ---------------------------------------------------------------------------

def test_warns_on_flat_rewards_and_no_successful_rollouts(tmp_path, caplog):
    dataset = Dataset.from_dict({"prompt": ["Solve 2+2."]})
    trainer = make_trainer(
        tmp_path,
        dataset,
        zero_reward,
        {"diagnostics_warning_interval": 1},
    )
    with caplog.at_level(logging.WARNING):
        trainer.train()

    # Training must not crash even though no sample can be supervised.
    assert any("train_loss" in entry for entry in trainer.state.log_history)

    warnings = [record.getMessage() for record in caplog.records if record.levelno >= logging.WARNING]
    assert any("flat" in message.lower() and "reward" in message.lower() for message in warnings), \
        "missing flat-rewards diagnostic warning"
    assert any("successful rollout" in message.lower() for message in warnings), \
        "missing no-successful-rollouts diagnostic warning"
