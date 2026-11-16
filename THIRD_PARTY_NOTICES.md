# Third-party notices

WuwaTappo Chest Helper's own source is MIT-licensed; see `LICENSE`. The
following separately licensed components are distributed with the Windows
application. Their licenses do not change the license of this project's own
source. Original copyright and license texts are preserved in `licenses/` and,
where supplied by the packaging hooks, `_internal/`.

## MouseClickTool 3.3 (helper 1.10.0)

`bundled_tools/MouseClickTool.exe` is the original, unmodified executable
provided by the user. The Windows package carries the same file at
`_internal/bundled_tools/MouseClickTool.exe`. Its file copyright is
`Copyright (C) 2026 lalaki.cn`; rights remain with its original author.
This executable is not relicensed under this project's MIT license.

SHA256: `e4b07ea0a1cebc18e825c0ad3bc9bfdadbcb1b5b30e26cf05e00f5409da6bf60`.
MouseClickTool requires .NET Framework 4.6.2 or later.

## WuwaTappo UI artwork (helper 1.9.0)

The three PNG images embedded in `chest_artwork.py` (reward bubble, chest base
and chest lid) come from the WuwaTappo 1.0.4 UI. No WuwaTappo program source
code is included. Rights to these UI images remain with their original authors;
the images are not covered by this project's MIT license.

## MaaFramework 5.16.0

Background window capture uses the unmodified Python bindings and dynamically
loaded DLLs from the official **MaaFw 5.16.0 Windows x64 wheel**, licensed under
LGPL version 3. This application uses MaaFramework's Win32 FramePool capture API;
the input messaging is implemented in this project's own source. MaaNTE code is
not included.

- Source: <https://github.com/MaaXYZ/MaaFramework/tree/v5.16.0>
- Source archive: <https://github.com/MaaXYZ/MaaFramework/archive/refs/tags/v5.16.0.tar.gz>
- Official binaries: <https://pypi.org/project/MaaFw/5.16.0/>
- License: `licenses/MaaFramework-LGPL-3.0.md` and `licenses/GPL-3.0.txt`

The source archive omits Git submodule contents. Obtain the complete upstream
source and build instructions with:

```sh
git clone --recurse-submodules --branch v5.16.0 https://github.com/MaaXYZ/MaaFramework.git
```

The tag records the exact submodule revisions. In particular, MaaUtils is commit
`55be777e326db9419f45b4082e2bf2a1ed10e697`, and XDeviceBridge is commit
`37cf0839320373aaeff15b9a33b0e00815538f82`. MaaFramework's dependency build and
patch sources for this version are **MaaDeps v3.0.2**:
<https://github.com/MaaXYZ/MaaDeps/tree/v3.0.2>.

The replaceable MaaFramework DLLs are in `_internal/maa/bin/`. You may replace
them with ABI-compatible rebuilt libraries, retaining their required dependent
DLLs. No checksum or signature enforcement prevents replacement. To replace the
Python bindings as well, install a compatible local build of MaaFw in a Python
environment and rebuild this application using the instructions in `README.md`.
Reverse engineering of this application for debugging modifications to the
LGPL-covered library is permitted. No additional restriction is imposed on
rights granted by the third-party licenses.

## Native dependencies supplied by MaaFramework

The application ships the official wheel's native runtime intact, including
components used by MaaFramework's other APIs. License texts from the matching
official `MaaDeps-x64-windows.tar.xz` v3.0.2 distribution are preserved verbatim
under `licenses/MaaDeps-v3.0.2/`. This contains the notices for both direct and
transitive dependencies; build-tool notices are retained as supplied upstream.
The source recipes, pinned versions and local patches are available in the
MaaDeps source link above. Important components include:

| Component | License / supplied notice |
| --- | --- |
| OpenCV | Apache-2.0, `MaaDeps-v3.0.2/opencv4/copyright` |
| FastDeploy | Apache-2.0, `MaaDeps-v3.0.2/maa-fastdeploy/copyright` |
| ONNX Runtime | MIT, `MaaDeps-v3.0.2/maa-onnxruntime/copyright` |
| Boost libraries | Boost Software License, `MaaDeps-v3.0.2/boost-*/copyright` |
| ZeroMQ | MPL-2.0, `MaaDeps-v3.0.2/zeromq/copyright` |
| cppzmq | MIT, `MaaDeps-v3.0.2/cppzmq/copyright` |
| Dawn / WebGPU | BSD-style and component notices, `MaaDeps-v3.0.2/dawn/copyright` |
| DirectX Shader Compiler | University of Illinois/NCSA and component notices, `MaaDeps-v3.0.2/directx-dxc/copyright` |
| DirectML | Microsoft Software License Terms, `MaaDeps-v3.0.2/directml-bin/copyright` |
| ViGEmClient / vgamepad | MIT, `ViGEmClient-LICENSE` and `MaaDeps-v3.0.2/vgamepad-bin/copyright` |
| XDeviceBridge | LGPL-3.0, `XDeviceBridge-LGPL-3.0.md` and `GPL-3.0.txt` |
| MaaUtils | LGPL-3.0, `MaaUtils-LICENSE.md` and `GPL-3.0.txt` |
| QuickJS-NG | MIT, `QuickJS-NG-LICENSE` |
| meojson | MIT, `meojson-LICENSE` |
| gzip-hpp | BSD-2-Clause, `gzip-hpp-LICENSE.md` |

DirectML is a separate Microsoft redistributable, not an LGPL component. It is
included unmodified as part of MaaFramework's Windows runtime. See its supplied
Microsoft terms. This helper does not configure or run a neural-network model.

Additional upstream source repositories:

- <https://github.com/MaaXYZ/MaaUtils>
- <https://github.com/scrctl/XDeviceBridge>
- <https://github.com/quickjs-ng/quickjs>
- <https://github.com/MistEO/meojson>
- <https://github.com/mapbox/gzip-hpp>

## Python packages and runtime

- **StrEnum 0.4.15** — MIT; `licenses/StrEnum-0.4.15-LICENSE`;
  <https://github.com/irgeek/StrEnum/tree/0.4.15>.
- **NumPy** — BSD-3-Clause and bundled component notices; exact installed
  version and complete license are in `_internal/numpy-*.dist-info/`;
  <https://numpy.org/>.
- **Pillow** — HPND and bundled component notices, included in
  `_internal/pillow-*.dist-info/licenses/LICENSE`;
  <https://python-pillow.org/>.
- **CPython** — Python Software Foundation license,
  `licenses/runtime/CPython-LICENSE.txt`;
  <https://www.python.org/>.
- **Tcl/Tk** — BSD-style license; `_internal/_tcl_data/license.terms` and
  `_internal/_tk_data/license.terms`; <https://www.tcl.tk/>.
- **PyInstaller bootloader** — GPL-2.0-or-later with the PyInstaller exception
  permitting distribution of bundled applications under their own license;
  `licenses/runtime/PyInstaller-COPYING.txt`;
  <https://pyinstaller.org/en/stable/license.html>.

No endorsement by the authors of these components or by WuwaTappo is implied.
