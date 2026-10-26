# Verifier image. Mirrors environment/Dockerfile so the separate verifier
# container has the same runtime as the agent image. At verify time /app and
# /workspace are re-materialized from the agent artifacts; this directory is
# also baked into /tests and re-uploaded before verification.
# scipy-stats-normal-infrastructure
# scipy (1.15.0.dev0 快照) 全量源码编译环境，含增量构建缓存
FROM python:3.11-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1

# gfortran + openblas：scipy 构建所需的 Fortran 编译器与 BLAS/LAPACK
# patch：运行期对 /app 应用源码改动时需要
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      gfortran \
      git \
      libopenblas-dev \
      pkg-config \
      patch \
 && rm -rf /var/lib/apt/lists/*

# 构建/测试依赖，按 pyproject.toml 的下限约束固定到快照年代（2024-10）
# 实测兼容的版本；pythran 在构建选项中关闭，故不安装
RUN pip install --no-cache-dir \
      "numpy==2.0.2" \
      "Cython==3.0.11" \
      "meson==1.5.2" \
      "ninja==1.11.1.1" \
      "pybind11==2.13.6" \
      "pytest==8.3.3"

# Source repository: https://github.com/scipy/scipy.git @ 8bfaef2c3351854de5898127d662e1357f9364ad
# Cloned at build time (this dataset ships no local repository/ directory).
RUN git init -q /app \
    && git -C /app remote add origin https://github.com/scipy/scipy.git \
    && git -C /app fetch -q --depth 1 origin 8bfaef2c3351854de5898127d662e1357f9364ad \
    && git -C /app checkout -q FETCH_HEAD \
    && rm -rf /app/.git
WORKDIR /app

# 仓库快照（GitHub 归档）不含 git submodule 内容，而 scipy 编译依赖它们。
# 按上游 source_commit 钉住的 submodule SHA 拉取源码补齐（只补空目录，
# 不改动快照内任何已有文件）。
RUN set -e; \
    fetch() { \
      mkdir -p "$3"; \
      curl -fsSL --retry 3 "https://codeload.github.com/$1/tar.gz/$2" \
        | tar -xz --strip-components=1 -C "$3"; \
    }; \
    fetch boostorg/math            54e5acf7faeab1400031d84bbfb2c46e81d82442 scipy/_lib/boost_math; \
    fetch scipy/highs              4a122958a82e67e725d08153e099efe4dad099a2 scipy/_lib/highs; \
    fetch scipy/unuran             21810c856880bb2f1b3071c8ac4aaf634983b338 scipy/_lib/unuran; \
    fetch data-apis/array-api-compat f905d8c299e626aa2902f21b75df3a42507a8436 scipy/_lib/array_api_compat; \
    fetch scipy/pocketfft          9367142748fcc9696a1c9e5a99b76ed9897c9daa scipy/_lib/pocketfft; \
    fetch cobyqa/cobyqa            55c8e5a5d29ba5c873d87ad27bd9cffee94ae3df scipy/_lib/cobyqa; \
    fetch scipy/PROPACK            300f803c5ac3372ceb65ba446c71c90f128814b1 scipy/sparse/linalg/_propack/PROPACK

# 完整编译一次 scipy 并把 build/ 目录与安装产物烤进镜像（预热）。
# 之后 agent 修改源码只需 ninja 增量重编译/重装即可生效。
# use-pythran=false：跳过 pythran 编译以压缩镜像构建时长，对 stats 无影响。
# buildtype=debug：-O0 编译比默认 debugoptimized 快近一倍，给 1800s 的
# 镜像构建时限留出余量；数值结果由算法决定，与优化级别无关。
RUN meson setup build --prefix=/usr/local -Duse-pythran=false -Dbuildtype=debug \
 && ninja -C build -j4 \
 && meson install -C build \
 && cd /tmp \
 && python -c "import scipy; print('installed scipy', scipy.__version__)"

# Bake the verifier scripts into the image (harbor re-uploads them at verify time).
COPY . /tests
