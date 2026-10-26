# Natively support SAM2 (Segment Anything 2) in transformers

Our product line relies heavily on transformers for vision segmentation. The repo already has the first-generation SAM (`SamModel`/`SamProcessor`, the `mask-generation` pipeline set), but Meta's official SAM2 has always been a separate repository, forcing us to maintain two sets of dependencies and two calling conventions — painful. We now want to bring SAM2 directly into the transformers mainline, going through the same unified Config / Model / Processor / Auto machinery as the existing SAM. The repo is at `/app` (transformers source); please make this happen.

You need to learn the changes of SAM2 relative to the first generation yourself: the image encoder is replaced by a Hiera hierarchical backbone (multi-scale FPN features), and the model adds memory attention and a memory encoder for streaming video tracking; inference splits into two major scenarios — point/box prompted segmentation on single images, and object tracking on video. The official weights come in the SAM 2.1 hiera-tiny/small/base+/large series, and the default configuration should align with the hiera-tiny tier's architecture. Use "the same user experience as the existing SAM model in the repo" as the baseline for overall behavior: the same calling patterns should carry over to SAM2 seamlessly.

## Capabilities to deliver

**Image segmentation model (SAM2)**:

- Add a complete new model implementation, directly importable from the top-level package: `Sam2Config`, `Sam2Model`, `Sam2Processor`, `Sam2ImageProcessorFast`, plus sub-configs and sub-models `Sam2VisionConfig`, `Sam2VisionModel`, `Sam2HieraDetConfig` (hierarchical backbone config), `Sam2PromptEncoderConfig` (prompt encoder config), `Sam2MaskDecoderConfig` (mask decoder config). The composite config `Sam2Config` (`model_type = "sam2"`) constructed with no arguments must be a complete, usable architecture (aligned with SAM 2.1 hiera-tiny by default), and every sub-config must be independently constructible and independently serializable; the `model_type` of the vision encoder config `Sam2VisionConfig` is `"sam2_vision_model"`.
- Align the forward interface with the existing SAM: input `pixel_values`, optional `input_points`/`input_labels` (point coordinates organized in four dimensions: image × object × points-per-object × (x, y); labels: 1 = foreground point, 0 = background point, negative values = ignored padding points), `input_boxes`, `multimask_output`, etc. When no prompt is given, output 3 candidate masks in multi-mask mode: the returned object must contain `pred_masks` (shape batch × objects × 3 × height × width), `iou_scores` (batch × objects × 3), and `object_score_logits` (batch × objects × 1).
- The vision encoder sub-model `Sam2VisionModel` is usable standalone: it takes `pixel_values` and returns `last_hidden_state` (batch × channels × height × width, a 4-D tensor) and `fpn_hidden_states` (a tuple of multi-scale FPN features, each element a 4-D tensor with the same shape semantics).

The constructor parameter names of the config classes must be exposed strictly per the following contract (the verification code will construct configs using these parameter names with small-size values; every parameter must have a reasonable default so that each config class can also be constructed with no arguments):

- `Sam2HieraDetConfig`: `hidden_size`, `num_channels`, `image_size`, `patch_stride`, `patch_kernel_size`, `patch_padding`, `blocks_per_stage` (list of block counts per stage), `embed_dim_per_stage`, `num_attention_heads_per_stage`.
- `Sam2VisionConfig`: `backbone_config` (a `Sam2HieraDetConfig`), `backbone_channel_list`, `backbone_feature_sizes` (a list of per-level feature map sizes, each entry an `[H, W]` pair), `fpn_hidden_size`.
- `Sam2PromptEncoderConfig`: `image_size`, `patch_size`, `mask_input_channels`, `hidden_size`, `num_point_embeddings`.
- `Sam2MaskDecoderConfig`: `hidden_size`, `mlp_dim`, `num_hidden_layers`, `num_attention_heads`, `attention_downsample_rate`, `num_multimask_outputs`, `iou_head_depth`, `iou_head_hidden_dim`.
- `Sam2Config`: the three sub-config fields `vision_config`, `prompt_encoder_config`, `mask_decoder_config`, plus the memory-related fields `memory_attention_hidden_size`, `memory_encoder_hidden_size`, `memory_attention_num_layers`, `memory_attention_feed_forward_hidden_size`, `mask_downsampler_embed_dim`, `memory_fuser_embed_dim`, `image_size`.
- `Sam2VideoConfig` accepts the same fields as `Sam2Config`; sub-configs must be accepted either as config instances or as dicts.

**Image processing and Processor**:

- `Sam2ImageProcessorFast` (a torchvision-based fast image processor): by default rescales/pads input images uniformly to 1024×1024 (consistent with official SAM2), with the `size` argument following the usual transformers convention (a dict with `height`/`width` entries), and supports preprocessing of point/box prompts — when different images have different numbers of objects or points, automatically pads with filler points (padding point labels use -10), and records the `original_sizes` of each image.
- `Sam2Processor` wraps the image processor; a single call `processor(images=..., input_points=..., input_labels=..., return_tensors="pt")` returns both `pixel_values` and the processed points/labels; `post_process_masks(masks, original_sizes)` returns a per-image list by batch, restoring the model's low-resolution masks to the original image size, binarized by default (returning bool tensors).

**Auto-class registration** (the same treatment as every official model in the repo):

- `AutoConfig`/`AutoModel` must resolve to the corresponding config and model for the `"sam2"` model type; `AutoModelForMaskGeneration` must also support it, so the `mask-generation` pipeline machinery works directly.
- `AutoProcessor`/`AutoImageProcessor` likewise resolve to `Sam2Processor`/`Sam2ImageProcessorFast` for `"sam2"`; a directory produced by `Sam2Processor.save_pretrained` must be recognized back as-is by `AutoProcessor.from_pretrained`.

**Video tracking version (SAM2 Video)**:

- The video version extends the image version, adding `Sam2VideoConfig` (model type `"sam2_video"`), `Sam2VideoModel`, `Sam2VideoProcessor`, and the companion video processor, with AutoConfig/AutoModel/AutoProcessor registration likewise completed. On top of image segmentation, the video model carries a memory bank and memory attention, and uses an inference session to hold cross-frame state for object tracking.

**save/load contract**: both models and configs must round-trip through `save_pretrained` → `from_pretrained`; the loaded model's outputs on the same inputs must match the original model's.

## Constraints and verification

- Don't break existing repo functionality: the imports and Auto registration of existing models like SAM must not be broken.
- Develop fully offline; do not download any weights from the internet; for verification use small randomly-initialized configs (shrink the layer counts, hidden dimensions, and feature map sizes).
- Actually run the verification: after `pip install -e .`, use Python to — import the new classes, construct a small-config model and run a forward pass, create an image with PIL and run point prompts through the full processor→model→post_process chain, then do a round of save/load. Wrap up only after confirming the behavior matches the conventions above.
