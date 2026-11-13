# 构建与安装包

## 开发运行

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m modpilot
```

或 `python run.py`。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\ruff.exe check modpilot tests --select F,E9
```

UI 测试设置 `QT_QPA_PLATFORM=offscreen`，可在无显示环境运行。

## 图标

```powershell
.\.venv\Scripts\python.exe scripts\make_icon.py
```

生成：

- `resources/modpilot.ico`（多尺寸 PNG 帧 ICO）
- `resources/modpilot.png`

窗口图标在运行时由同一套 SVG 图形渲染；ICO 用于 EXE/安装包。

## PyInstaller

`modpilot.spec` 使用 one-dir 模式，窗口程序，无控制台：

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --clean --noconfirm modpilot.spec
```

输出：`dist\ModPilot\ModPilot.exe`。

one-dir 比 one-file 启动更快，也便于增量更新与排查缺失依赖。分发时请整体分发 `dist\ModPilot` 目录。

## Inno Setup

1. 安装 [Inno Setup 6](https://jrsoftware.org/isinfo.php)。
2. 确保已构建 `dist\ModPilot`。
3. 编译：

```powershell
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\modpilot.iss
```

输出：`installer\Output\ModPilot-Setup-0.1.0.exe`。

安装器采用 per-user 安装（`{localappdata}\Programs\ModPilot`），不需要管理员权限。用户数据默认在 `%LOCALAPPDATA%\ModPilot`，卸载不会删除。

## 一键构建

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build.ps1
```

脚本会：

1. 运行 pytest（可用 `-SkipTests` 跳过）。
2. 生成图标。
3. 调用 PyInstaller。
4. 若找到 `ISCC.exe`，构建安装包；否则给出提示。

## 发布检查清单

- [ ] `pytest` 通过
- [ ] `ruff check ... --select F,E9` 通过
- [ ] 手动启动 `dist\ModPilot\ModPilot.exe`
- [ ] 在干净 Windows 用户上测试首次启动（数据目录自动创建）
- [ ] 使用真实 Nexus API Key 测试元数据查询
- [ ] 测试非 Premium 下载失败提示
- [ ] 测试 AI 翻译/诊断超时与错误提示
- [ ] 验证安装/卸载/回滚
- [ ] 检查安装包不包含 `.venv`、测试数据、密钥或日志
- [ ] 更新版本号（`modpilot/version.py`、`pyproject.toml`、`installer/modpilot.iss`）
