# Flight Analyzer — 项目开发指南

## 项目概述

飞行数据分析应用：FastAPI 后端 + React 前端 + pywebview 桌面壳。

当前处于中期重构，权威需求与架构以 `docs/plan.md` 为准；历史架构决策（动态格式识别、科研网/外场双模式、原始文件归档、bundle 同步）见项目 memory。



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

也可用根目录 `run.sh` / `run.bat` / `run.ps1` 启动。

## 打包

**最小依赖原则：用** `.venv` **打包，避免 Anaconda 全量环境导致体积膨胀和依赖冲突。**

```bash
cd frontend && npm run build && cd ..
.venv/Scripts/pyinstaller FlightAnalyzer.spec     # 输出 dist/FlightAnalyzer.exe
```

frozen 模式（`console=False`）下 uvicorn 日志会因 `sys.stdout` 为 `None` 崩溃，修复见 `main.py:_build_log_config()` 的 docstring。