# WuwaTappo 宝箱助手

识别 WuwaTappo 桌宠上的金色宝箱，出现后自动领取。宝箱图案和“记得打卡”背景图已内置，更换桌宠角色无需重新截图或配置图片。

## 直接运行 EXE

在本仓库的 [Releases](../../releases) 下载 Windows ZIP。支持 Windows 10 1903+/Windows 11 x64，无需安装 Python。将 ZIP **完整解压**到固定文件夹，再运行其中的 `WuwaTappoChestHelper.exe`，保留它旁边的整个 `_internal` 文件夹。

1. 选择 **后台领取**，点击 **开始领取**，或按 **F8**。后台模式不会移动真实鼠标、抢焦点或移动桌宠，可以同时使用其他应用。
2. 保持 WuwaTappo 运行、桌面解锁。助手可以最小化；桌宠本身不要最小化或隐藏。
3. 两个宝箱分别识别和领取；点击后确认图案消失，再等待各自刷新。无需显示倒计时文字。
4. **F8** 暂停/继续，**F12** 退出。打开程序时保持暂停。

如果桌宠以管理员权限运行，请点击 **以管理员身份重新启动**，并亲自确认 Windows UAC。也可右键 EXE 选择“以管理员身份运行”。

程序采用文件夹打包，启动时不创建临时解包目录，保留了 1.5 版对“Could not create temporary directory”的修复。不能只复制 EXE，也不要直接在 ZIP 中运行。

## 后台领取（1.7）

- 后台模式使用 MaaFramework `FramePool` 独立读取桌宠渲染窗口，再向同一进程的 `DesktopCatInput` 输入层发送定向鼠标消息。不会调用真实鼠标点击，也不需要等待键鼠空闲。
- 本机 WuwaTappo 1.0.4 已实测普通后台领取，以及被不透明全屏测试窗口完全遮住时的识别和领取。两次单击前后，真实鼠标位置、前台窗口和桌宠位置保持不变。
- 此验证覆盖普通全屏遮挡。独占全屏游戏、应用主动隐藏桌宠、远程桌面断开等情况未验证，不能保证可用。
- 锁屏时暂停截图和点击，解锁后重新识别。睡眠和休眠期间不能领取。
- 截图失败、窗口变化或无法确认图像时会等待恢复；后台模式不会自动改用真实鼠标。
- **可见点击（兼容模式）**保留原有方式：要求按钮在桌面可见，键鼠空闲 1.5 秒后短暂移动鼠标点击。切换模式前先暂停。

**仅识别一次**只显示匹配位置和相似度，不点击。

## 低占用运行

- 匹配前排除颜色不可能符合的区域，两套图案共享同一帧的计算数据，减少 CPU 和临时内存开销。
- 连续未发现宝箱时逐步降低检查频率。默认活跃间隔 2 秒，空闲时最长每 10 秒检查一次；看到宝箱或等待点击确认时恢复活跃间隔。
- 桌面锁定、桌宠未出现或截图不可用时每 15 秒重查一次；暂停和退出仍可立即打断等待。
- 后台截图 SDK 仅在后台模式启动时加载到独立截图进程；暂停、退出或重连时释放该进程。可见点击模式不加载 SDK。
- 默认设置下，刷新后通常约 2～12 秒发出点击，实际时间受设备、截图和匹配耗时影响；将活跃间隔调大也会增加等待。后台模式使用独立窗口截图，其内存和 GPU 开销与可见点击模式不同。

## 识别与确认

- 默认匹配阈值 0.94，连续两帧匹配才点击，并在点击前重新核对图像、窗口和权限。
- 同一宝箱点击后仍可见至少 4 秒才重试，最多 3 次，图案仍在则暂停。两个宝箱的状态与重试分别处理。
- 点击仅发送给当前已验证的 `WuwaTappo.exe` 输入窗口。窗口退出、PID/句柄变化、锁屏或截图不可用时不发送点击。
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
```

`--check` 只检查运行环境和桌宠窗口；`--scan-once` 读取当前桌面并识别，不发送点击。`--background-self-test` 检查截图组件及独立进程启动，不发送输入。测试图像内置于 `fixture_assets.py`，不需要额外下载。

## 构建 Windows EXE

```powershell
python -m pip install -r requirements-build.txt
python build_windows.py --clean
```

生成文件位于 `dist/`：

- `WuwaTappoChestHelper.exe`：图形程序入口，与旁边的 `_internal` 一起使用。
- `_internal/`：内置 Python、界面库、背景图、宝箱图案与后台截图组件，必须完整保留。
- `WuwaTappoChestHelper-v1.7-windows-x64.zip`：完整程序、说明、第三方许可和校验信息。
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

后台截图使用 [MaaFramework](https://github.com/MaaXYZ/MaaFramework) 5.16.0（LGPL-3.0），运行库以独立 DLL 形式随程序分发。项目自身代码保持 MIT 许可。第三方许可、源码地址和替换说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
