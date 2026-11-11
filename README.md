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
| 回归计划 | 每用例的启用、运行次数、起始 seed、周期；检查模式、断言 fatal 开关、并行数、超时、波形开关 |
| 结果与波形 | 分别显示接口、事务、完整时序结论；失败原拍号、完整快照、日志、波形与覆盖率 |
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
make regress CHECK_MODE=transaction ASSERT_FATAL=0 SEEDS=1,2,42
make run CASE=tc_directed CHECK_MODE=strict ASSERT_FATAL=1
make check-test JOBS=2
make wave REGRESSION=latest CASE=tc_random SEED=42
make coverage REGRESSION=latest
make wave-file FSDB=/absolute/path/to/input.fsdb
make new-case CASE=my_read TEMPLATE=tc_user_example
make dist
```

`WAVES=0` 保留日志和代码覆盖率，该轮不生成 FSDB / VCD。GUI 运行中可查看实时 Make 日志，任务结束后保留日志与结果。

`make wave`、`make wave-file` 及对应 GUI 按钮会自动加入 DUT 顶层全部已记录的信号，包括端口、内部信号和数组，并缩放到完整时间范围。信号列表直接来自 FSDB，更换 RTL 后无需维护固定信号名。外部 FSDB 可指定 `DUT_SCOPE=top.u_cmdbuf`；GUI 使用当前“实例”栏。加载后会核对完整清单，错误实例路径明确报错。操作补充见说明 40。

所有 VCS 编译自动加入 `-lca -licqueue +vcs+lic+wait`；仿真自动加入 `+vcs+lic+wait`，URG 合并自动加入 `+urg+lic+wait`。等待 License 不设超时上限，编译和合并进程也不再使用外层固定超时。`TIMEOUT=120` 只从 VCS 的仿真启动回调之后计算执行时间；`TIMEOUT=0` 表示执行也不限时。GUI 显示等待/启动及执行状态，结果中的 `sim.process.json` 分别记录启动等待和执行耗时。旧归档二进制没有启动回调时，重放保留原二进制并关闭整个进程的超时，以免中断许可证等待。

当前 SV 用例从 t=0 启动时钟，`clk` 初始为 1。`rst_n` 在 t=0 为 0，在 `#1`（1 ns）释放后一直为 1。默认 5 ns 下降、10 ns 上升；`main_phase` 的 `cycle=0` 在 12 ns 驱动，供 20 ns 采样。TC 保持 `rst_n=1`，再次请求拉低会报错。`clock_reset.csv` 记录实际时钟和复位跳变；旧归档重放沿用其原二进制和原时序。说明 38 覆盖旧说明中的多周期复位及用例中再次复位示例。

新计划和无计划的命令行运行默认采用通用事务模式。它保存完整沿前四态快照，按实际握手检查事务、数据和单源顺序；同地址发射保留所有相容的事务对应关系。MEM 输入仍遵守 N+4 契约。pending 更新时点、精确 ready/req/R 调度和最早 N+5 返回显示 `NOT_CHECKED`，不把通用模式通过当作完整时序通过。事务未结束或对应关系候选超过上限时，结果为 `INCOMPLETE`。

`CHECK_MODE=strict` 执行 20261021 全部原生 SVA，保留组合 pending 和精确调度要求。旧保存计划没有新字段时继续使用 strict；Make 显式参数优先于计划。`ASSERT_FATAL=0` 使用 `$error` 继续记录断言失败，最终仍判 `FAIL`，失败用例不合并进通过覆盖率。TC 非法赋值、驱动时点、文件和工具错误仍立即终止。`make replay` 保留原运行的模式与 fatal 选择。运行记录增加 `check_frames.csv`、`assertions.jsonl`、`check_runtime.json` 和 `check_scope.json`。

## 下载与说明

- [Verdi 自动加载 DUT 顶层信号 PDF](documents/40_cmdbuf_verdi_dut_signals_20261111.pdf)
- [License 等待与执行超时 PDF](documents/39_cmdbuf_license_wait_20261111.pdf) / [Word](documents/39_cmdbuf_license_wait_20261111.docx)
- [时钟与复位启动时序 PDF](documents/38_cmdbuf_startup_20261111.pdf) / [Word](documents/38_cmdbuf_startup_20261111.docx)
- [检查模式与 fatal 操作说明 PDF](documents/37_cmdbuf_check_modes_20261111.pdf) / [Word](documents/37_cmdbuf_check_modes_20261111.docx)
- [便携 ZIP](packages/cmdbuf-studio-portable.zip) / [Linux tar.gz](packages/cmdbuf-studio-portable.tar.gz) / [SHA256](packages/SHA256SUMS.json)
- **当前 Tk 本机版操作手册**：[PDF](documents/36_cmdbuf_native_tk_20261111.pdf) / [Word](documents/36_cmdbuf_native_tk_20261111.docx)
- main_phase 详细写法：[PDF](documents/32_cmdbuf_rd_main_phase_tc_20261111.pdf) / [Word](documents/32_cmdbuf_rd_main_phase_tc_20261111.docx)
- NPI 与 Formal 语义：[PDF](documents/34_cmdbuf_fsdb_formal_20261111.pdf) / [Word](documents/34_cmdbuf_fsdb_formal_20261111.docx)

说明 33 和 35 保留为历史版本，其浏览器界面、SSH 操作流程已由 36 替代。便携包可运行 `python3 simulation/vcs/portable.py --verify` 核对文件，修改工程后通过 `make dist` 重新打包。机器配置、许可证、EDA 二进制、编译缓存和既有结果均不分发。

## 验证记录

[License 等待验收](verification/license_wait_20261111/release.json)包含真实 VCS / URG 命令参数、启动后计时、零执行超时、新旧归档重放、并行等待取消，以及中文带空格目录中的便携包和 Tk 回归操作。延迟启动测试验证等待超过执行期限仍能通过；没有人为耗尽或修改许可证服务器。

[原生 Tk 本机版验收](verification/tk_studio_20261111/acceptance.json)记录了 Linux 上未配置环境脚本路径、未设置 VCS_HOME/VERDI_HOME 时的自动检测，以及真实窗口和本机 Make 流程：导入 DUT、只修改 main_phase、6 次带波形回归、逐例 seed 计数、内置波形、Verdi 波形和覆盖率、导入 FSDB、NPI 层级检查、双 pattern 导出，以及关闭波形后的单次回归。

新的 GUI 验证不修改之前封存的 [FSDB 与 Formal 验收](verification/fsdb_formal_20261111/acceptance.json)、[原迁移验收](verification/portable_20261111/acceptance.json) 和 [仓库完整性检查](verification/repository_20261111/acceptance.json)。旧记录对应其当时的代码和工具版本。新设备仍应执行 `make doctor` 和 `make smoke`；有限仿真与轨迹证明不代表任意输入或任意 EDA 版本均已验证。
