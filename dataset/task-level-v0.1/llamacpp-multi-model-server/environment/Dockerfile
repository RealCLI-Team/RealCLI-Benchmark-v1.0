# llama.cpp multi-model server (router) task environment.
# Base snapshot: ggml-org/llama.cpp @ 773340973473952df872b179f97b5a484a0b063d
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        build-essential \
        ca-certificates \
        cmake \
        curl \
        libcurl4-openssl-dev \
        patch \
        procps \
        python3 \
        python3-numpy \
        python3-pytest \
        python3-yaml \
    && rm -rf /var/lib/apt/lists/*

COPY repository/ /app/
WORKDIR /app

# Bake tiny random-weight GGUF models into the image so that router-mode
# behavior can be exercised fully offline (no Hugging Face downloads).
# The generator uses the gguf python package shipped in the repository.
RUN <<'EOF'
set -e
mkdir -p /opt/models/tiny-c
cat > /tmp/gen_tiny_gguf.py <<'PYEOF'
#!/usr/bin/env python3
"""Generate a tiny random-weight llama-architecture GGUF for offline testing."""
import sys

import numpy as np

sys.path.insert(0, "/app/gguf-py")

from gguf import GGUFWriter  # noqa: E402

OUT = sys.argv[1]
NAME = sys.argv[2]

N_EMBD = 64
N_HEAD = 2
N_HEAD_KV = 2
N_LAYER = 2
N_FF = 128
N_CTX = 256

SPECIALS = ["<unk>", "<s>", "</s>"]
CHARS = [chr(c) for c in range(0x20, 0x7F)] + ["\n", "\t"]
TOKENS = SPECIALS + CHARS
N_VOCAB = len(TOKENS)

TOKEN_TYPE_CONTROL = 3
TOKEN_TYPE_NORMAL = 1
TOKEN_TYPES = [TOKEN_TYPE_CONTROL] * len(SPECIALS) + [TOKEN_TYPE_NORMAL] * len(CHARS)

CHAT_TEMPLATE = (
    "{% for message in messages %}"
    "<|im_start|>{{ message['role'] }}\n{{ message['content'] }}<|im_end|>\n"
    "{% endfor %}"
    "<|im_start|>assistant\n"
)

rng = np.random.default_rng(20260810)


def rand(*shape):
    return (rng.standard_normal(shape) * 0.02).astype(np.float32)


writer = GGUFWriter(OUT, "llama")
writer.add_name(NAME)
writer.add_block_count(N_LAYER)
writer.add_context_length(N_CTX)
writer.add_embedding_length(N_EMBD)
writer.add_feed_forward_length(N_FF)
writer.add_head_count(N_HEAD)
writer.add_head_count_kv(N_HEAD_KV)
writer.add_layer_norm_rms_eps(1e-5)
writer.add_rope_freq_base(10000.0)

writer.add_tokenizer_model("gpt2")
writer.add_tokenizer_pre("default")
writer.add_token_list(TOKENS)
writer.add_token_scores([0.0] * N_VOCAB)
writer.add_token_types(TOKEN_TYPES)
writer.add_token_merges(["a b"])
writer.add_bos_token_id(1)
writer.add_eos_token_id(2)
writer.add_unk_token_id(0)
writer.add_add_bos_token(False)
writer.add_add_eos_token(False)
writer.add_string("tokenizer.chat_template", CHAT_TEMPLATE)

writer.add_tensor("token_embd.weight", rand(N_VOCAB, N_EMBD))
for i in range(N_LAYER):
    p = "blk.%d." % i
    writer.add_tensor(p + "attn_norm.weight", rand(N_EMBD))
    writer.add_tensor(p + "attn_q.weight", rand(N_EMBD, N_EMBD))
    writer.add_tensor(p + "attn_k.weight", rand(N_EMBD, N_EMBD))
    writer.add_tensor(p + "attn_v.weight", rand(N_EMBD, N_EMBD))
    writer.add_tensor(p + "attn_output.weight", rand(N_EMBD, N_EMBD))
    writer.add_tensor(p + "ffn_norm.weight", rand(N_EMBD))
    writer.add_tensor(p + "ffn_gate.weight", rand(N_FF, N_EMBD))
    writer.add_tensor(p + "ffn_down.weight", rand(N_EMBD, N_FF))
    writer.add_tensor(p + "ffn_up.weight", rand(N_FF, N_EMBD))
writer.add_tensor("output_norm.weight", rand(N_EMBD))
writer.add_tensor("output.weight", rand(N_VOCAB, N_EMBD))

writer.write_header_to_file()
writer.write_kv_data_to_file()
writer.write_tensors_to_file()
writer.close()
print("wrote", OUT)
PYEOF
python3 /tmp/gen_tiny_gguf.py /opt/models/tiny-a.gguf tiny-a
python3 /tmp/gen_tiny_gguf.py /opt/models/tiny-b.gguf tiny-b
python3 /tmp/gen_tiny_gguf.py /opt/models/tiny-c/tiny-c.gguf tiny-c
rm -f /tmp/gen_tiny_gguf.py
EOF

# Warm the build: configure and compile llama-server so that, after the
# solution is applied, only an incremental rebuild is needed. CPU-only,
# portable codegen (GGML_NATIVE=OFF), no examples/tests to keep it fast.
RUN cmake -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DGGML_NATIVE=OFF \
        -DLLAMA_BUILD_EXAMPLES=OFF \
        -DLLAMA_BUILD_TESTS=OFF \
        -DLLAMA_CURL=ON \
    && cmake --build build --target llama-server -j"$(nproc)"

CMD ["bash"]
