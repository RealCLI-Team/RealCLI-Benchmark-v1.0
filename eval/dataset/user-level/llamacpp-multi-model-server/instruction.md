# Make llama-server support dynamic multi-model loading and request routing

We use llama.cpp's `llama-server` to provide an OpenAI-compatible API for internal services. On a single machine we now need to serve several small models at once, and the current single-model mode (one model specified with `-m` at startup) is very inconvenient: switching models means restarting the process, and there is no way to load/unload models on demand. We want a **multi-model routing** capability added to `llama-server`: the main process acts as a router that loads no model itself, and forwards traffic to model instances spun up on demand, based on the model name in each request.

The repository is at `/app` (the llama.cpp source code). Implement this capability on top of the existing server code. The behavioral contract below is the public API that callers already depend on — follow it strictly; the process model, internal state management, and other implementation details are up to you. The existing single-model mode (model specified at startup) must not be broken.

## Router mode and model sources

- Starting `llama-server` **without specifying any model** (no `-m`/`-hf`, etc.) enters router mode: the main process loads no model itself and only manages and forwards requests.
- Add a startup flag `--models-dir PATH`: scan GGUF files under this directory as servable models. **The model name (i.e. the model id in the API) is the filename with the `.gguf` suffix stripped**; if a GGUF is placed inside a subdirectory (the organization for multi-file models, e.g. with an mmproj projection file or a multi-shard model), the entire subdirectory counts as one model, named after the subdirectory. If the directory does not exist or is not a directory, report an error and exit.
- Add `--models-max N`: the maximum number of models loaded at the same time (default 4, `0` means unlimited). When loading a new model would exceed the limit, automatically unload the **least recently used** loaded model.
- Add `--models-allow-extra-args`: allow callers to attach custom startup arguments to a model instance in the load endpoint (off by default; when off, that field must be ignored).
- Inference-related parameters given to the router at startup (e.g. `-ctx`, `-n`) must be passed through as defaults to every model instance spun up later.

## Model management endpoints

The following endpoints are handled by the router process itself:

- `GET /models` and `GET /v1/models`: list all known models in the OpenAI-compatible `{"object": "list", "data": [...]}` structure. Besides the OAI-compatible fields, each model object must include:
  - `id`: the model name;
  - `in_cache`: boolean; models coming from the local `--models-dir` are `false`;
  - `path`: path to the model file;
  - `status`: an object whose `value` is one of `unloaded` / `loading` / `loaded`; when `loading`/`loaded` it carries `args` (the array of startup arguments actually used by that instance); after a failed load it returns to `unloaded` and carries `failed: true` and `exit_code`.
- `POST /models/load`, request body `{"model": "<模型名>"}`: load a model (an `extra_args` array may be attached, effective only when `--models-allow-extra-args` is enabled). On success return HTTP 200 with `{"success": true}`; model name does not exist → **404**; model already loaded → **400**. The endpoint may return as soon as the model loading process has started; callers watch the status converge to `loaded` via `/v1/models`.
- `POST /models/unload`, request body `{"model": "<模型名>"}`: unload a model. On success return 200 with `{"success": true}`; model name does not exist → 400; model is not currently in the loaded state → 400.

## Request routing

- For **POST** inference endpoints such as `/v1/chat/completions`, `/v1/completions`, `/infill`, route to the corresponding model instance based on the `"model"` field in the request body JSON; a request body without `"model"` → **400**; a model name that does not exist → **400**.
- For **GET** endpoints such as `/props`, `/metrics`, route based on the `model` parameter in the URL query (URL-encoded).
- **On-demand autoload is on by default**: when a request is routed to a model that is not yet loaded, load it first and then forward the request — from the caller's perspective it is just an ordinary successful inference response. Two ways to disable it: pass `--no-models-autoload` at startup to disable it globally; or pass `?autoload=false` on a single request to disable it temporarily. When disabled, requesting an unloaded model → **400** (report that the model is not loaded).
- `GET /props` without a `model` parameter returns information about the router itself, with its `role` field set to `"router"`.
- Every spawned model instance must start "as itself being that model", so that the `model_alias` field in its `/props` equals the corresponding model name (i.e. the filename with the `.gguf` suffix stripped / the subdirectory name). That way, in a `GET /props` response routed to that instance with the `model` parameter, `model_alias` identifies which model instance answered.
- Routing and forwarding must be transparent to callers: the response is the model instance's own response (e.g. the `choices` of completions and the `message` structure of chat are passed through as usual).

## Self-test environment

The image ships with two tiny randomly-initialized GGUF models (only a few hundred KB each, for functional self-testing only — do not use them to evaluate generation quality):

- `/opt/models/tiny-a.gguf`, `/opt/models/tiny-b.gguf` (single-file models, model names `tiny-a` and `tiny-b` respectively)
- `/opt/models/tiny-c/tiny-c.gguf` (a model organized as a subdirectory, model name `tiny-c`)

## How to verify

Your changes must pass a full build: under `/app`, configure with cmake and build `llama-server` (the `build` directory already contains warmed-up build artifacts, so an incremental build is enough). Actually run a verification pass: start the router with `--models-dir /opt/models`, and use curl to walk through the full flow of "list → load → inference routing (including autoload) → unload → limit-based eviction". Confirm that the external behavior matches the contract above before wrapping up.

Process management note: when you need to stop or restart the router or model instances during verification, do NOT use `pkill -f` / `killall` with a broad name pattern — the evaluation harness runs your agent CLI with the task text (which contains the string `llama-server`) in its own command line, so a pattern like `pkill -f llama-server` would match and kill your own agent process. Instead, record the PID at startup (e.g. `$!` or a pid file) and kill by PID, or use an exact executable-name match such as `pkill -x llama-server`.
