# Flight Analyzer — 项目开发指南

## 项目概述

飞行数据分析桌面应用。基于 **FastAPI + pywebview** 架构：

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
├── FlightAnalyzer.spec  # PyInstaller 打包配置
└── requirements.txt     # 最小依赖
```

## 核心架构原则

### 动态格式识别（禁止硬编码类型白名单）

**所有 `.txt` 文件类型一律平等——导入后才被系统认识，不硬编码额外处理。** 没有 `KNOWN_TYPES` 白名单。已知类型与未来未知类型走同一条流水线（发现 → 派生 key → 建表 → 比对）。

关键点：
- 类型 key 从文件名**确定性派生**（`_sanitize_data_type_key`，如 `DroneStateData` → `dronestatedata`），标签 = 文件名原文。
- 文件名开头的飞机序号前缀（`21DroneStateData`/`24DroneStateData`）在 `_discover_file_patterns` 中被剥离（`re.sub(r'^\d+(?=[A-Z])', '', name)`），同机型的多架飞机文件合并为一个类型。
- `is_alert` 从文件名内容派生（`'alert' in name.lower()`），不是列表查表。告警端点按 `data_table_registry.is_alert=1` 动态查表（`_get_alert_data_type`）。
- `compare_configs` 用 **recall**（`|交集|/|已有机型类型数|`），生成端多出的未知类型不进分母——这是"接纳其他 .txt"的前提。阈值 0.95。结构标志（`has_header`/`has_uav_send_id` 一致性）+ 列数一致性作为辅助维度。

格式识别流程（`backend/scanner.py:resolve_model_for_scan`）：

1. `generate_config_from_scan(source_path)` — 分析实际数据结构，附带 `is_raw` 标记（内容驱动：hex-token 占比 ≥ 0.85 或 C0 控制字符超阈值 → 疑似原始字节转储）。
2. `compare_configs(generated, existing)` — 与所有已有机型逐项比对（recall 0.60 + 结构标志 0.25 + 列数 0.15）。
3. 分数 ≥ 0.95 → 匹配，沿用该机型。
4. 分数 < 0.95 → 返回候选类型列表（含 `is_raw` 标记），**预览不写库**。前端弹创建框：用户填机型名 + 勾选类型（`is_raw` 默认不勾选，带"原始数据"徽章），确认后才 `create_model_from_scan` 写库（只注册勾选类型）。

### 原始字节转储判别（is_raw）

不是文件名白名单，是结构化内容分析。`_is_raw_dump(filepath)` 用两个信号：
- NUL 字节 / C0 控制字符占比 > 10%（真二进制）
- 采样行中 1-2 位 hex token 占比 ≥ 0.85（十六进制字节转储，如 HandlePacket/AllReceivedData/SendCommand）

仅用于新建机型时的默认勾选（raw 默认不选，用户可手动勾上），不影响任何导入/比对逻辑。

### 机型与飞机的数据模型

- `aircraft_models`：存 `name`、`has_header`、`has_uav_send_id`、`extract_serial_from_path`。**没有 `format_category` 字段**（已删除——它是遗留占位符，语义已退化）。
- `aircraft`：`name` 字段存储飞机标识（自定义字符串，从目录结构提取或用户输入）。**没有 `serial_number` 字段**（已重命名为 `name`——它本就是自定义字符串，非数字序号）。
- `flights`：`source_path` 保留用于数据溯源（后续转存功能可能用到），但**不参与查重**。查重边界 = `aircraft_id + flight_date + session_key`（`UNIQUE` 约束）。

### 导入不使用新生配置扫描

序列前缀已剥离，`get_data_type_key` 正则可匹配任何序号前缀，所以导入现在全程用**机型存储配置**扫描和导入（不再另调 `generate_config_from_scan`）。用户取消勾选的类型不在存储配置的 `file_patterns` 中 → 不会被扫描 → imports 无 0 行噪声。

### 告警列名必须动态查询

告警表的列名必须从 `column_registry` 动态读取，不要硬编码 `alert_desc`/`extra_value`。自动生成的配置可能使用不同列名（`col_2`/`col_3`/`col_4`）。告警表找法：按 `is_alert=1` 查 `data_table_registry`（`_get_alert_data_type`），不认 `'alert'` key。

### 已接受的取舍

- 领域统计卡片（最高高度/最大速度/转速/油量/电量）已移除——它们靠硬编码类型 key + 列名，与平等原则冲突。要重建须用语义角色机制。
- 类型标签默认为文件名（如 `DroneStateData` 而非中文 `飞控状态`）——用户可通过 API 编辑 `data_table_registry.display_label`。
- Schema v9。`migrate_v2..v7.py` 已删除，`init_db` 在版本不匹配时自动备份并重建。

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
.venv/Scripts/pyinstaller FlightAnalyzer.spec

# 输出：dist/FlightAnalyzer.exe
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