# Third-party notices

SceneTrace is created by Isaac Oselukwue and released under the MIT License (see LICENSE). It uses the components below, which keep their own licences.
Versions are the locked versions in `uv.lock` and `client/package-lock.json`.

## Services

- Meta Model API: SAM 3.1, Muse Spark 1.3 and Muse Wonder 1.0, used under Meta's terms for the API. No model weights are distributed here.

## Python runtime dependencies

- fastapi 0.162.0: MIT
- starlette 1.10.0: BSD-3-Clause
- pydantic 2.13.5: MIT
- httpx 0.28.1: BSD-3-Clause
- uvicorn 0.54.0: BSD-3-Clause
- numpy 2.5.4: BSD-3-Clause
- pillow 13.0.0: MIT-CMU (HPND)
- jsonschema 4.26.0: MIT
- python-multipart 0.0.32: Apache-2.0
- meta-sam-parser 0.1.0: SAM License (Meta Platforms, Inc.)

`meta-sam-parser` is distributed by Meta Platforms, Inc. under the SAM License. It is installed from PyPI, not redistributed in this repository.

## Client dependencies

- react 19.3.1: MIT
- react-dom 19.3.1: MIT
- @fontsource-variable/inter 5.3.0: the Inter typeface by The Inter Project Authors, SIL Open Font License 1.1
- @fontsource-variable/jetbrains-mono 5.3.0: the JetBrains Mono typeface by The JetBrains Mono Project Authors, SIL Open Font License 1.1

The two typefaces are bundled into the built client as WOFF2 files; their licence texts ship in the npm packages.

Development tools: @playwright/test 1.64.1, @types/node 22.20.5, @types/react 19.3.0, @types/react-dom 19.3.0, @vitejs/plugin-react 6.1.2, jsdom 30.1.3, openapi-typescript 7.13.0, typescript 5.9.3, vite 8.3.5, vitest 5.0.5.

## Tools

- FFmpeg and FFprobe (LGPL/GPL depending on the build) are invoked as external programs and are not bundled in this repository. The Docker image installs the Debian `ffmpeg` package.
