"""Build a Windows GUI with adjacent dependencies; no temporary extraction."""
from __future__ import annotations

import argparse
import ast
import hashlib
from importlib import metadata as package_metadata
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
2. 打开时保持暂停，默认“无黄框领取（不占鼠标）”。点击“开始领取”或按 F8 开始。
   需要其他方式时可在下拉框选择；下方会显示对应说明。F8 暂停/继续，F12 退出。
3. 三种领取方式：
   “无黄框领取（不占鼠标）”：桌面截图和定向消息，不移动真实鼠标、不抢焦点，不等待键鼠空闲。
   不产生窗口捕获黄框；宝箱需要在桌面可见，被其他窗口完全遮住时等待恢复。
   “遮挡后台（可能有黄框）”：独立读取桌宠窗口，使用定向消息，不占鼠标，支持普通全屏遮挡。
   本机 Windows 10 19045 的系统捕获黄框无法关闭；Windows 11 会自动尝试关闭，依系统版本和授权而定。
   不能保证窗口捕获始终无黄框；需要确定无黄框时请选择默认方式。独占全屏游戏尚未验证。
   “可见点击（兼容模式）”：宝箱需要在桌面可见，键鼠空闲 1.5 秒后短暂移动鼠标点击。
   切换方式前先暂停。
   首次升级时，1.7 保存的遮挡后台设置会改为无黄框；在 1.8 及以后版本手动选择其他方式后会保存该选择。
4. 若提示桌宠权限较高，点击“以管理员身份重新启动”，并亲自确认 Windows UAC。
5. 两个宝箱分别识别和领取；点击后等待图案连续两帧消失，再等待各自刷新。
6. 可以最小化助手窗口。关闭助手则停止自动领取。
7. 未识别或未领取时，先暂停并保留故障状态，选择对应模式后点击“仅识别一次”。
   会显示分阶段诊断，并写入数据目录的 diagnostics/latest.json；不点击、不保存截图。
   请提供该 JSON 文件用于排查。数据目录默认在 EXE 旁；不可写时使用 %LOCALAPPDATA%\\WuwaTappoChestHelper。
   命令行也可使用 WuwaTappoChestHelper.exe --diagnose diagnostics/manual.json，默认读取配置中的模式。
   可加 --mode borderless、--mode background 或 --mode visible 指定诊断方式。

宝箱图案和背景图已内置，不需要截图或配置图片。
保持 WuwaTappo 正在运行、桌面解锁；不要最小化或隐藏桌宠本身。
无黄框和遮挡后台模式均不需要等待键鼠空闲，不移动桌宠，也不会自动改用真实鼠标。
只有遮挡后台模式加载 MaaFramework；无黄框和可见点击模式均不加载该截图组件。
无宝箱时自动降低检查频率，默认最长每 10 秒检查一次，发现宝箱后恢复快检。
桌宠未出现、锁屏或截图不可用时默认每 15 秒重查。
遮挡后台模式已实测普通不透明全屏遮挡；另外两种模式在完全遮挡时等待。
锁屏时暂停，解锁后继续。睡眠和休眠期间不能领取。
同一宝箱最多尝试 3 次，图案仍在则暂停并提示。
“确认图案消失”表示观察到界面变化，实际奖励以桌宠内显示为准。
设置和运行日志会保存在助手的数据目录中。

1.8.1 改进窗口边框略微超出屏幕时的识别，并让识别/预览工作线程各自启用 DPI 感知。
只识别真实屏幕像素覆盖的完整宝箱，不把屏幕外填充区域当作有效图像。
这些兼容性改进尚未在遇到故障的那台 Windows 11 电脑上验证解决问题，请提供诊断报告继续定位。

1.5 版将运行依赖放在 _internal 中，启动不再创建临时解包目录。
SHA256SUMS.txt 可用于检查 EXE 和运行依赖是否完整。项目源码和构建方法见 GitHub 仓库 README。
遮挡后台截图使用 MaaFramework；第三方许可和来源见 THIRD_PARTY_NOTICES.md 与 licenses/。
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
        # The capture SDK is imported only inside the spawned capture process.
        # Keep its DLLs adjacent in maa/bin so ctypes can load their dependencies.
        "--collect-all", "maa", "--copy-metadata", "MaaFw",
        "--copy-metadata", "StrEnum", "--copy-metadata", "Pillow",
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
    background_report = work / "package-background-self-test.json"
    if background_report.exists():
        background_report.unlink()
    subprocess.run([str(built), "--background-self-test", str(background_report)],
                   cwd=work, timeout=90, check=True)
    background_data = json.loads(background_report.read_text(encoding="utf-8"))
    if not background_data.get("ok") or not background_data.get("frozen"):
        raise RuntimeError(f"Packaged background self-test failed: {background_data}")

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
    license_file = output / "LICENSE"
    shutil.copy2(ROOT / "LICENSE", license_file)
    notices = output / "THIRD_PARTY_NOTICES.md"
    shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.md", notices)
    shutil.copytree(ROOT / "licenses", output / "licenses", dirs_exist_ok=True)
    runtime_licenses = output / "licenses" / "runtime"
    runtime_licenses.mkdir(exist_ok=True)
    shutil.copy2(Path(sys.base_prefix) / "LICENSE.txt", runtime_licenses / "CPython-LICENSE.txt")
    pyinstaller = package_metadata.distribution("PyInstaller")
    installer_license = next(
        pyinstaller.locate_file(path) for path in pyinstaller.files or ()
        if str(path).replace("\\", "/").endswith("/licenses/COPYING.txt")
    )
    shutil.copy2(installer_license, runtime_licenses / "PyInstaller-COPYING.txt")
    packaged = [destination, instructions, license_file, notices,
                *sorted((output / "_internal").rglob("*")),
                *sorted((output / "licenses").rglob("*"))]
    packaged = [path for path in packaged if path.is_file()]
    runtime_checksums = "".join(f"{sha256(path)}  {path.relative_to(output).as_posix()}\n" for path in packaged)
    archive = output / f"WuwaTappoChestHelper-v{version}-windows-x64.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in packaged:
            bundle.write(path, path.relative_to(output).as_posix())
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
