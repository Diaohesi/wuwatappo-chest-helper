"""Build a Windows GUI with adjacent dependencies; no temporary extraction."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parent
EXE_NAME = "WuwaTappoChestHelper.exe"


def app_version() -> str:
    source = ast.parse((ROOT / "auto_chest.py").read_text(encoding="utf-8"))
    for node in source.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "APP_VERSION" for target in node.targets
        ):
            version = ast.literal_eval(node.value)
            if isinstance(version, str) and re.fullmatch(r"\d+(?:\.\d+){1,3}", version):
                return version
    raise ValueError("auto_chest.py must define a numeric APP_VERSION")


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_version_info(path: Path, version: str) -> None:
    parts = [int(part) for part in version.split(".")]
    numeric = tuple(parts + [0] * (4-len(parts)))
    text = f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(filevers={numeric!r}, prodvers={numeric!r}, mask=0x3f,
                   flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('080404b0', [
    StringStruct('FileDescription', 'WuwaTappo 宝箱自动领取'),
    StringStruct('FileVersion', {version!r}),
    StringStruct('InternalName', 'WuwaTappoChestHelper'),
    StringStruct('OriginalFilename', {EXE_NAME!r}),
    StringStruct('ProductName', 'WuwaTappo 宝箱自动领取'),
    StringStruct('ProductVersion', {version!r})
  ])]), VarFileInfo([VarStruct('Translation', [2052, 1200])])]
)
"""
    path.write_text(text, encoding="utf-8")


def portable_instructions(version: str) -> str:
    return f"""WuwaTappo 宝箱自动领取 {version}

1. 将 ZIP 完整解压到固定文件夹，双击 WuwaTappoChestHelper.exe 即可运行，无需安装 Python。
   必须保留 EXE 旁的整个 _internal 文件夹，不可只复制 EXE 或在 ZIP 中直接运行。
2. 打开时保持暂停，点击“开始领取”或按 F8 开始。F8 暂停/继续，F12 退出。
3. 保持 WuwaTappo 正在运行、桌面解锁、金色宝箱没有被其他窗口遮住。
4. 若提示桌宠权限较高，点击“以管理员身份重新启动”，并亲自确认 Windows UAC。
5. 两个宝箱分别识别和领取；点击后等待图案连续两帧消失，再等待各自刷新。
6. 可以最小化助手窗口。关闭助手则停止自动领取。

宝箱图案和背景图已内置，不需要截图或配置图片。
鼠标和键盘空闲 1.5 秒后才会点击；点击时会短暂移动鼠标。
同一宝箱最多尝试 3 次，图案仍在则暂停并提示。
“确认图案消失”表示观察到界面变化，实际奖励以桌宠内显示为准。
设置和运行日志会保存在助手的数据目录中。

1.5 版将运行依赖放在 _internal 中，启动不再创建临时解包目录。
SHA256SUMS.txt 可用于检查 EXE 和运行依赖是否完整。项目源码和构建方法见 GitHub 仓库 README。
"""


def build(clean: bool = False) -> list[Path]:
    if os.name != "nt" or sys.maxsize <= 2**32:
        raise RuntimeError("Please build on 64-bit Windows with 64-bit Python 3.12+.")
    version = app_version()
    work = ROOT / "build"
    output = ROOT / "dist"
    work.mkdir(exist_ok=True)
    output.mkdir(exist_ok=True)
    destination = output / EXE_NAME
    if destination.exists():
        try:
            # Probe without writing bytes, so a running EXE is left untouched.
            with destination.open("r+b"):
                pass
        except PermissionError as exc:
            raise RuntimeError("Please close the running packaged helper, then build again.") from exc

    from ui_assets import write_icon

    icon = work / "app.ico"
    write_icon(icon)
    metadata = work / "version_info.txt"
    write_version_info(metadata, version)
    command = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--onedir",
        "--windowed", "--noupx", "--name", Path(EXE_NAME).stem,
        "--icon", str(icon), "--version-file", str(metadata),
        "--distpath", str(work / "package"),
        "--workpath", str(work / "pyinstaller"), "--specpath", str(work),
    ]
    if clean:
        command.append("--clean")
    command.append(str(ROOT / "auto_chest.py"))
    subprocess.run(command, cwd=ROOT, check=True)
    package = work / "package" / Path(EXE_NAME).stem
    built = package / EXE_NAME
    if not built.is_file() or built.stat().st_size == 0:
        raise RuntimeError("PyInstaller did not produce a usable EXE.")
    runtime = package / "_internal"
    if not runtime.is_dir():
        raise RuntimeError("PyInstaller did not include the runtime folder.")
    report = work / "package-self-test.json"
    if report.exists():
        report.unlink()
    subprocess.run([str(built), "--self-test", str(report)], cwd=work, timeout=90, check=True)
    data = json.loads(report.read_text(encoding="utf-8"))
    if not data.get("ok") or not data.get("frozen") or data.get("version") != version:
        raise RuntimeError(f"Packaged self-test failed: {data}")

    # Stage the complete runtime before replacing anything. User settings and
    # logs sit in output, outside _internal, and are preserved during updates.
    with tempfile.TemporaryDirectory(prefix="chest-package-", dir=work) as staging_name:
        staging = Path(staging_name).resolve()
        staging.relative_to(work.resolve())
        installed = (output / "_internal").resolve()
        installed.relative_to(output.resolve())
        replacement = staging / "_internal"
        previous = staging / "previous-runtime"
        shutil.copytree(runtime, replacement)
        staged_exe = staging / EXE_NAME
        shutil.copy2(built, staged_exe)
        if installed.exists():
            installed.rename(previous)
        try:
            replacement.rename(installed)
            staged_exe.replace(destination)
        except OSError:
            if installed.exists():
                installed.rename(staging / "failed-runtime")
            if previous.exists():
                previous.rename(installed)
            raise

    instructions = output / "简明使用说明.txt"
    instructions.write_text(portable_instructions(version), encoding="utf-8-sig")
    packaged = [destination, *sorted((output / "_internal").rglob("*"))]
    packaged = [path for path in packaged if path.is_file()]
    runtime_checksums = "".join(f"{sha256(path)}  {path.relative_to(output).as_posix()}\n" for path in packaged)
    archive = output / f"WuwaTappoChestHelper-v{version}-windows-x64.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in packaged:
            bundle.write(path, path.relative_to(output).as_posix())
        bundle.write(instructions, instructions.name)
        bundle.writestr("SHA256SUMS.txt", runtime_checksums)
    checksums = output / "SHA256SUMS.txt"
    checksums.write_text(runtime_checksums + f"{sha256(archive)}  {archive.name}\n", encoding="utf-8")
    return [destination, archive, checksums]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clean", action="store_true", help="Clear PyInstaller caches for this build")
    arguments = parser.parse_args()
    try:
        paths = build(clean=arguments.clean)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Build failed: {exc}\n")
    for path in paths:
        print(path)


if __name__ == "__main__":
    main()
