# Parakh optional Qwen model pack

The model pack is a separate, versioned offline payload. Its absence is not a
screening failure: deterministic document checks continue normally, while the
Visual Q&A operation reports that no model pack is installed.

## Exact supported payload

`pins.json` is authoritative. Version `1.0.0` contains:

- Qwen `Qwen/Qwen3-VL-4B-Instruct-GGUF` at revision
  `1cd86afb9a95c410a6038ab3b40d8b578c892266`.
- `Qwen3VL-4B-Instruct-Q4_K_M.gguf` (Q4_K_M) and
  `mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf` (Q8_0), with tokenizer and model
  configuration embedded in the pinned GGUF files.
- llama.cpp `b10298` / commit `15586e2`, with separate Windows x64 Vulkan and
  CPU runtimes. Vulkan is preferred; CPU is the required fallback.

Every source artifact has an expected SHA-256 in `pins.json`. The generated
`model-pack.json` also hashes every installed file. The installer validates all
hashes before placing files in their versioned location.

## Licensing

The official Qwen model repository declares Apache-2.0 and the official
llama.cpp repository uses MIT. The pack includes both complete license texts
and `THIRD-PARTY-NOTICES.md`. Do not replace the model with a community
quantization without a new license review, new source revision, and new hashes.

Primary sources:

- https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct-GGUF
- https://github.com/ggml-org/llama.cpp/blob/master/LICENSE
- https://github.com/ggml-org/llama.cpp/releases/tag/b10298

Apache-2.0 permits redistribution when its conditions are followed, including
providing the license, preserving applicable notices, and marking modified
files. The selected model files are redistributed unmodified. The Qwen name is
used only to describe origin; Apache-2.0 does not grant trademark rights.

## Build administrator procedure

Download the four files named in `pins.json` from their official sources. Then:

```powershell
powershell -File build/model-pack/make-model-pack.ps1 `
  -PythonExe C:\Python313\python.exe
```

By default the script reads the already-verified model files from
`backend/models/qwen3-vl-4b-q4` and runtime archives from
`backend/runtime/llama-b10298/archives`. All four paths can still be overridden
with `-ModelDir`, `-ModelFile`, `-VisionProjectorFile`, `-LlamaCpuZip`, and
`-LlamaVulkanZip`. The selected build Python must be able to import the backend
dependencies because the builder calls the production validator before it can
report success.

The script refuses unpinned bytes and writes:

`release/model-pack/Parakh-Qwen3-VL-4B-Instruct-Q4_K_M-1.0.0.zip`

It prints the final archive SHA-256. Record that digest in the release notes.
The large external artifacts are intentionally neither committed nor included
in the main Parakh installer.

For an internal command-line installation test:

```powershell
# Elevated PowerShell, machine-wide:
powershell -File build/model-pack/install-model-pack.ps1 `
  -PackArchive release/model-pack/Parakh-Qwen3-VL-4B-Instruct-Q4_K_M-1.0.0.zip

# Or a per-user test without elevation:
powershell -File build/model-pack/install-model-pack.ps1 `
  -PackArchive release/model-pack/Parakh-Qwen3-VL-4B-Instruct-Q4_K_M-1.0.0.zip `
  -Scope CurrentUser
```

Production distribution should wrap the same verified payload in the signed
model-pack installer so an employee only double-clicks Setup. The stable
machine-wide destination is:

`%PROGRAMDATA%\Parakh\ModelPacks\parakh-qwen3-vl-4b-instruct-q4-k-m\1.0.0`

The per-user fallback is the same tree under `%LOCALAPPDATA%`.

## Desktop launcher contract

The Electron launcher should use the manifest rather than hard-coded model
paths. It must:

1. Check `PARAKH_VLM_MODEL_PACK`, then the machine-wide and per-user locations.
2. If the pack is absent, start no model process and leave VLM environment
   variables unset. Normal screening must still start.
3. If the pack is invalid, start no model process and expose the manifest error
   through `/api/v1/vlm/status`; never describe it as merely disabled.
4. Allocate a free loopback port. Start the Vulkan runtime first with the fixed
   manifest paths and `--gpu-layers 99`. If it exits or fails readiness, stop it
   and retry the CPU runtime with `--gpu-layers 0`.
5. Use these common arguments:

```text
--model <model GGUF>
--mmproj <vision projector GGUF>
--alias Qwen3-VL-4B-Instruct
--ctx-size 8192
--parallel 1
--jinja
--no-webui
--host 127.0.0.1
--port <allocated port>
```

6. Pass these variables to the FastAPI process only after the server is ready:

```text
VLM_ENABLED=1
VLM_BASE_URL=http://127.0.0.1:<allocated port>/v1
VLM_MODEL=Qwen3-VL-4B-Instruct
PARAKH_VLM_MODEL_PACK=<absolute pack directory>
PARAKH_VLM_RUNTIME_BACKEND=vulkan|cpu
PARAKH_VLM_RUNTIME_PATH=<absolute llama-server.exe>
PARAKH_VLM_DEVICE=GPU (Vulkan)|CPU
```

`backend.vlm_model_pack.launcher_contract()` provides the executable paths,
common arguments, and base environment in machine-readable form.

## Verification

After installing the pack and launching Parakh:

- `GET /api/v1/vlm/status` must report `source: model_pack`, the exact pack,
  model and runtime versions, selected backend/device/path, and `ready: true`.
- `GET /api/v1/diagnostics/dependencies` must keep deterministic `complete`
  independent of pack presence and report the pack under
  `optional.vlm_model_pack`.
- Ask a golden document question that requires looking at a page image, confirm
  the citation is to a supplied page, then repeat after forcing CPU fallback.
- Remove the pack and confirm screening still completes while Visual Q&A clearly
  reports that the model is unavailable.
