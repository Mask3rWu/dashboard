# CR500A 飞行数据结构分析

> 分析日期：2026-06-02
> 数据源：`20250323153351_535/`（2025年3月23日飞行任务）

---

## 一、数据源概览

本次数据来自 **CR500A 无人机飞行数据记录系统**，包含 **3 架无人机**（编号 UAV5/UAV21/UAV24）在 2025年3月23日的飞行数据。

### 目录结构

```
data/20250323153351_535/
├── MouseOperationRecord.txt.txt          # 地面站鼠标操作记录
├── {DroneID}/                            # 每架无人机一个文件夹（5/21/24）
│   ├── AllFlightData/                    # 原始全量接收数据（hex 二进制流）
│   │   └── {ID}AllReceivedData_{timestamp}_{seq}.txt
│   ├── FlightAlertInfo/                  # 飞行告警日志
│   │   └── {ID}FlightAlertInfo_{timestamp}.txt
│   ├── HandlePacket/                     # 解码后的数据包流（按包类型标识）
│   │   └── {ID}HandlePacket_{timestamp}_{seq}.txt
│   ├── SendCommand/                      # 地面站发送的指令（hex 编码）
│   │   └── {ID}SendCommand_{timestamp}_{seq}.txt
│   └── ParserData/                       # ★ 按主题分类解析后的结构化数据（核心）
│       ├── {ID}DroneStateData_{ts}_{seq}.txt      # 无人机状态（~7Hz）
│       ├── {ID}PosData_{ts}_{seq}.txt             # 位置综合数据（~7Hz）
│       ├── {ID}GPSData_{ts}_{seq}.txt             # 双天线 GPS 数据（~1Hz）
│       ├── {ID}IMUData_{ts}_{seq}.txt             # 惯性导航数据（~1Hz）
│       ├── {ID}EngineData_{ts}_{seq}.txt          # 发动机数据（~1Hz）
│       ├── {ID}DualAntennaData_{ts}_{seq}.txt     # 双天线差分导航（~1Hz）
│       └── {ID}PowerBoxData_{ts}_{seq}.txt        # 电源箱状态（~1Hz）
```

### 文件命名规则

```
{DroneID}{DataType}_{Timestamp}_{SequenceNumber}.txt
```
- `DroneID`：无人机编号（5/21/24）
- `DataType`：数据类型（如 GPSData、IMUData 等）
- `Timestamp`：记录起始时间（格式 `HHmmss`，如 153351 = 15:33:51）
- `SequenceNumber`：序列号

---

## 二、数据量统计

| 数据类别 | UAV21 | UAV24 | UAV5 | 采样频率 |
|---------|-------|-------|------|---------|
| DroneStateData | 18,732 行 | 17,257 行 | - | ~7 Hz |
| PosData | 18,730 行 | 17,258 行 | - | ~7 Hz |
| GPSData | 3,749 行 | 3,452 行 | - | ~1 Hz |
| IMUData | 3,749 行 | 3,453 行 | - | ~1 Hz |
| EngineData | 3,748 行 | 3,453 行 | - | ~1 Hz |
| DualAntennaData | 3,747 行 | 3,452 行 | - | ~1 Hz |
| PowerBoxData | 3,746 行 | 3,452 行 | - | ~1 Hz |
| FlightAlertInfo | 12,058 行 | 10,238 行 | - | 实时触发 |
| SendCommand | 32,660 行 | 32,655 行 | 1 行 | 实时触发 |
| HandlePacket | 56,265 行 | 51,831 行 | - | 实时触发 |
| AllFlightData | 17,763 行 | 16,277 行 | - | 原始二进制流 |

**飞行时长估算**：
- UAV21：约 **62 分钟**（3,749秒 @ 1Hz 数据推算）
- UAV24：约 **57 分钟**（3,452秒 @ 1Hz 数据推算）
- UAV5：几乎无有效数据（仅 1 行 SendCommand）

**数据规模**：5Hz 量级，单次数据记录约 100+ 字节/条，飞行时长半小时到四五小时不等。

---

## 三、各数据文件字段详解

所有文件采用 **Tab 分隔**（TSV 格式），首行为表头，每行一条记录。

### 3.1 GPSData — 双天线 GPS 数据（~1Hz）

| 字段 | 含义 | 单位 |
|------|------|------|
| Time | 记录时间 | HH:MM:SS |
| UAVSendID | 无人机标识 | - |
| 北向速度 / 东向速度 / 地向速度 | 三维速度分量 | m/s |
| NavA_Lat / NavA_Lng / NavA_Alt | 天线A 纬度/经度/高度 | °/°/m |
| NavB_Lat / NavB_Lng / NavB_Alt | 天线B 纬度/经度/高度 | °/°/m |
| GPSBVelN / GPSBVelE / GPSBVelD | 天线B 三维速度 | m/s |
| 位置精度 / 速度精度 | 精度指标 | - |
| 气压 / 气压高度 | 气压及对应高度 | Pa / m |
| 航向角 | 基于双天线计算的航向 | ° |
| PDOP / HDOP | 位置/水平精度因子 | - |
| 天线基线长度 | 两天线间距 | mm |
| 更新频率 | 数据更新频率 | Hz |

### 3.2 IMUData — 惯性导航数据（~1Hz）

| 字段 | 含义 | 单位 |
|------|------|------|
| Time | 记录时间 | HH:MM:SS |
| NavA_Roll / NavA_Pitch / NavA_Yaw | 天线A 横滚/俯仰/偏航角 | ° |
| Vx / Vy / Vz | 天线A 三轴速度 | m/s |
| Ax / Ay / Az | 天线A 三轴加速度 | m/s² |
| NavB_Roll / NavB_Pitch / NavB_Yaw | 天线B 横滚/俯仰/偏航角 | ° |
| NavB_Vx / NavB_Vy / NavB_Vz | 天线B 三轴速度 | m/s |
| NavB_Ax / NavB_Ay / NavB_Az | 天线B 三轴加速度 | m/s² |
| AxExtremum / AyExtremum / AzExtremum | 三轴加速度极值 | m/s² |

### 3.3 DroneStateData — 无人机核心状态（~7Hz）

| 字段 | 含义 | 单位 |
|------|------|------|
| Time | 记录时间 | HH:MM:SS |
| UpLinkCommType / DownLinkCommType | 上下行通信链路类型 | - |
| 横滚角 / 俯仰角 / 航向角 | 实时姿态角 | ° |
| 前向速度 / 侧向速度 / 地向速度 | 三维速度 | m/s |
| 前向目标速度 / 侧向目标速度 / 地向目标速度 | 目标速度指令 | m/s |
| 目标偏航角 / 目标俯仰角 | 目标姿态指令 | ° |
| 偏航角速度 | 转弯速率 | °/s |
| 目标偏航角速度 / 目标高度 | 角速度指令与高度指令 | °/s, m |
| 电量(%) / 舵机电量(%) | 剩余电量百分比 | % |
| 链路质量(%) | 通信链路质量 | % |
| 链路切换数量 | 链路切换次数 | - |
| 飞行模式 | 飞行模式字 | - |
| 飞行时长 / 剩余飞行时间 | 已飞/剩余时间 | min |

### 3.4 PosData — 位置综合数据（~7Hz）

| 字段 | 含义 | 单位 |
|------|------|------|
| Time | 记录时间 | HH:MM:SS |
| 北向位置 / 东向位置 | 相对起飞点位置 | m |
| 相对高度 | 相对起飞点高度 | m |
| 经度 / 纬度 | GPS 坐标 | ° |
| 海拔高度 | 海拔高度 | m |
| Home点距离 | 距起飞点直线距离 | - |
| 飞控电压 | 飞控供电电压 | V |
| IsNavAOnline / IsNavBOnline / IsGCSOnline | 各子系统在线状态 | True/False |
| IsPowerBoxOnline / IsECUOnline / IsTCUOnline | 电源/发动机/TCU在线状态 | True/False |
| IsServo1~6Online | 各舵机在线状态 | True/False |
| 飞行模式 | 飞行模式代码+名称 | - |
| 记录仪状态 | 数据记录器状态 | - |
| 目标航线 / 偏航距 | 目标航线编号与偏差 | - |
| NaviA状态 / NaviB状态 | 导航系统工作状态 | - |
| GPSTimeH / GPSTimeM / GPSTimeS | GPS授时 | 时/分/秒 |
| PDOP | 位置精度因子 | - |
| PreWarningDronePosFlag | 位置预警标志 | - |
| PreWarningCountryBoundaryFlag | 国境线预警 | - |

### 3.5 EngineData — 发动机数据（~1Hz）

| 字段 | 含义 | 单位 |
|------|------|------|
| Time | 记录时间 | HH:MM:SS |
| 缸头温度 | 气缸头温度 | °C |
| 排气温度 1/2 | 两路排气温度 | °C |
| 发动机温度 | 发动机本体温度 | °C |
| 进气温度 1/2/3/4 | 四路进气温度 | °C |
| 发动机转速 | 当前转速 | RPM |
| 节气门开度 | 油门开度 | % |
| 进气歧管压力 | 歧管压力 | mbar |
| 剩余燃油 | 剩余燃油量 | L |
| 电池电压 | 供电电压 | V |
| TCU温度 | TCU模块温度 | °C |
| TCU进气歧管压力 | TCU侧歧管压力 | mbar |

### 3.6 PowerBoxData — 电源箱状态（~1Hz）

| 字段 | 含义 | 单位 |
|------|------|------|
| Time | 记录时间 | HH:MM:SS |
| 飞控电压 / 舵机电压 / 接收机电压 / 电池电压 | 各回路电压 | V |
| 飞控电流 / 舵机电流 / 接收机电流 / 电池电流 | 各回路电流 | mA |
| 12V电压 / 28V电压 | 系统供电电压 | V |
| 舵机电流（备用） | 备用舵机电流 | mA |

### 3.7 DualAntennaData — 双天线差分导航（~1Hz）

| 字段 | 含义 |
|------|------|
| Time / UAVSendID | 时间与无人机标识 |
| GPSPosType / RadioPosFlag / PaAltFlag / MagHeadingFlag / GPSDataFlag | 各类数据有效性标志 |
| PDOP_Diff / HDOP_Diff / SatNum_Diff | 差分GPS精度与卫星数 |
| 位置更新频率 / 速度更新频率 / 状态更新频率 | 各数据更新率 |
| 气压(Pa) / 气压温度 / 气压高度传感器 | 气压传感数据 |
| 修正气压 / 修正气压温度 | 修正后气压数据 |

### 3.8 FlightAlertInfo — 飞行告警日志

格式：`Time  UAV_ID  无人机型号  告警描述  [附加数值]`

常见告警类型：
- **当前距离Home点 >= 100km**（伴随 Home 点距离值）
- **某路遥控中断**（链路断开告警）

### 3.9 SendCommand — 地面站指令

格式：`Time  GCS_ID  指令类型(DRONE_STATE/POSITION/...)  Hex数据...`

以 hex 编码记录地面站向无人机发送的控制指令，包含指令类型标识和数据负载。

### 3.10 HandlePacket — 解码数据包

格式：`Time  UAV_ID  包类型(DRONE_STATE/POSITION/ENGINE_CONTROLLER_STATUS/...)  Hex数据...`

按包类型分类的已解码下行数据包流，是 ParserData 的原始来源。

### 3.11 AllFlightData — 原始全量数据

纯 hex 二进制流，为最原始的接收数据，未做任何解析。

---

## 四、数据特征总结

### 4.1 数据关联关系

```
AllFlightData (原始hex流)
    └── HandlePacket (按包类型分拣)
            └── ParserData (按主题解析，结构化)
                    ├── DroneStateData  ← 核心飞行状态
                    ├── PosData         ← 综合位置与系统状态
                    ├── GPSData         ← 双天线定位
                    ├── IMUData         ← 惯性测量
                    ├── EngineData      ← 动力系统
                    ├── DualAntennaData ← 差分导航
                    └── PowerBoxData    ← 供电系统
SendCommand (地面站→无人机)
FlightAlertInfo (告警)
MouseOperationRecord (地面站操作)
```

**关联键**：`Time`（时间戳）是跨表关联的主键，不同数据源采样率不同（1Hz/7Hz），需通过时间对齐合并。

### 4.2 数据等级

| 等级 | 数据 | 说明 |
|------|------|------|
| L0 原始 | AllFlightData | 未解析的 hex 二进制流 |
| L1 解码 | HandlePacket | 按包类型分拣后的 hex 数据 |
| L2 解析 | ParserData/ | 结构化的 TSV 数据，可直接使用 |
| L3 告警/操作 | FlightAlertInfo / MouseOperationRecord | 上下文事件 |

> **核心使用层为 L2 ParserData**，是日常分析的主要数据来源。

### 4.3 采样率差异

- **~7Hz**：DroneStateData、PosData（约 140ms/条）
- **~1Hz**：GPSData、IMUData、EngineData、DualAntennaData、PowerBoxData（约 1s/条）
- **实时触发**：FlightAlertInfo、SendCommand、HandlePacket

---

## 五、可开发功能规划

### 5.1 单次飞行数据解析与可视化

| 功能模块 | 详细说明 |
|---------|---------|
| 数据导入 | 拖拽/选择文件夹，自动识别无人机编号和数据类型，解析 TSV 文件 |
| 飞行仪表盘 | 姿态仪表（横滚/俯仰/偏航）、速度表、高度表、航向罗盘 |
| 2D/3D 轨迹 | 基于经纬度+高度绘制飞行轨迹，支持缩放、回放 |
| 时间序列曲线 | 任意参数随时间变化的折线图，支持多选通道叠加对比 |
| 告警时间线 | 飞行告警事件按时间轴排列，点击跳转到对应时刻 |
| 系统状态面板 | 各子系统在线状态（NavA/B/GCS/舵机等），绿色/红色状态灯 |
| 数据表格 | 原始数据表格查看，支持排序、筛选、导出 |

### 5.2 多次飞行数据汇总分析

| 功能模块 | 详细说明 |
|---------|---------|
| 飞行目录管理 | 管理多个飞行数据文件夹，按无人机/日期组织 |
| 飞行统计摘要 | 每架次：总时长、最大高度/速度、总油耗、告警次数等 |
| 多飞行对比 | 同一指标跨飞行对比曲线 |
| 轨迹叠加 | 多架次飞行轨迹在同一地图/图表叠加显示 |

### 5.3 数据挖掘（扩展）

| 功能模块 | 详细说明 |
|---------|---------|
| 多维数据透视 | 将不同采样率的数据按时间对齐到统一表格，自由选择数据列 |
| 交叉关联分析 | 计算不同数据通道间的 Pearson/Spearman 相关系数矩阵并热力图展示 |
| 异常检测 | 基于滑动窗口+3σ阈值检测数据异常点并标记 |
| 飞行阶段识别 | 自动分割：起飞滑跑/爬升/巡航/降落（基于高度、速度模式） |
| 油耗模型 | 分析油门开度-转速-油耗关系曲线 |
| 数据导出 | 将对齐后的数据导出为 CSV/Excel |

---

## 六、技术架构（待定）

> 目标：封装为 Windows .exe 软件，个人本地使用。
> 待评估方案后确定。
