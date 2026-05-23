# Tauri 桌面包构建指南

> 把 financial-analyst CLI + GuanLan UI 打成 Windows `.msi` / macOS `.dmg` /
> Linux `.AppImage` 单文件安装包. 用户双击安装, 完全脱离 Python 环境.
>
> **当前状态**: 骨架文件已建 (`packaging/src-tauri/` + `packaging/pyinstaller/`),
> 实际 build 需要 maintainer 在装 Rust toolchain 后跑一次. 文档化为后续工作.

## 一、为什么是 Tauri

| 方案 | 包体积 | 性能 | 维护 |
|------|------|------|------|
| **Tauri 2.x** ⭐ | ~5 MB shell (用系统 webview) | 启动 <1s | Rust 一份代码三平台 |
| Electron | ~120 MB (Chromium 内嵌) | 启动 2-3s | JS, 但要带 Chromium |
| PyWebview | ~50 MB | 启动 1-2s | Python, 但 webview 渲染不稳 |
| 浏览器 + http.server | 0 MB | 用户自己开浏览器 | 体验差 |

Tauri 用系统 WebView (Windows 10+ 自带 Edge, macOS WebKit, Linux WebKitGTK),
shell 自己只 ~3-5MB, 所有 Python runtime 通过 PyInstaller 打成 sidecar.exe.

最终 .msi/.dmg ≈ PyInstaller exe (200-500MB) + Tauri shell (5MB) ≈ 250 MB.

## 二、前置依赖

### 2.1 Rust toolchain (一次性, ~600MB)

```bash
# Windows: 走 rustup-init.exe 安装器
# 浏览器开 https://www.rust-lang.org/tools/install
# 下载 rustup-init.exe, 双击, 选 default profile

# macOS / Linux:
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
```

装完验证:
```bash
rustc --version    # rustc 1.75+ (anything reasonably modern)
cargo --version
```

### 2.2 Tauri CLI

```bash
# 用 cargo (推荐, 不依赖 Node)
cargo install tauri-cli --version "^2.0"

# 验证
cargo tauri --version
```

或用 npm:
```bash
npm install -g @tauri-apps/cli@latest
tauri --version
```

### 2.3 Windows: WebView2 (Win 11 自带, Win 10 可能要装)

```bash
# 检查
reg query "HKEY_LOCAL_MACHINE\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients" 2>nul
# 没装就到 https://developer.microsoft.com/microsoft-edge/webview2/ 下 Evergreen Bootstrapper
```

### 2.4 platform 特定

| 平台 | 额外要求 |
|------|---------|
| Windows | MSVC C++ build tools (走 rustup-init 安装时勾选) |
| macOS | Xcode Command Line Tools (`xcode-select --install`) |
| Linux | `webkit2gtk-4.1-dev` + `libayatana-appindicator3-dev` + `librsvg2-dev` |

## 三、打包流程 (3 步)

### Step 1: PyInstaller 打 financial-analyst single-exe

```bash
cd G:/financial-analyst
G:/financial-analyst/.venv/Scripts/pyinstaller.exe \
    packaging/pyinstaller/financial-analyst.spec \
    --distpath packaging/dist \
    --workpath packaging/build \
    --noconfirm
```

产出: `packaging/dist/financial-analyst.exe` (200-500 MB).

验证:
```bash
packaging/dist/financial-analyst.exe data status
# 应输出: 日线 provider_uri: ... instruments (all): N 只 ...

packaging/dist/financial-analyst.exe serve --port 9998
# 浏览器开 http://127.0.0.1:9998/health 应 200
```

### Step 2: 复制 sidecar 到 Tauri 期望位置

Tauri 要求 sidecar 文件名按 target triple 加后缀, 例如 Windows 是
`financial-analyst-x86_64-pc-windows-msvc.exe`.

```bash
mkdir -p packaging/src-tauri/binaries
cp packaging/dist/financial-analyst.exe \
   packaging/src-tauri/binaries/financial-analyst-x86_64-pc-windows-msvc.exe
```

macOS:
```bash
cp packaging/dist/financial-analyst \
   packaging/src-tauri/binaries/financial-analyst-x86_64-apple-darwin
# 或 aarch64-apple-darwin (Apple Silicon)
```

### Step 3: Tauri build

```bash
cd packaging/src-tauri
cargo tauri build
```

产出:
- Windows: `packaging/src-tauri/target/release/bundle/msi/financial-analyst_0.1.0_x64_en-US.msi`
- macOS:   `target/release/bundle/dmg/financial-analyst_0.1.0_x64.dmg`
- Linux:   `target/release/bundle/appimage/financial-analyst_0.1.0_amd64.AppImage`

第一次 build ~15-30 min (Rust 编译所有依赖). 之后增量 ~1-2 min.

## 四、Dev 模式 (调试)

```bash
cd packaging/src-tauri
cargo tauri dev
```

- 启 Tauri shell window
- 自动跑 `before_dev_command` (起前端 http.server)
- spawn sidecar (financial-analyst.exe serve --port 9999)
- 任何 Rust / 前端文件改动自动 reload

第一次 ~5 min compile Rust deps, 之后秒级.

## 五、配置说明 (tauri.conf.json)

| key | 用途 |
|-----|------|
| `productName` | 安装包显示的 app 名 |
| `identifier` | 反向域名标识 (Apple/Google 注册用) |
| `build.frontendDist` | 静态前端目录 (相对于 src-tauri/), 默认指 `G:/stocks/fa_ui_ready` |
| `build.devUrl` | dev 时前端地址 (我们用 http.server 5173) |
| `app.windows[0]` | 主窗口大小/标题 |
| `app.security.csp` | Content Security Policy. 我们允许 127.0.0.1:9999 + unpkg.com (babel) |
| `bundle.externalBin` | sidecar binary 名前缀, Tauri 自动按 target triple 找 |
| `bundle.icon` | 各平台图标 (.ico / .icns / .png) — 暂用 placeholder |

## 六、图标

Tauri 需要这些图标 (放 `packaging/src-tauri/icons/`):

```
32x32.png       (Linux, basic)
128x128.png     (Linux retina)
icon.ico        (Windows)
icon.icns       (macOS)
```

**生成图标**:
```bash
# Tauri 提供工具从单个 PNG 生成全套
cargo tauri icon path/to/logo-1024.png
```

待设计 logo 后填 `packaging/src-tauri/icons/`.

## 七、签名 (可选, 生产发布要)

### Windows
- 申请 EV 或 OV 代码签名证书 (DigiCert / Sectigo ~$300/yr)
- `signtool sign /a /tr http://timestamp.digicert.com financial-analyst.msi`
- 或不签 — 用户首次启动会过 SmartScreen warning

### macOS
- Apple Developer ID ($99/yr)
- `xcrun notarytool submit financial-analyst.dmg --apple-id ... --team-id ...`
- 或不签 — 用户右键 "打开" 跳过 Gatekeeper

### Linux
- AppImage 不需要签名

## 八、自动 update (P3 后)

Tauri 内置 update plugin. 配 `tauri.conf.json::plugins.updater.endpoints` 指向
HF / GitHub Releases JSON 文件, app 自动检测新版本 + 下载 + 重启.

```bash
# 生成签名 keypair
cargo tauri signer generate -w ~/.tauri/financial-analyst.key
# 公钥放 tauri.conf.json::plugins.updater.pubkey
# 私钥仅 maintainer 持有, build 时签 .sig 文件
```

P3 工作量, 先打能跑的版本.

## 九、CI/CD (P3 后)

GitHub Actions Tauri Action:

```yaml
# .github/workflows/release.yml
name: release
on:
  push:
    tags: [v*]
jobs:
  build:
    strategy:
      matrix:
        platform: [windows-latest, macos-latest, ubuntu-latest]
    runs-on: ${{ matrix.platform }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
      - uses: dtolnay/rust-toolchain@stable
      - run: pip install pyinstaller && pip install -e .[serve]
      - run: pyinstaller packaging/pyinstaller/financial-analyst.spec --distpath packaging/dist
      - run: cp packaging/dist/financial-analyst* packaging/src-tauri/binaries/
      - uses: tauri-apps/tauri-action@v0
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        with:
          projectPath: packaging/src-tauri
          tagName: ${{ github.ref_name }}
          releaseName: ${{ github.ref_name }}
          releaseDraft: true
```

## 十、踩坑预案

| 问题 | 原因 | 解 |
|------|------|------|
| `failed to spawn sidecar 'financial-analyst'` | binaries/ 没放 target-triple 命名的 exe | rename 到 `financial-analyst-x86_64-pc-windows-msvc.exe` |
| WebView 一直白屏 | CSP 拦了 unpkg / 127.0.0.1 | 检查 tauri.conf.json::app.security.csp |
| Sidecar 杀软误报 | PyInstaller single-file 自解压被 AV 嫌 | 签名 + 关 UPX (我们的 spec 已关) |
| `MSVC linker not found` (Windows) | rustup 装时没选 MSVC | 重跑 `rustup-init -y --default-host x86_64-pc-windows-msvc` |
| `webkit2gtk not found` (Linux) | 系统没装 dev lib | `apt install libwebkit2gtk-4.1-dev` |
| Bundle 时 missing icon | icons/ 是空 | 跑 `cargo tauri icon logo.png` |

## 十一、当前未做

1. **真实 build 通过** — 需要先装 Rust toolchain (用户机器没装)
2. **代码签名证书** — $300/yr, 个人项目 P3 再说
3. **CI 自动 build matrix** — 三平台并行, 等代码稳定再加
4. **GuanLan UI 内嵌 vs 引用 G:/stocks/fa_ui_ready** — 当前 `frontendDist` 指向 G:/stocks 目录, 不利于分发. 应该 copy fa_ui_ready 进 packaging/src-tauri/ 子目录
5. **自动 update endpoint** — Tauri updater 配 HF / GitHub Releases

## 十二、状态 checkpoint

- [x] `packaging/pyinstaller/financial-analyst.spec` 写完
- [x] `packaging/pyinstaller/fa_entry.py` entry shim
- [x] `packaging/src-tauri/tauri.conf.json` 配置
- [x] `packaging/src-tauri/Cargo.toml`
- [x] `packaging/src-tauri/src/main.rs` (sidecar 管理 + cleanup)
- [x] `packaging/src-tauri/build.rs`
- [x] 本文档
- [ ] **装 Rust toolchain (maintainer 跑)**
- [ ] **跑 PyInstaller build (启动验证)**
- [ ] **跑 cargo tauri dev 测试 dev 流**
- [ ] **跑 cargo tauri build 出 .msi**
- [ ] **签名 + CI**

骨架就位. 用户装好 Rust 后, 按 §三 三步骤就能出 .msi.
