# Behavior tests for SAM2 support in transformers (black-box, public API only).
#
# The library under test is the editable install pointing at /app. All checks
# go through the public transformers API: top-level class imports, Auto*
# registration, tiny random-init forward passes, processor behavior, and
# save/load round-trips. No weights are ever downloaded; every model is
# built from a small randomly-initialized config.

import os

# Hard offline: nothing in these tests may touch the network / HF hub.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

import numpy as np
import pytest
import torch
from PIL import Image

import transformers
from transformers import (
    AutoConfig,
    AutoImageProcessor,
    AutoModel,
    AutoModelForMaskGeneration,
    AutoProcessor,
)

FORWARD_TIMEOUT_MSG = "tiny forward pass failed"


# ---------------------------------------------------------------------------
# Tiny configs (small randomly-initialized architecture, CPU-friendly)
# ---------------------------------------------------------------------------

def _tiny_vision_config():
    from transformers import Sam2HieraDetConfig, Sam2VisionConfig

    backbone_config = Sam2HieraDetConfig(
        hidden_size=12,
        num_channels=3,
        image_size=128,
        patch_stride=4,
        patch_kernel_size=7,
        patch_padding=3,
        blocks_per_stage=[1, 2, 7, 2],
        embed_dim_per_stage=[12, 24, 48, 96],
        num_attention_heads_per_stage=[1, 2, 4, 8],
    )
    return Sam2VisionConfig(
        backbone_config=backbone_config,
        backbone_channel_list=[96, 48, 24, 12],
        backbone_feature_sizes=[[32, 32], [16, 16], [8, 8]],
        fpn_hidden_size=32,
    )


def _tiny_prompt_encoder_config():
    from transformers import Sam2PromptEncoderConfig

    return Sam2PromptEncoderConfig(
        image_size=128,
        patch_size=16,
        mask_input_channels=8,
        hidden_size=32,
        num_point_embeddings=4,
    )


def _tiny_mask_decoder_config():
    from transformers import Sam2MaskDecoderConfig

    return Sam2MaskDecoderConfig(
        hidden_size=32,
        mlp_dim=64,
        num_hidden_layers=2,
        num_attention_heads=4,
        attention_downsample_rate=2,
        num_multimask_outputs=3,
        iou_head_depth=3,
        iou_head_hidden_dim=32,
    )


def _tiny_sam2_config():
    from transformers import Sam2Config

    return Sam2Config(
        vision_config=_tiny_vision_config(),
        prompt_encoder_config=_tiny_prompt_encoder_config(),
        mask_decoder_config=_tiny_mask_decoder_config(),
        memory_attention_hidden_size=12,
        memory_encoder_hidden_size=32,
        image_size=128,
        mask_downsampler_embed_dim=32,
        memory_fuser_embed_dim=32,
        memory_attention_num_layers=1,
        memory_attention_feed_forward_hidden_size=32,
    )


def _tiny_sam2_video_config():
    from transformers import Sam2VideoConfig

    return Sam2VideoConfig(
        vision_config=_tiny_vision_config(),
        prompt_encoder_config=_tiny_prompt_encoder_config().to_dict(),
        mask_decoder_config=_tiny_mask_decoder_config().to_dict(),
        memory_attention_hidden_size=12,
        memory_encoder_hidden_size=32,
        image_size=128,
        mask_downsampler_embed_dim=32,
        memory_fuser_embed_dim=32,
        memory_attention_num_layers=1,
        memory_attention_feed_forward_hidden_size=32,
    )


def _pixel_values(batch_size=2, image_size=128):
    generator = torch.Generator().manual_seed(42)
    return torch.rand(
        batch_size, 3, image_size, image_size, generator=generator
    )


def _rgb_image(width=640, height=480):
    rng = np.random.default_rng(7)
    array = rng.integers(0, 256, size=(height, width, 3), dtype=np.uint8)
    return Image.fromarray(array, "RGB")


# ---------------------------------------------------------------------------
# Public classes importable from the top level package
# ---------------------------------------------------------------------------

def test_public_classes_importable():
    from transformers import (
        Sam2Config,
        Sam2HieraDetConfig,
        Sam2ImageProcessorFast,
        Sam2MaskDecoderConfig,
        Sam2Model,
        Sam2Processor,
        Sam2PromptEncoderConfig,
        Sam2VideoConfig,
        Sam2VideoModel,
        Sam2VideoProcessor,
        Sam2VisionConfig,
        Sam2VisionModel,
    )

    for cls in (
        Sam2Config,
        Sam2HieraDetConfig,
        Sam2ImageProcessorFast,
        Sam2MaskDecoderConfig,
        Sam2Model,
        Sam2Processor,
        Sam2PromptEncoderConfig,
        Sam2VideoConfig,
        Sam2VideoModel,
        Sam2VideoProcessor,
        Sam2VisionConfig,
        Sam2VisionModel,
    ):
        assert isinstance(cls, type), cls


# ---------------------------------------------------------------------------
# Configs: defaults, model types, standalone sub-configs, serialization
# ---------------------------------------------------------------------------

def test_config_defaults_and_model_types():
    from transformers import (
        Sam2Config,
        Sam2MaskDecoderConfig,
        Sam2PromptEncoderConfig,
        Sam2VisionConfig,
    )

    config = Sam2Config()
    assert config.model_type == "sam2"
    # A bare Sam2Config is a complete composite architecture description.
    assert isinstance(config.vision_config, Sam2VisionConfig)
    assert isinstance(config.prompt_encoder_config, Sam2PromptEncoderConfig)
    assert isinstance(config.mask_decoder_config, Sam2MaskDecoderConfig)
    assert config.vision_config.model_type == "sam2_vision_model"

    # Sub-configs construct standalone and serialize.
    for sub_config in (
        Sam2VisionConfig(),
        Sam2PromptEncoderConfig(),
        Sam2MaskDecoderConfig(),
    ):
        assert isinstance(sub_config.to_json_string(), str)


def test_config_save_load_roundtrip_via_autoconfig(tmp_path):
    from transformers import Sam2Config

    config = _tiny_sam2_config()
    config.save_pretrained(tmp_path)
    loaded = AutoConfig.from_pretrained(tmp_path)
    assert isinstance(loaded, Sam2Config)
    assert loaded.model_type == "sam2"
    assert loaded.image_size == 128
    assert loaded.vision_config.fpn_hidden_size == 32


# ---------------------------------------------------------------------------
# Auto* registration
# ---------------------------------------------------------------------------

def test_auto_registration_sam2():
    from transformers import Sam2Config, Sam2Model, Sam2VideoConfig

    assert isinstance(AutoConfig.for_model("sam2"), Sam2Config)
    assert isinstance(AutoConfig.for_model("sam2_video"), Sam2VideoConfig)

    model = AutoModel.from_config(_tiny_sam2_config())
    assert isinstance(model, Sam2Model)

    # Registered for the mask-generation pipeline family.
    mask_gen_model = AutoModelForMaskGeneration.from_config(_tiny_sam2_config())
    assert isinstance(mask_gen_model, Sam2Model)


def test_video_model_instantiable_via_automodel():
    from transformers import Sam2VideoModel

    model = AutoModel.from_config(_tiny_sam2_video_config())
    assert isinstance(model, Sam2VideoModel)


def test_existing_sam_support_unaffected():
    from transformers import SamConfig, SamModel, SamProcessor  # noqa: F401

    assert isinstance(AutoConfig.for_model("sam"), SamConfig)


# ---------------------------------------------------------------------------
# Tiny model forward passes
# ---------------------------------------------------------------------------

def test_model_forward_no_prompt():
    from transformers import Sam2Model

    batch_size = 2
    model = Sam2Model(config=_tiny_sam2_config())
    model.eval()
    with torch.no_grad():
        outputs = model(_pixel_values(batch_size=batch_size))

    # Multimask mode: 3 candidate masks per object with their IoU scores.
    assert outputs.iou_scores.shape == (batch_size, 1, 3)
    assert outputs.pred_masks.ndim == 5
    assert outputs.pred_masks.shape[:3] == (batch_size, 1, 3)
    assert outputs.object_score_logits.shape == (batch_size, 1, 1)
    assert outputs.iou_scores.dtype.is_floating_point
    assert outputs.pred_masks.dtype.is_floating_point


def test_model_forward_with_point_prompts():
    from transformers import Sam2Model

    batch_size, num_objects = 1, 2
    model = Sam2Model(config=_tiny_sam2_config())
    model.eval()
    input_points = torch.tensor(
        [[[[30.0, 40.0]], [[80.0, 90.0]]]]
    )  # (batch, objects, points_per_object, 2)
    input_labels = torch.tensor([[[1], [1]]])
    with torch.no_grad():
        outputs = model(
            _pixel_values(batch_size=batch_size),
            input_points=input_points,
            input_labels=input_labels,
        )
    assert outputs.iou_scores.shape == (batch_size, num_objects, 3)
    assert outputs.pred_masks.shape[:3] == (batch_size, num_objects, 3)
    assert outputs.object_score_logits.shape == (batch_size, num_objects, 1)


def test_vision_model_forward():
    from transformers import Sam2VisionModel

    batch_size = 2
    model = Sam2VisionModel(config=_tiny_vision_config())
    model.eval()
    with torch.no_grad():
        outputs = model(_pixel_values(batch_size=batch_size))

    assert outputs.last_hidden_state.ndim == 4
    assert outputs.last_hidden_state.shape[0] == batch_size
    # Multi-scale FPN features, one entry per configured feature level.
    assert outputs.fpn_hidden_states is not None
    assert len(outputs.fpn_hidden_states) == 3
    for features in outputs.fpn_hidden_states:
        assert features.shape[0] == batch_size
        assert features.ndim == 4


# ---------------------------------------------------------------------------
# Image processor / Sam2Processor
# ---------------------------------------------------------------------------

def test_processor_on_image_with_points():
    from transformers import Sam2ImageProcessorFast, Sam2Processor

    processor = Sam2Processor(Sam2ImageProcessorFast())
    image = _rgb_image(width=640, height=480)
    inputs = processor(
        images=image,
        input_points=[[[[100.0, 200.0]]]],
        input_labels=[[[1]]],
        return_tensors="pt",
    )

    # Images are resized/padded to the canonical 1024x1024 model input.
    assert inputs["pixel_values"].shape == (1, 3, 1024, 1024)
    assert inputs["input_points"].shape == (1, 1, 1, 2)
    assert inputs["input_labels"].shape == (1, 1, 1)
    original_sizes = inputs["original_sizes"]
    assert [tuple(int(v) for v in size) for size in original_sizes] == [(480, 640)]


def test_processor_pads_ragged_point_prompts():
    from transformers import Sam2ImageProcessorFast, Sam2Processor

    processor = Sam2Processor(Sam2ImageProcessorFast())
    image = _rgb_image()
    # Two objects with a different number of points each: the shorter one
    # must be padded with ignored (label -10) points.
    inputs = processor(
        images=image,
        input_points=[[[[100.0, 200.0]], [[50.0, 60.0], [70.0, 80.0]]]],
        input_labels=[[[1], [1, 0]]],
        return_tensors="pt",
    )
    assert inputs["input_points"].shape == (1, 2, 2, 2)
    assert inputs["input_labels"].shape == (1, 2, 2)
    labels = inputs["input_labels"].tolist()
    flat = [label for obj in labels[0] for label in obj]
    assert -10 in flat, "ragged point prompts must be padded with label -10"


def test_processor_save_load_roundtrip(tmp_path):
    from transformers import Sam2ImageProcessorFast, Sam2Processor

    processor = Sam2Processor(Sam2ImageProcessorFast())
    processor.save_pretrained(tmp_path)

    loaded = AutoProcessor.from_pretrained(tmp_path)
    assert isinstance(loaded, Sam2Processor)
    assert isinstance(loaded.image_processor, Sam2ImageProcessorFast)

    image_processor = AutoImageProcessor.from_pretrained(tmp_path)
    assert isinstance(image_processor, Sam2ImageProcessorFast)


# ---------------------------------------------------------------------------
# Model save/load contract
# ---------------------------------------------------------------------------

def test_model_save_load_forward_parity(tmp_path):
    from transformers import Sam2Config, Sam2Model

    model = Sam2Model(config=_tiny_sam2_config())
    model.eval()
    pixel_values = _pixel_values(batch_size=1)
    with torch.no_grad():
        reference = model(pixel_values)

    model.save_pretrained(tmp_path)
    loaded = Sam2Model.from_pretrained(tmp_path)
    loaded.eval()
    assert isinstance(loaded.config, Sam2Config)
    assert loaded.config.model_type == "sam2"

    with torch.no_grad():
        reloaded = loaded(pixel_values)
    assert torch.allclose(reference.iou_scores, reloaded.iou_scores, atol=1e-5)
    assert torch.allclose(reference.pred_masks, reloaded.pred_masks, atol=1e-5)


# ---------------------------------------------------------------------------
# End-to-end: processor -> model -> post-process back to original size
# ---------------------------------------------------------------------------

def test_end_to_end_point_prompt_segmentation():
    from transformers import Sam2ImageProcessorFast, Sam2Model, Sam2Processor

    # The image processor input size must match the (tiny) model config, so
    # both are set to 128x128 here; the default-size behavior is covered by
    # the processor test above.
    processor = Sam2Processor(
        Sam2ImageProcessorFast(size={"height": 128, "width": 128})
    )
    model = Sam2Model(config=_tiny_sam2_config())
    model.eval()

    image = _rgb_image(width=640, height=480)
    inputs = processor(
        images=image,
        input_points=[[[[100.0, 200.0]]]],
        input_labels=[[[1]]],
        return_tensors="pt",
    )
    with torch.no_grad():
        outputs = model(
            pixel_values=inputs["pixel_values"],
            input_points=inputs["input_points"],
            input_labels=inputs["input_labels"],
        )

    masks_per_image = processor.post_process_masks(
        outputs.pred_masks, inputs["original_sizes"]
    )
    assert len(masks_per_image) == 1
    masks = masks_per_image[0]
    # Masks are upscaled back to the original (height, width); the default
    # post-processing binarizes them.
    assert tuple(masks.shape[-2:]) == (480, 640)
    assert masks.dtype == torch.bool
