# Flight Analyzer — 项目开发指南

## 项目概述

CR500A 飞行数据分析桌面应用。基于 **FastAPI + pywebview** 架构：
- 后端：Python FastAPI，SQLite 存储，解析 TSV 飞行数据文件
- 前端：React + Vite + TailwindCSS + ECharts/Recharts
- 桌面壳：pywebview（Windows Edge Chromium）

## 目录结构

```
dashboard/
├── main.py              # FastAPI 应用入口 + pywebview 桌面壳
├── backend/
│   ├── database.py      # SQLite 数据库初始化与连接
│   ├── parser.py        # TSV 数据文件解析
│   └── analysis.py      # 时序对齐、统计、相关性、异常检测
├── frontend/
│   ├── src/             # React 源码
│   ├── dist/            # Vite 构建产物（打包时嵌入）
│   └── package.json
├── CR500A_FlightAnalyzer.spec  # PyInstaller 打包配置
└── requirements.txt     # 最小依赖
```

## 核心架构原则

### 动态格式识别（禁止硬编码格式枚举）

**传入任何新数据时，系统必须自动分析其实际特征，与已有机型配置比对，若无匹配则自动创建新机型。** 不再使用评分机制（`detect_format_from_content`/`_score_format_match`）强制匹配到 A/B/C。

格式识别流程（`backend/scanner.py:resolve_model_for_scan`）：
1. `generate_config_from_scan(source_path)` — 分析实际数据结构（表头、UAVSendID、列名、数据类型）
2. `compare_configs(generated, existing)` — 与所有已有机型逐项比对（结构标志 0.50 + 数据类型 Jaccard 0.30 + 列数 0.20）
3. 分数 ≥ 0.75 → 匹配，沿用该机型
4. 分数 < 0.75 → 自动创建新机型（insert aircraft_models, save_model_config, register_model_tables）
5. 前端无需手动"创建新机型"按钮

每个机型有独立的 `configs/model_{id}.json` 配置文件。旧 `format_A.json`/`format_C.json` 仅作为已有机型配置保留。

### 告警列名必须动态查询

告警表的列名必须从 `column_registry` 动态读取，不要硬编码 `alert_desc`/`extra_value`。自动生成的配置可能使用不同列名（`col_2`/`col_3`/`col_4`）。

## 开发环境

```bash
# 后端：使用 .venv 虚拟环境
.venv/Scripts/activate
pip install -r requirements.txt

# 前端
cd frontend
npm install
npm run dev          # 开发模式（Vite HMR）
```

## 打包（最小环境）

**关键原则：使用最小虚拟环境打包，避免 Anaconda 全量环境导致体积膨胀和依赖冲突。**

### 打包步骤

```bash
# 1. 构建前端
cd frontend
npm run build

# 2. 使用 .venv 中的 Python 打包（确保 .venv 已安装所有依赖）
cd ..
.venv/Scripts/pyinstaller CR500A_FlightAnalyzer.spec

# 输出：dist/CR500A_FlightAnalyzer.exe
```

### 打包修复：frozen 环境 uvicorn 日志崩溃

**问题：** PyInstaller `console=False` 时，`sys.stdout` 为 `None`，uvicorn 默认日志格式化器调用 `sys.stdout.isatty()` 抛出 `AttributeError: 'NoneType' object has no attribute 'isatty'`。

**解决：** 在 `main.py` 的 `run_server()` 中，frozen 模式下传入自定义 `log_config`，设置 `use_colors=False` 并使用 `ext://sys.stderr` 作为 handler stream。非 frozen 模式仍使用默认配置以保留颜色输出。

### 最小依赖清单 (requirements.txt)

```
fastapi>=0.100.0
uvicorn>=0.30.0
pywebview>=5.0
pyinstaller>=6.0
```

这些包的传递依赖会自动安装：pydantic, starlette, anyio, click, cffi, pythonnet, proxy_tools, bottle 等。
