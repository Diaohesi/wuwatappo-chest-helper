# WuwaTappo 宝箱助手

识别 WuwaTappo 桌宠上的金色宝箱，出现后自动左键单击。宝箱图案和“记得打卡”背景图已内置，更换桌宠角色无需重新截图或配置图片。

## 直接运行 EXE

在本仓库的 [Releases](../../releases) 下载 Windows ZIP。支持 Windows 10/11 x64，无需安装 Python。将 ZIP **完整解压**到固定文件夹，再运行其中的 `WuwaTappoChestHelper.exe`，并保留它旁边的整个 `_internal` 文件夹。

1.5 版将依赖随程序存放，启动时无需创建临时解包目录，修复单文件版的“Could not create temporary directory”错误。不能只复制 EXE，也不要直接在 ZIP 中运行。

1. 打开程序，点击 **开始领取**，或按 **F8**。
2. 保持 WuwaTappo 运行、桌面解锁、宝箱按钮可见。助手可最小化。
3. 两个宝箱分别领取；点击后图案连续两帧消失就确认，再等待该宝箱各自刷新。无需显示倒计时文字。
4. **F8** 暂停/继续，**F12** 退出。打开程序时保持暂停。

如果提示桌宠以管理员权限运行，请点击 **以管理员身份重新启动**，并亲自确认 Windows UAC。也可右键 EXE 选择“以管理员身份运行”。

**仅识别一次**只显示匹配位置和相似度，不点击。鼠标和键盘空闲 1.5 秒后才会自动点击，点击时会短暂移动鼠标。

## 识别与确认

- 默认匹配阈值 0.94，每轮识别结束后等待 2 秒；连续两帧匹配才点击，并在点击前重新核对图像、窗口和权限。
- 同一宝箱点击后仍可见至少 4 秒才重试，最多 3 次，图案仍在则暂停。两个宝箱的状态与重试分别处理。
- 支持领取后原位置变透明、鼠标穿透到底层窗口。遮挡、锁屏、窗口隐藏/移动、截图失败或无法判断层级时，会等待恢复后继续确认。
- 点击仅发送给当前实际接收该位置输入的 `WuwaTappo.exe` 窗口。正在拖动或按住修饰键时跳过。
- 图案按用户提供的固定像素尺寸识别。更换桌宠外观无需配置；改变宝箱 UI 像素尺寸需要更新适配。
- “确认图案消失”表示观察到界面变化，奖励是否入账以桌宠内显示为准。

配置 `config.json` 和运行日志 `logs/auto_chest.log` 默认放在 EXE 旁边；目录不可写时改用 `%LOCALAPPDATA%\WuwaTappoChestHelper`。程序日常运行不联网、不保存整屏截图。

## 从源码运行

使用 Windows x64 和 Python 3.12：

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python auto_chest.py
```

离线测试和不点击的自检：

```powershell
python -B -m unittest discover -v
python auto_chest.py --self-test build/source-self-test.json
python auto_chest.py --check
python auto_chest.py --scan-once
```

`--check` 只检查运行环境和桌宠窗口；`--scan-once` 读取当前桌面并识别，不发送点击。测试图像内置于 `fixture_assets.py`，不需要额外下载。

## 构建 Windows EXE

```powershell
python -m pip install -r requirements-build.txt
python build_windows.py --clean
```

生成文件位于 `dist/`：

- `WuwaTappoChestHelper.exe`：图形程序入口，与旁边的 `_internal` 一起使用。
- `_internal/`：内置 Python、界面库、背景图和宝箱图案的运行依赖，必须完整保留。
- `WuwaTappoChestHelper-v1.5-windows-x64.zip`：完整 EXE、依赖文件夹、简明说明和校验信息。
- `SHA256SUMS.txt`：EXE、全部运行依赖与 ZIP 的 SHA256 校验。

GitHub Actions 在 `main` 更新后执行测试、构建和 EXE 自检，再发布对应版本的 Release。已有版本不会覆盖；发布新版前请更新 `auto_chest.py` 的 `APP_VERSION` 和发布说明。日志、个人配置、诊断截图、本地依赖和构建缓存不进入仓库。

## 主要文件

- `auto_chest.py`：背景界面、监控、独立确认与重试。
- `ui_assets.py`：用户提供的背景图，以无损格式内嵌。
- `chest_assets.py`：内置宝箱图案。
- `chest_detection.py` / `chest_matcher.py`：多目标模板匹配。
- `chest_windows.py`：Windows 截图、权限、窗口层级与鼠标输入。
- `chest_state.py`：连续帧和防重复点击状态。
- `build_windows.py`：EXE、ZIP 和校验生成。
