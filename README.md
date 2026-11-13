# WuwaTappo 宝箱助手

识别 WuwaTappo 桌宠上的金色宝箱，出现后自动领取。宝箱图案和“记得打卡”背景图已内置，更换桌宠角色无需重新截图或配置图片。

## 直接运行 EXE

在本仓库的 [Releases](../../releases) 下载 Windows ZIP。支持 Windows 10 1903+/Windows 11 x64，无需安装 Python。将 ZIP **完整解压**到固定文件夹，再运行其中的 `WuwaTappoChestHelper.exe`，保留它旁边的整个 `_internal` 文件夹。

1. 默认选择 **无黄框领取（不占鼠标）**。保持宝箱在桌面可见，点击 **开始领取**，或按 **F8**；需要其他方式时可在下拉框切换。
2. 保持 WuwaTappo 运行、桌面解锁。助手可以最小化；桌宠本身不要最小化或隐藏。
3. 两个宝箱分别识别和领取；点击后确认图案消失，再等待各自刷新。无需显示倒计时文字。
4. **F8** 暂停/继续，**F12** 退出。打开程序时保持暂停。

如果桌宠以管理员权限运行，请点击 **以管理员身份重新启动**，并亲自确认 Windows UAC。也可右键 EXE 选择“以管理员身份运行”。

程序采用文件夹打包，启动时不创建临时解包目录，保留了 1.5 版对“Could not create temporary directory”的修复。不能只复制 EXE，也不要直接在 ZIP 中运行。

## 三种领取方式（1.8.1）

| 领取方式 | 鼠标和取图方式 | 遮挡与边框 |
| --- | --- | --- |
| **无黄框领取（不占鼠标）** | 读取桌面图像，向桌宠发送定向消息；不移动真实鼠标、不抢焦点，不等待键鼠空闲 | 不产生窗口捕获黄框；金色宝箱需要在桌面可见，被完全遮住时等待恢复 |
| **遮挡后台（可能有黄框）** | 使用 MaaFramework `FramePool` 独立读取桌宠窗口，发送定向消息；不占鼠标、不抢焦点，不等待键鼠空闲 | 支持普通全屏窗口遮挡；系统可能显示捕获黄框，Windows 11 会自动尝试关闭 |
| **可见点击（兼容模式）** | 读取桌面图像，键鼠空闲 1.5 秒后短暂移动鼠标点击 | 不产生窗口捕获黄框；宝箱需要在桌面可见 |

希望没有黄框且正常使用鼠标时，选择“无黄框领取”；需要在桌宠被其他窗口完全遮住时领取，选择“遮挡后台”。切换方式前先暂停。无黄框模式与遮挡后台模式都向同一桌宠进程的 `DesktopCatInput` 输入层发送消息，不移动桌宠。

界面会随当前选择显示对应说明。首次从 1.7 升级时，原来的遮挡后台设置会切换为无黄框领取；在 1.8 及以后版本中手动选择的其他方式会保存，后续启动继续使用。

- “无黄框领取”不会隔着其他应用识别隐藏的宝箱。全屏应用完全遮住桌宠时会等待，退出遮挡后重新识别。
- “遮挡后台”在本机 WuwaTappo 1.0.4 已实测普通后台领取，以及被不透明全屏测试窗口完全遮住时的识别和领取。两次单击前后，真实鼠标位置、前台窗口和桌宠位置保持不变。
- Windows 10 的窗口捕获黄框是系统提示；本机 Windows 10 19045 没有关闭该边框的系统接口。“无黄框领取”使用桌面截图，无需这个接口。
- 在 Windows 11 上，遮挡后台模式会自动尝试关闭捕获黄框；实际效果取决于系统版本和相关授权，不能保证成功。需要确定没有捕获黄框时，请使用无黄框领取。
- 普通全屏遮挡的验证适用于遮挡后台模式。独占全屏游戏、应用主动隐藏桌宠、远程桌面断开等情况未验证，不能保证可用。
- 锁屏时暂停截图和点击，解锁后重新识别。睡眠和休眠期间不能领取。
- 截图失败、窗口变化或无法确认图像时会等待恢复；无黄框和遮挡后台模式不会自动改用真实鼠标。

**仅识别一次**会显示匹配结果和分阶段诊断，并生成诊断报告，不点击。

## 未识别或未领取时诊断（1.8.1）

先暂停领取，让故障状态下的宝箱保持可见，选择需要排查的模式，再点击 **仅识别一次**。助手会报告环境、窗口、取图和匹配各阶段的结果，并写入数据目录中的 `diagnostics/latest.json`。诊断不点击，也不保存截图。排查问题时请提供这个 JSON 文件。

数据目录默认在 EXE 旁边；如果该目录不可写，则使用 `%LOCALAPPDATA%\WuwaTappoChestHelper`，报告相应位于其中的 `diagnostics/latest.json`。

也可在 EXE 所在目录打开 PowerShell，指定报告位置：

```powershell
.\WuwaTappoChestHelper.exe --diagnose .\diagnostics\manual.json
.\WuwaTappoChestHelper.exe --diagnose .\diagnostics\background.json --mode background
```

`--diagnose JSON` 默认读取已保存的模式；`--mode` 可指定 `borderless`、`background` 或 `visible`，分别对应上表三种方式。命令行诊断同样不点击、不保存截图。

1.8.1 修正窗口边框略微超出屏幕时整窗被跳过的问题，只识别真实屏幕像素覆盖的完整宝箱；识别和预览工作线程也各自启用 DPI 感知，保持截图与窗口坐标一致。这些是兼容性改进，尚未确认遇到问题的那台 Windows 11 电脑的具体原因，也未验证其故障已经解决，请以该电脑生成的诊断报告继续定位。

## 低占用运行

- 匹配前排除颜色不可能符合的区域，两套图案共享同一帧的计算数据，减少 CPU 和临时内存开销。
- 连续未发现宝箱时逐步降低检查频率。默认活跃间隔 2 秒，空闲时最长每 10 秒检查一次；看到宝箱或等待点击确认时恢复活跃间隔。
- 桌面锁定、桌宠未出现或截图不可用时每 15 秒重查一次；暂停和退出仍可立即打断等待。
- MaaFramework 仅在遮挡后台模式启动时加载到独立截图进程；暂停、退出或重连时释放该进程。无黄框和可见点击模式均不加载 SDK。
- 默认设置下，宝箱可被当前模式识别时，刷新后通常约 2～12 秒发出点击，实际时间受设备、截图和匹配耗时影响；将活跃间隔调大也会增加等待。遮挡后台模式使用独立窗口截图，其内存和 GPU 开销与另外两种模式不同。

## 识别与确认

- 默认匹配阈值 0.94，连续两帧匹配才点击，并在点击前重新核对图像、窗口和权限。
- 同一宝箱点击后仍可见至少 4 秒才重试，最多 3 次，图案仍在则暂停。两个宝箱的状态与重试分别处理。
- 无黄框和遮挡后台模式的点击仅发送给当前已验证的 `WuwaTappo.exe` 输入窗口。窗口退出、PID/句柄变化、锁屏或截图不可用时不发送点击。
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
python auto_chest.py --background-self-test build/background-self-test.json
python auto_chest.py --check
python auto_chest.py --scan-once
python auto_chest.py --diagnose build/source-diagnostic.json
```

`--check` 只检查运行环境和桌宠窗口；`--scan-once` 识别一次，不发送点击。`--diagnose JSON` 生成分阶段诊断报告，默认读取配置中的模式，可用 `--mode` 覆盖。`--background-self-test` 检查截图组件及独立进程启动，不发送输入。测试图像内置于 `fixture_assets.py`，不需要额外下载。

## 构建 Windows EXE

```powershell
python -m pip install -r requirements-build.txt
python build_windows.py --clean
```

生成文件位于 `dist/`：

- `WuwaTappoChestHelper.exe`：图形程序入口，与旁边的 `_internal` 一起使用。
- `_internal/`：内置 Python、界面库、背景图、宝箱图案与后台截图组件，必须完整保留。
- `WuwaTappoChestHelper-v1.8.1-windows-x64.zip`：完整程序、说明、第三方许可和校验信息。
- `SHA256SUMS.txt`：EXE、运行依赖、说明、许可与 ZIP 的 SHA256 校验。
- `THIRD_PARTY_NOTICES.md` / `licenses/`：第三方组件的来源和许可文本。

GitHub Actions 在 `main` 更新后执行测试、构建和 EXE 自检，再发布对应版本的 Release。已有版本不会覆盖；发布新版前请更新 `auto_chest.py` 的 `APP_VERSION` 和发布说明。日志、个人配置、诊断截图、本地依赖和构建缓存不进入仓库。

## 主要文件

- `auto_chest.py`：界面、运行模式、监控、独立确认与重试。
- `chest_background.py`：独立窗口截图进程和定向消息点击。
- `ui_assets.py`：用户提供的背景图，以无损格式内嵌。
- `chest_assets.py`：内置宝箱图案。
- `chest_detection.py` / `chest_matcher.py`：多目标模板匹配。
- `chest_windows.py`：Windows 截图、权限、窗口层级与鼠标输入。
- `chest_state.py`：连续帧和防重复点击状态。
- `build_windows.py`：EXE、ZIP 和校验生成。

## 第三方组件

遮挡后台模式使用 [MaaFramework](https://github.com/MaaXYZ/MaaFramework) 5.16.0（LGPL-3.0），运行库以独立 DLL 形式随程序分发。项目自身代码保持 MIT 许可。第三方许可、源码地址和替换说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
