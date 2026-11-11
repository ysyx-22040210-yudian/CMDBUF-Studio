# CMDBUF Studio

原生 **Python Tk / ttk** EDA 验证工作台，直接运行于装有 VCS、Verdi 的 **Linux 目标设备**。包含 DUT 集成、仅编辑 `main_phase` 的 SV 用例窗口、严格按已登记约束随机的回归、内置波形查看、Verdi 波形和代码覆盖率，以及 Verdi NPI 直接解析 FSDB 后生成 Formal 代码。

GUI 不依赖浏览器、HTTP 服务、Node.js 或 SSH。所有 EDA 操作经本机 Make 执行。VM 只用于开发调试，分发包不含 VM 地址或连接流程。本仓库具有独立 Git 历史，与之前的验证仓库分开维护。

![Linux 上实际运行的原生 Tk 工作台](documents/CMDBUF_Studio_Tk预览.png)

## 在 Linux 上启动

在已加载本站点 EDA 工具及许可证环境的图形桌面终端，从工程根目录执行：

```bash
python3 -c "import tkinter; print(tkinter.TkVersion)"
make doctor
make smoke
make gui
```

需要 Python ≥ 3.6、Tk ≥ 8.5、Bash、GNU Make、兼容的 C/C++ 编译器，以及 VCS / Verdi / URG 与 PLI / NPI。GUI 使用 Python 标准库，无需 pip 安装第三方包。若缺少 Tk，Ubuntu/Debian 安装 `python3-tk`，RHEL/CentOS 对应包通常为 `python3-tkinter`。

`make doctor` 检查 Tk、工具与组件；`make smoke` 真实运行 `tc_directed`（seed 1）和 `tc_random`（seed 17、42），并合并覆盖率。启动 GUI 需要有效的 `DISPLAY`。Verdi 继续使用同一图形桌面和授权。

如果需要站点环境脚本：

```bash
export EDA_TOOL_SETUP=/absolute/path/to/site_setup.sh
make gui
```

通常无需填写任何路径：“本机工具”页面自动显示工程位置、启动目录、命令路径与 PLI/NPI；当前 PATH 缺少工具时再尝试当前用户的登录环境。只有特殊站点才使用“高级选项”显式启用手动覆盖。覆盖写入已忽略的 `eda.local.json` 并绑定当前设备；旧 VM 配置、其他设备的覆盖和 `EDA_MODE=ssh` 不会自动套用。

Windows 的 `CMDBUF Studio.cmd` 或 `.\make.cmd gui` 可打开同一原生窗口，编辑工程和查看已有 VCD；EDA 任务请在 Linux 设备运行。

## GUI 功能

| 页面 | 操作 |
|---|---|
| DUT 集成 | 导入 RTL 文件或目录，维护编译顺序、include、宏定义与 `dut` 例化代码 |
| 用例编辑 | 从模板新建用例；窗口仅显示 `main_phase`，保存时保留构造函数和外层 class |
| 回归计划 | 每用例的启用、运行次数、起始 seed、周期；全局并行数、超时、波形开关 |
| 结果与波形 | 逐例结果、驱动审计、日志和覆盖率；原生波形画布、Verdi 波形与覆盖率 |
| FSDB 转 Formal | 原生文件对话框导入 FSDB，或加入回归 FSDB；层级检查、端口映射、pattern 编号和验证包导出 |
| 本机工具 | 自动检测本机环境并显示结果，默认无路径输入；特殊站点覆盖收纳在高级选项 |

用例在每次 `@(posedge clk)` 后按模板的 `#1`、控制驱动、反馈采样顺序赋值。约束写成普通 `if / else`；随机数只能在合法分支内选择。driver 检查并原样驱动，不隐式修正。时钟低/高电平与暂停、复位也由模板中的变量控制。新协议需要显式补充约束清单和检查器。

内置查看器是 Tk Canvas，保留四态数值，提供信号筛选、时间缩放、平移、A/B 游标和进制切换。FSDB 显示通过本机 `fsdb2vcd` 转换；**Formal 提取直接使用 NPI 读取 FSDB**。

每份 FSDB 对应一个 `pattern`，复位释放后逐上升沿提取，保留空拍、背压、`reset_segment` 和 `trace_cycle`。输入生成 assume，应比较的输出生成 assert；无效输出负载保留记录但不强制比较。生成 SVA 不等于已完成 Formal 证明，仍需在目标引擎初始化 DUT 并检查断言与终点 cover。

## Make 命令

```bash
make env-info
make compile
make run CASE=tc_directed SEED=17 WAVES=1
make regress SEEDS=1,2,42 CYCLES=2000 JOBS=2 WAVES=1
make wave REGRESSION=latest CASE=tc_random SEED=42
make coverage REGRESSION=latest
make wave-file FSDB=/absolute/path/to/input.fsdb
make new-case CASE=my_read TEMPLATE=tc_user_example
make dist
```

`WAVES=0` 保留日志和代码覆盖率，该轮不生成 FSDB / VCD。GUI 运行中可查看实时 Make 日志，任务结束后保留日志与结果。

## 下载与说明

- [便携 ZIP](packages/cmdbuf-studio-portable.zip) / [Linux tar.gz](packages/cmdbuf-studio-portable.tar.gz) / [SHA256](packages/SHA256SUMS.json)
- **当前 Tk 本机版操作手册**：[PDF](documents/36_cmdbuf_native_tk_20261111.pdf) / [Word](documents/36_cmdbuf_native_tk_20261111.docx)
- main_phase 详细写法：[PDF](documents/32_cmdbuf_rd_main_phase_tc_20261111.pdf) / [Word](documents/32_cmdbuf_rd_main_phase_tc_20261111.docx)
- NPI 与 Formal 语义：[PDF](documents/34_cmdbuf_fsdb_formal_20261111.pdf) / [Word](documents/34_cmdbuf_fsdb_formal_20261111.docx)

说明 33 和 35 保留为历史版本，其浏览器界面、SSH 操作流程已由 36 替代。便携包可运行 `python3 simulation/vcs/portable.py --verify` 核对文件，修改工程后通过 `make dist` 重新打包。机器配置、许可证、EDA 二进制、编译缓存和既有结果均不分发。

## 验证记录

[原生 Tk 本机版验收](verification/tk_studio_20261111/acceptance.json)记录了 Linux 上未配置环境脚本路径、未设置 VCS_HOME/VERDI_HOME 时的自动检测，以及真实窗口和本机 Make 流程：导入 DUT、只修改 main_phase、6 次带波形回归、逐例 seed 计数、内置波形、Verdi 波形和覆盖率、导入 FSDB、NPI 层级检查、双 pattern 导出，以及关闭波形后的单次回归。

新的 GUI 验证不修改之前封存的 [FSDB 与 Formal 验收](verification/fsdb_formal_20261111/acceptance.json)、[原迁移验收](verification/portable_20261111/acceptance.json) 和 [仓库完整性检查](verification/repository_20261111/acceptance.json)。旧记录对应其当时的代码和工具版本。新设备仍应执行 `make doctor` 和 `make smoke`；有限仿真与轨迹证明不代表任意输入或任意 EDA 版本均已验证。
