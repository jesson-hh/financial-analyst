# 小白上手 · 从零到第一份研报

> **这份指南是写给完全没用过命令行的人的.** 假设你电脑里没装过 Python、不知道
> 什么是 pip、不会"开终端". 全程**复制粘贴**就行, 不需要懂为啥.
>
> 全程大约 **30 分钟** (含下载等待). 看不懂任何一步, 把那一行 + 报错截图发到
> [Issues](https://github.com/jesson-hh/financial-analyst/issues), 会有人回。
>
> 适用: **Windows 10 / 11** (主要). macOS / Linux 在文末.

---

## 你大概需要准备

| 东西 | 用来干嘛 | 怎么搞 |
|------|---------|--------|
| 一台电脑 | 跑程序 | 你已经有了 |
| 5 GB 硬盘空间 | 存数据 + 软件 | 检查下 C 盘还剩多少, 不够就准备好 D 盘 |
| 网络 | 下软件 + 下数据 | 普通家用宽带就行, 国内可能要挂梯子拉数据 (后面会说) |
| 一个手机号 | 注册阿里云送 100w 免费 token | 国内手机号就行 (实名认证) |
| 30 分钟 | 装 + 跑 | 一半时间是等下载 |

**你不需要的**:
- ❌ 海外信用卡 (用阿里云国内卡就行)
- ❌ Tushare 付费账号 (零成本数据源已经做好)
- ❌ 任何编程基础
- ❌ GPU / 高配电脑

---

## 第 1 步 · 装 Python (5 分钟)

Python 是程序运行的底层. 没有它, 啥都跑不起来.

### 1.1 下载安装包

浏览器打开 **https://www.python.org/downloads/**

页面最上方有个大黄按钮: **"Download Python 3.12.x"** (具体小版本号会变, 只要 3.11 或以上都行). 点它, 下到一个 `.exe` 文件.

### 1.2 安装 (关键一步, 不要漏)

双击下载好的 `.exe`. 看到下面这个画面:

```
╔═══════════════════════════════════════════╗
║  Install Python 3.12.x (64-bit)           ║
║                                            ║
║  [Install Now]    ← 不要先点这个!         ║
║  [Customize installation]                  ║
║                                            ║
║  ☐ Use admin privileges                    ║
║  ☑ Add python.exe to PATH    ← 务必勾上!! ║
╚═══════════════════════════════════════════╝
```

**最下面那个 "Add python.exe to PATH" 必须打勾** — 没勾的话, 后面所有命令都会报错 "找不到 python". 这是 90% 新手第一次装 Python 翻车的地方.

勾好之后再点 **"Install Now"**, 等进度条走完, 点 "Close".

### 1.3 如果你忘了勾, 怎么办

不用慌. 控制面板 → 程序和功能 → 找到 "Python 3.12" → 右键卸载 → 重新跑 1.2, 这次记得勾。

---

## 第 2 步 · 打开终端 (1 分钟)

"终端" 就是那个黑乎乎的窗口, 让你打字告诉电脑做事。

按 **Windows 键** (键盘左下角带田字格那个), 输入 `cmd`, 回车. 出来一个黑色窗口:

```
Microsoft Windows [Version 10.0.xxxxx.xxx]
(c) Microsoft Corporation. All rights reserved.

C:\Users\你的用户名>_
```

这就是终端. 后面所有命令都是在这个窗口里**复制粘贴 + 回车**.

> **粘贴技巧**: 在黑窗口里**右键** = 粘贴 (不是 Ctrl+V). 选中文字直接拖蓝再回车 = 复制。

---

## 第 3 步 · 检查 Python 装好了 (30 秒)

在黑窗口输 (或复制粘贴):

```cmd
python --version
```

回车. 看到这样的输出就 OK:

```
Python 3.12.4
```

如果显示 **"不是内部或外部命令"** 或 **"Python was not found"** — 说明第 1.2 步忘了勾 "Add to PATH". 回去重装, 这次勾上。

---

## 第 4 步 · 装 financial-analyst (3-5 分钟)

### 4.1 (国内用户强烈推荐) 换 pip 源到清华镜像

中国大陆默认 pip 源在国外, 慢. 一行换成清华大学镜像, 后面装包飞快:

```cmd
pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
```

回车. 没报错就成了 (大部分用户报 "Writing to ..." 一行, 这是好事).

### 4.2 装包

```cmd
pip install financial-analyst
```

回车. 终端会哗哗哗下载很多包 — 这是正常的, 我们的包依赖 30 多个底层库. 看到最后:

```
Successfully installed financial-analyst-1.0.3 ...一大堆包名...
```

就成功了. 如果中间报错, 大概率是网络问题, **重新跑一遍同一条命令**通常就过。

### 4.3 验证装好了

```cmd
fa --version
```

应该看到:

```
financial-analyst 1.0.3
```

---

## 第 5 步 · 注册免费 LLM API key (5 分钟)

我们用的是 **AI 模型** 来分析股票. AI 模型需要一个 key (类似密码), 你不付费就拿不到.

### 5.1 最划算的选择: 阿里云百炼

为啥推荐: **国内手机号注册, 不要海外卡**, 而且**送 100 万 token 免费额度** (足够跑 ~150 份完整研报).

打开 **https://bailian.console.aliyun.com/**

如果你有支付宝, 直接扫码登录最快. 没有就用手机号注册阿里云账号 (走实名认证, 大约 2 分钟).

### 5.2 创建 API key

登录后, 在页面找:
- 左侧菜单 → **"API-KEY"** 或 **"API KEY 管理"**
- 点 **"创建 API-KEY"** 按钮
- 复制弹出来的那串 `sk-xxxxxxxxxxxxxxxxxxxxxx`

**这个 key 只显示一次**. 立刻打开记事本粘上去暂存. 万一关了页面就只能重新创建一个.

> 国内用户也可以选 **DeepSeek** (deepseek.com) 作为替代, 也是国内手机号注册, 价格更便宜. 但 DashScope (阿里百炼) 跟我们默认配置最合拍, 新手第一次先用它.

---

## 第 6 步 · 一键启动 (3-15 分钟, 主要看下数据多大)

回到黑窗口. 输:

```cmd
fa start
```

回车. 第一次会走交互向导. **下面每一步出来什么, 你怎么回**:

### 向导步骤 1: 选语言

```
语言 · Language
  1  中文 (default)
  2  English
  选择 · Choose:
```

→ 输 `1` 回车 (或直接回车走默认中文).

### 向导步骤 2: 选工作目录 (workspace)

```
  Step 1 / 4    选个工作目录
  ──────────────────────────────────────
  默认  C:\Users\你的用户名\.financial-analyst   (剩余空间 25 GB)

  按回车用默认, 或输入别的路径 (例如 D:\fa-data):
```

**如果 C 盘剩余 < 5 GB**, 强烈建议输个 D 盘的路径, 比如:
```
D:\fa-data
```

否则直接**回车**用默认. 数据会装在你 Windows 用户文件夹下隐藏的 `.financial-analyst` 文件夹。

### 向导步骤 3: 填 LLM API key

```
  Step 2 / 4    LLM API key
  ──────────────────────────────────────
  Provider           Env Var               State          Description
  qwen (阿里通义)    DASHSCOPE_API_KEY      ○ 未配置       推荐, 国内直连
  deepseek           DEEPSEEK_API_KEY       ○ 未配置       国内可用
  openai             OPENAI_API_KEY         ○ 未配置       需海外网络
  anthropic          ANTHROPIC_API_KEY      ○ 未配置       需海外网络

  填一个 DASHSCOPE_API_KEY (回车跳过): ▏
```

**粘贴你第 5 步复制的 `sk-xxxxx`** (黑窗口里右键粘贴), 回车. 粘贴时你不会看到任何字符显示 — 这是安全特性, 不是你按错了, 直接回车就行.

剩下三个 (deepseek / openai / anthropic) 都直接回车跳过。

### 向导步骤 4: 选数据包

```
  Step 3 / 4    选数据包
  ──────────────────────────────────────
  #   预设      大小      股票数         耗时        适合
  1   demo      ~155 MB   300 (沪深300)  ~3 分钟     试用 · 看流程
  2   lite      ~3 GB     800 (中证800)  ~30 分钟    日常 · 多股研报
  3   full      ~14 GB    5500+ (全 A)   1-2 小时    量化研究 / 重度
  4   skip      —         —              —           跳过, 自己配数据

  选 [1/2/3/4]: ▏
```

**新手第一次选 1 (demo)** 就够了. 5 分钟内可以下完, 跑得通整个流程. 后面用熟了再升 lite / full.

### 数据下载

会看到一堆进度条, 形如:

```
📥 下载 yifishbossman/financial-analyst-data-demo → C:\...\data
  大约 ~155 MB · 看网速 1-30 分钟

下载文件: 100%|████████████████████| 312/312 [02:48<00:00]
✓ 下载完成 (168s)
```

**如果卡住或报"连不上 huggingface.co"**: 国内直连 HF 有时候慢/被墙. 看本文末 [#常见问题 → HuggingFace 下不下来].

### 启动后端 + UI + 浏览器

数据下完后会自动:

```
✓ 工作台就绪
  Web UI:   http://127.0.0.1:5173/
  Backend:  http://127.0.0.1:9999
  日志:     .fa-launch-backend.log · .fa-launch-ui.log

浏览器自动打开. 关闭工作台: 按 Ctrl+C
```

浏览器会自己开. 没开就**手动**在浏览器地址栏输:

```
http://127.0.0.1:5173/
```

看到觀瀾的界面, 就成了!

---

## 第 7 步 · 跑第一份研报 (10 分钟)

界面下方的输入框输:

```
研报 SH600519
```

回车. 上面会显示 24 个 agent 一个个工作. 大约 **10 分钟** 后, 出一份完整的茅台研报 — 基本面 / 技术面 / 主力 / 量化 / 多空辩论 / 风险 / AI 综合判断.

> **代码格式**: SH = 上海, SZ = 深圳, BJ = 北京. 然后跟 6 位数字.
> 例: 茅台 SH600519, 比亚迪 SZ002594, 中际旭创 SZ300308, 寒武纪 SH688256.

---

## 第二天怎么用

电脑重启之后, 后端会停掉. 再开的步骤:

1. 按 Windows 键, 输 `cmd`, 回车
2. 黑窗口里输:

```cmd
fa start
```

3. 等几秒, 浏览器自动开. 完事.

之前下过的数据和配置都还在, 不需要再走一次向导。

---

## 怎么停 / 关闭

- **黑窗口里按 Ctrl + C**: 后端 + UI 都停, 浏览器页面会变成"无法连接"
- **直接关黑窗口**: 同上, 后台进程也会被干掉
- **重启电脑**: 自然停了

---

## 常见问题 (FAQ)

### Q1: 第 4 步 `pip install` 报 `SSL: CERTIFICATE_VERIFY_FAILED`

通常是公司网络 / 代理拦了. 试一次性绕过:

```cmd
pip install --trusted-host pypi.tuna.tsinghua.edu.cn financial-analyst
```

### Q2: 第 6 步数据包下载报 "连不上 huggingface.co"

国内访问 huggingface.co 有时被墙. 两种修法:

**修法 A: 用 HF 镜像** (推荐, 不需要梯子)

关掉当前 `fa start`. 黑窗口输:

```cmd
set HF_ENDPOINT=https://hf-mirror.com
fa init --preset demo
```

走完向导再:

```cmd
fa start
```

**修法 B: 挂梯子** (你有梯子的话)

如果你有 Clash / V2Ray 等代理, 开"系统代理"模式即可, fa 会走它。

### Q3: 浏览器没自动打开

手动打开你的浏览器 (Chrome / Edge / Firefox 都行), 地址栏输:

```
http://127.0.0.1:5173/
```

### Q4: 浏览器开了但页面空白 / 报错

99% 是 backend 没启动起来. 看黑窗口里有没有红字报错. 把那些红字截图发到 Issues。

### Q5: Windows Defender / 杀毒软件报警

Python 程序首次启动有时候被 Defender 怀疑. 在 Defender 设置 → 病毒和威胁防护 → 排除项 → 添加排除项 → 文件夹, 把 `C:\Users\你的用户名\.financial-analyst` 整个加白名单。

### Q6: 我电脑装过 Anaconda, 跟纯 Python 冲突吗

不冲突, 但**建议在 Anaconda 的 base 环境里直接装**:

```cmd
conda activate base
pip install financial-analyst
fa start
```

### Q7: 杀毒报 fa.exe 是病毒

没事, 是 PyInstaller 打包的 EXE 容易被误报. 加白名单即可 (参考 Q5).

### Q8: 我想换更大的数据包 / 重装

跑这个就是再走一次向导:

```cmd
fa init
```

选 lite 或 full. 老数据保留, 新数据合并下载。

### Q9: API key 怎么换 / 怎么改

办法 A: 重新走向导

```cmd
fa init
```

走到第 3 步重新粘新 key.

办法 B: 直接编辑 `.env` 文件

在你的工作目录 (默认 `C:\Users\你的用户名\.financial-analyst`) 里找到 `.env` 文件, 用记事本打开, 改 `DASHSCOPE_API_KEY=sk-xxx` 那行, 存盘. 重启 `fa start` 即可。

### Q10: 报告生成卡住 / 跑太久

正常一份研报 **8-15 分钟**. 超过 20 分钟没动静, 可能是网络不稳, 或者 LLM API 限流. 关掉黑窗口重开:

```cmd
fa start
```

再试一次.

---

## macOS 用户

macOS 自带 Python (通常 2.7, 太旧). 推荐用 **Homebrew** 装新的:

```bash
# 1. 装 Homebrew (如果没装)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# 2. 装 Python 3.12
brew install python@3.12

# 3. (国内) 换 pip 源
python3 -m pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

# 4. 装包
python3 -m pip install financial-analyst

# 5. 启动 (跟 Windows 一样)
fa start
```

其余步骤跟 Windows 一致, 但终端是 **Terminal.app** (在"应用程序 → 实用工具") 而不是 cmd。

---

## Linux 用户

```bash
# 大部分发行版自带 Python 3, 检查下:
python3 --version

# Debian/Ubuntu 装 pip:
sudo apt update && sudo apt install python3-pip

# Fedora/RHEL:
sudo dnf install python3-pip

# 换源 (国内):
python3 -m pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

# 装 + 跑:
python3 -m pip install financial-analyst
fa start
```

---

## 还是搞不定?

不丢人, 配环境对每个人都是劝退点.

**3 种求助渠道**:

1. **GitHub Issues**: https://github.com/jesson-hh/financial-analyst/issues — 把**黑窗口的完整输出**截图贴上来, 越多越好诊断
2. **直接看更详细的安装文档**: [install.md](install.md) — 含 venv / 源码安装 / Docker 等所有路径
3. **数据 + 配置发问**: [zero_to_report.md](zero_to_report.md) — 从配数据到出研报的完整 walkthrough

---

> 这份指南面向**完全不懂技术**的炒股用户. 如果你是开发者想看更技术性的部署
> (CI / Docker / 源码改造), 看 [install.md](install.md) 和 [zero_to_report.md](zero_to_report.md).

*Last updated 2026-05-25 · financial-analyst v1.0.3*
