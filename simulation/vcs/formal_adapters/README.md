# test_mode Studio 商业 Formal 对接指南

在安装商业 Formal 和许可证的 Linux 设备上启动 Studio。站点适配器负责调用本机商业工具。Studio 负责冻结 RTL 与 SVA、生成请求、归档原始报告和显示逐属性结果。适配器可复用站点已经验证的批处理脚本。

## 在 EDA 设备上接入

1. 在同一终端加载站点的工具环境，先用该设备的模板确认商业 Formal 能完成工具启动和许可签出。
2. 准备可执行的站点适配器。在「Formal 验证 → 商业 Formal」选择它。也可在「本机工具 → 高级选项」保存当前机器的路径，或者设定 `EDA_COMMERCIAL_FORMAL_ADAPTER`。
3. 选择 DUT Cover、DUT Assert / BMC 或 FSDB 验证包。DUT 任务使用当前已保存的 DUT 配置和端口检查器，GUI 会同步属性编辑器内容。FSDB 任务接受已解压的包，也可以使用「验证包证明」选中的包。
4. 先点「只准备任务」，查看请求的顶层、文件顺序、时钟和复位。确认后点「运行商业 Formal」。任务页可查看属性结论、日志和适配器返回的 VCD。

```bash
export EDA_COMMERCIAL_FORMAL_ADAPTER=/site/bin/studio-formal
make formal-commercial COMMERCIAL_MODE=cover \
  COMMERCIAL_TARGETS=C_PARITY_R COMMERCIAL_DEPTH=48 \
  COMMERCIAL_DEFINES=SYNTHESIS
make formal-commercial COMMERCIAL_MODE=bmc \
  COMMERCIAL_TARGETS=A_R_READY COMMERCIAL_DEPTH=16 \
  COMMERCIAL_DEFINES=SYNTHESIS
make formal-commercial COMMERCIAL_MODE=prove \
  COMMERCIAL_PACKAGE=/work/exported-fsdb-package \
  COMMERCIAL_DEFINES=SYNTHESIS
```

`COMMERCIAL_TARGETS` 用逗号分隔属性源码标签或完整实例名，留空检查该模式的全部属性。`prove` 按包的原生断言与终点运行，深度留空时取包内推荐值。DUT 默认 48 拍。`COMMERCIAL_TIMEOUT` 是适配器传给求解阶段的秒数，默认 600，0 代表不限时。

没有商业工具的设备可以准备任务。运行 `make formal-commercial COMMERCIAL_PREPARE=1 COMMERCIAL_RUN_ID=connect_check`，或者直接使用项目 Python 执行以下命令。

```bash
python3 simulation/vcs/commercial_formal.py \
  --mode cover --targets C_PARITY_R --prepare
```

<!-- pagebreak -->

## 给实现适配器的 agent

在站点目录实现一个能从终端启动的可执行程序。开始前核对工具、版本、许可与原生属性查询方式。用安装版本所附的命令帮助、批处理模板和认证结果作为映射依据。

```text
adapter /absolute/job/request.json /absolute/job/adapter-result.json
```

Studio 以任务目录为工作目录，按参数数组启动程序。日志保存在 `adapter.log`。默认继承启动终端的环境，配置了站点环境脚本时先加载它。把许可证配置留在站点环境。

| 请求字段 | 适配器如何使用 |
|---|---|
| `schema`、`run_id` | 协议是 `test_mode.commercial_formal.v1`。原样回填运行编号，计算整个请求文件原始字节的 SHA-256。 |
| `inputs` | 从请求目录下的 `source/` 读取输入。`files` 是编译顺序，`include_dirs` 和 `defines` 单独传递，也可加载 `files.f`。`sha256` 列出快照摘要。 |
| `design` | 按 `top` 展开，以 `clock` 的上升沿采样。先真正施加低有效复位 `reset`，完成初始化后释放。`initialize_reset` 表示必须执行初始化。 |
| `mode`、`kinds` | `cover` 选 Cover。`bmc` 有界检查 Assert。`prove` 验证 FSDB 包的 Assert 和所有终点 Cover。始终保留所有原生 Assume 的环境角色。 |
| `targets`、`required_labels` | 对明确目标精确匹配完整实例名或源码标签。一个标签展开到多个实例时核对全部匹配项。FSDB 包还须报告列出的断言标签和每个终点。 |
| `depth`、`solve_timeout_seconds` | 深度按复位释放后的采样拍计。超时设置在商业引擎求解阶段，许可排队由站点工具管理。 |

DUT 快照会保留 RTL 中的宏、命名序列和原生 SVA。`port_checks` 是端口环境检查器实例，属性编辑器中的声明在 wrapper 内引入。FSDB 包使用 `trace_properties.sv`，`pattern` 与 `reset_segment` 按包内原生约束选择轨迹。`disable iff` 只控制属性使能，初始化必须由工具真正执行。

基准 DUT 用 `SYNTHESIS` 宏隔离包含 `$stop` 的仿真诊断。可在 GUI 的「附加编译宏」填写它，或通过 `COMMERCIAL_DEFINES` 传入。Studio 将附加宏记录在请求和 filelist 中。接入其他 RTL 时，按源码的预处理条件选宏，确认目标 SVA 展开后仍然存在。

站点脚本的工作库、报表和波形写到任务目录。需要写入编译输入时，先复制 `source/` 到工作目录，再按原样加载。Studio 会在读取结果前核对请求和输入摘要。

<!-- pagebreak -->

## 回传逐属性结果

适配器将结果原子写入第二个参数指定的 JSON 文件。退出码 0 表示已写出结构化结果。正常发现反例、深度内未命中或求解超时，也应返回各属性的实际状态。启动、许可或编译失败要让本次任务失败，并在日志记录原生诊断。

```json
{
  "schema": "test_mode.commercial_formal.v1",
  "run_id": "connect_check",
  "request_sha256": "<64-character SHA-256 of request.json>",
  "engine": {"name": "<tool name>", "version": "<installed version>"},
  "complete": true,
  "properties": [
    {
      "kind": "cover",
      "label": "C_PARITY_R",
      "name": "test_mode_commercial_formal_top.C_PARITY_R",
      "status": "REACHED",
      "depth": 18,
      "trace": "waves/parity.vcd"
    }
  ],
  "reports": ["vendor-summary.txt"]
}
```

`complete` 为真表示选定属性已完整展开、全部获得状态。每个属性使用唯一的完整实例名和原生源码标签。`trace` 可省略或填 `null`，提供时须为任务目录内的相对 VCD 路径。商业工具专用数据库可通过 `reports` 留档。三个有界状态 `REACHED`、`UNREACHED_IN_BOUND`、`BOUNDED_PASS` 必须提供实际深度。

| 工具证据 | v1 状态 |
|---|---|
| Assert 有无界证明、完成指定深度或找到反例 | `PROVEN`、`BOUNDED_PASS` 或 `COUNTEREXAMPLE` |
| Assert 仅空触发 | `VACUOUS` |
| Cover 命中、只完成有界搜索或具有不可达证明 | `REACHED`、`UNREACHED_IN_BOUND` 或 `UNREACHABLE` |
| 求解停在未知、超时或明确错误 | `UNKNOWN`、`TIMEOUT` 或 `ERROR` |

Studio 将真实断言反例标为 `FAIL`。通过要求完整结果、必需标签齐全、目标 Cover 命中，以及断言已证明或达到请求深度。空触发、浅于请求的有界结论、缺失终点和未完成求解会显示为 `INCOMPLETE`。以任务里的 `result.json` 为最终判断依据。商业结果页可查看 `trace` 指向的 VCD。将命中轨迹转换为 VCS 激励的方法见下文。

<!-- pagebreak -->

## 复用站点报告适配器

仓库附带 `site_adapter.py`，可把站点驱动生成的 TSV 报告封装为 v1 JSON。agent 只需对接本地商业命令、选择原生属性，并按下面的格式输出真实结论。

```bash
chmod +x simulation/vcs/formal_adapters/site_adapter.py
export EDA_COMMERCIAL_FORMAL_ADAPTER="$PWD/simulation/vcs/formal_adapters/site_adapter.py"
export EDA_COMMERCIAL_FORMAL_DRIVER=/site/bin/vendor-formal-driver
make formal-commercial COMMERCIAL_MODE=cover COMMERCIAL_TARGETS=C_PARITY_R \
  COMMERCIAL_DEFINES=SYNTHESIS
```

样例适配器调用 `vendor-formal-driver REQUEST_JSON SITE_RESULT_TSV`。TSV 使用 UTF-8 和真实制表符分隔。引擎和完整性记录各一行，属性记录每个实例一行。Python 驱动可以用 `csv.writer(delimiter="\t")`，Tcl 驱动可用列表拼接制表符。

| 首列 | 后续列顺序 |
|---|---|
| `engine` | 工具名称、安装版本 |
| `complete` | `1` 或 `0` |
| `property` | 类型、源码标签、完整实例名、状态、深度、VCD 相对路径。允许省略值的列仍保留制表符。 |
| `report` | 任务目录内的文件相对路径，可重复 |
| `message` | 可选的一行简短说明 |

### 厂商工具落地顺序

- **Synopsys VC Formal FPV**。从站点现有 FPV 批处理入口开始，核对版本的 RTL 读取、时钟和复位命令，再用正式属性查询接口获取状态和深度。[VC Formal 官方资料](https://www.synopsys.com/verification/static-and-formal-verification/vc-formal.html)
- **Cadence Jasper FPV**。沿用站点 Jasper 批处理方式。确认 analyze、elaborate、clock 和 reset 对应请求中的编译顺序和实际时钟复位，再查询目标属性。[Jasper FPV 官方资料](https://www.cadence.com/en_US/home/tools/system-design-and-verification/formal-and-static-verification/jasper-core-formal-apps.html)
- **Siemens Questa Formal**。选择站点已有的属性验证流程，核对本版编译、时钟复位和属性导出接口，将完整实例名归一到同一协议。[Questa Formal 官方资料](https://eda.sw.siemens.com/en-US/ic/questa/formal-verification/)

将厂商脚本和适配器分开保存，工具升级时优先重测编译、属性查询和状态映射。接口端负责校验结果完整性，商业工具的实际支持范围以站点安装版本和验收为准。

<!-- pagebreak -->

## 在有许可证的站点验收

1. 先做 `COMMERCIAL_PREPARE=1`。核对 RTL、Include、defines、top、真实复位和原生属性源码，保存成功请求作为站点模板。
2. 用同一适配器跑一个已知可达 Cover、一个有界内未命中的 Cover、一个已知反例和一个完整深度通过的 Assert。对照原生报告核对名、状态、深度和见证。
3. 试一个多 pattern 或多复位段的 FSDB 包，每个 `C_END_P*_S*` 都须出现。用支持该写法的安装版本验证双层宏和带 `##[1:$]` 的原生属性，核对展开后的实例名。
4. 验证许可等待、求解超时和 GUI 取消。许可等待与求解超时分开观察。若站点适配器提交了独立调度作业，适配器须负责撤销该作业。
5. 交付站点脚本、工具版本、设备启动步骤、用例与原生报告链接，以及逐属性状态映射。明确哪些用例已经用有许可证的工具运行，将协议联调与真实求解验收分别记录。

| 现象 | 先检查 |
|---|---|
| 找不到适配器 | 文件是否可执行，脚本解释器与站点环境能否在同一终端运行 |
| 找不到目标标签 | 读取的 top 与已实例化的层级。检查宏定义、generate 实例和源标签是否对应 |
| FSDB 包缺少终点 | 复位是否释放，选择器约束是否保留，深度是否覆盖最长复位段 |
| 请求或输入校验失败 | 请求和 `source/` 的原始字节是否被转码、重写或替换 |
| 状态未完成或空触发 | 查看属性被求解到的实际深度、约束可满足性和原始工具报告 |

任务默认归档在结果目录下的 `commercial_formal/<run_id>/`。排查时一起保留 `request.json`、`result.json`、原始 `adapter-result.json`、`adapter.log` 和本地工具报告。适配器协议回归可以用仓库的 `test_commercial_formal.py`，厂商求解验收使用本节的站点用例。

<!-- pagebreak -->

## 商业 Cover 的 VCS 回放

站点适配器应先确认已命中的 Cover 是否有对应的原始轨迹（witness）。记录完整实例名、命中深度、工具版本和原始产物。VCS 回放需要可驱动的逐拍 DUT 输入，以及与 Formal 一致的时钟、复位或初态。VCD 是可选的交换格式。

| 站点工具的产物 | 移植方式 |
|---|---|
| VCD | 核对文件对应的 Cover 实例。解压后用 `trace` 回传任务内的 VCD 相对路径。按采样边沿提取 DUT 输入，生成 VCS 激励。 |
| CSV、TXT、JSON 或逐拍查询 | 用站点支持的导出命令或接口取得输入值，按端口名、位宽和数组下标映射为逐拍激励。原始文件可填入 `reports`。已有可读激励时无需 USDB 提取许可。 |
| 可运行的 SV testbench | 在 VCS 编译 testbench 和同一版 RTL、SVA，核对时钟、复位与实际 Cover 命中。纳入 Studio 回归时，按 SV 用例模板把激励接入 `main_phase`。 |
| USDB 或其他私有波形库 | 若站点有可用的厂商导出或读取接口，将时钟、复位和逐拍 DUT 输入导出成可读格式。结果接口可在 `reports` 归档任务目录内的原始文件。 |
| 仅有命中结论、深度或不可读取的波形库 | 保留 Formal 结论并记录原始轨迹无法回放。若目标是取得同一个 Cover 的 VCS 波形，可重新搜索或编写定向用例，并记录新激励的来源。 |

### 将轨迹接入现有驱动

商业 Formal v1 支持查看 VCD、归档报表和显示逐属性结果。自动回放需要站点将原始轨迹转成仿真激励。现有 `functional_cover.py` 的轨迹读取使用 SBY 专用的 `smt_step` 与 `fc_stimulus`，`make formal-replay` 接收已生成激励的 Open Formal 活动编号。厂商通用 VCD 和文本输入应按其端口和周期定义解码，再接入 VCS 驱动。

先核对 RTL、SVA、宏、参数和约束版本，再对齐复位、启动拍与 SVA 边沿前采样。逐拍保留端口值、空拍和背压。现有 `stimulus.txt` 的 `FORMAL_COVER_V1` 模式按 DUT 端口位序接收用十六进制编码的二态输入帧。遇到缺失值、X/Z、多时钟或中途复位时，核对现有驱动能否保留原语义，必要时改用能忠实驱动该轨迹的 SV testbench。DUT 输出和内部状态由 VCS 计算。

符合 Studio `main_phase` 模板的移植用例可以从项目根目录执行以下命令。开启波形时使用站点已有的 VCS 和 FSDB 生成配置。

```bash
make run CASE=/path/to/replay_tc.sv WAVES=1
```

<!-- pagebreak -->

## 在站点核验轨迹导出和回放

### 阿卡思 FormalMC

厂商产品页说明 FormalMC 能为被证伪的属性生成可用于仿真的激励。Cover 命中导出需按实际安装版本和授权单独验证。UCAgent 的公开脚本会处理求解目录下已有的 VCD。`fanin -cover -list -dump` 在该脚本中用于 COI 覆盖分析。FormalMC 2024 等版本以本版命令帮助和实际产物为准。[产品页](https://www.arcas-da.com/html/products/AveMC.html) · [UCAgent 脚本](https://raw.githubusercontent.com/XS-MLVP/UCAgent/ac007684b2c6b1b4b7a1ca6e3090fef174d459fa/ucagent/lang/zh/skills/formal/lib/templates/tests/formal.tcl.j2#L38)

选一个已知可达的 Cover 单独试跑，检查结果菜单或命令帮助中的 witness、trace、export 和 testbench 入口。用现有 License 试导出并记录结果和许可诊断。把实际的 `-work_dir` 路径代入下方命令，列出该次运行的产物。核对文件与目标 Cover 的关联。

```bash
find /path/to/formalmc-work-dir -type f \
  \( -iname '*.vcd' -o -iname '*.vcd.gz' -o -iname '*.usdb' \
     -o -iname '*.fsdb' -o -iname '*.csv' -o -iname '*.txt' -o -iname '*.sv' \) -print
```

### 只有命中结论时重新生成激励

若需要的是同一目标的仿真命中，而现场无法取出原始逐拍输入，可用 Studio 的 Open Formal 对支持的同一 Cover 重新搜索。以下以基准 DUT 的 `C_PARITY_R` 为例。将生成的用例标为重新搜索得到的轨迹，并继续保留原始商业 Formal 结论。

```bash
make formal-cover CAMPAIGN=cover_repro TARGETS=C_PARITY_R FORMAL_DEPTH=48
make formal-replay CAMPAIGN=cover_repro WAVES=1
```

### 核对 VCS 运行证据

将原始 witness 或新用例、端口与采样映射、运行日志共同归档。用 VCS 实际记录的 `functional_hits.json` 验证同一 Cover 实例命中，核对 `waves.fsdb` 和 `simv.vdb`。Studio 回归批次通过且 DUT、属性版本与覆盖定义相符时，可参与联合最小集。

### 用厂商波形生成验证包

已生成的 FSDB 验证包交给商业 Formal 时，站点求解器读取包内 RTL、SVA 和文件清单。商业工具的私有数据库由站点驱动按厂商入口加载。若起点是 USDB 波形，Studio 导入需新增厂商读取适配，将时钟、复位段和沿前采样整理为 `fsdb_formal/core.py` 消费的 `meta`、`reset` 和 `edge` 记录，再复用验证包生成逻辑。合见工软的 USDB 是波形格式，FormalMC 的可导出格式以站点版本为准。[合见工软 UVD 波形说明](https://www.univista-isg.com/site/news_detail/32)
