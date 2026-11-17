# test_mode Studio 商业 Formal 移植手册

本手册供目标设备上的 agent 按顺序执行。先用仓库的基准 DUT 完成一次命中 Cover 的真实搜索，再将该轨迹用 VCS 回放。确认回放进入「结果与波形」后，与同版本的普通回归计算「联合最小集」。DUT Assert 和 FSDB 验证包各有独立的验收步骤。

执行前记录 Studio 版本、厂商工具及版本、使用的 DUT、目标属性和本地结果目录。每一步保留原始命令、运行编号和证据路径。遇到阻塞时，从出现错误的步骤继续排查。

| 章节 | 操作 |
| --- | --- |
| 01–03 | 核对版本和许可环境，检查 VCS，准备一份可复用的原生请求。 |
| 04–06 | 读取原生请求，验证厂商命令，安装站点驱动。 |
| 07–10 | 转换厂商报告，核对状态，离线自测接口，运行真实 DUT Cover 和 Assert。 |
| 11–14 | 选择导出路线，准备 VCD 或逐拍 JSON，发起 VCS 原轨迹回放。 |
| 15–16 | 提取联合最小集，导出选中 FSDB，验证 FSDB 包。 |
| 17–20 | 核对具体厂商能力，执行验收矩阵，定位失败，交付移植结果。 |

**验收边界。** `PREPARED` 证明 Studio 已生成请求。商业结果 `REACHED` 证明商业引擎在该约束下找到了命中。VCS `PASS` 且存在目标命中记录才证明这一条轨迹在仿真中重现。联合最小集还需要覆盖率重合并通过及最优证明。

<!-- pagebreak -->

## 01 检查工程版本和运行设备

在目标 Linux 的 Bash 终端进入解压或检出的 Studio 根目录。命令从包含 `Makefile` 和 `simulation/vcs/` 的目录执行。将首行路径替换为实际位置，保留后续命令。

```bash
cd /path/to/test_mode-Studio
ROOT="$PWD"
test -f "$ROOT/Makefile" || exit 1
test -f simulation/vcs/commercial_formal.py || exit 1
test -f simulation/vcs/commercial_replay.py || exit 1
uname -srm
python3 --version
make --version
git rev-parse --short HEAD 2>/dev/null || true
make -n formal-commercial-replay COMMERCIAL_RUN_ID=version_check
```

预期最后一条打印含 `formal-replay --commercial-run` 的命令。该检查使用 `make -n`，无需存在 `version_check` 任务。源码 ZIP 无 Git 元数据时，记录压缩包文件名和校验值。若缺少回放入口，先在有该入口的 Studio 版本上继续。

先选仓库基准 DUT。本手册的首轮目标 `C_XAU0_AR` 在 `simulation/vcs/functional_coverage.sv`。GUI 的「DUT 集成」显示当前 DUT，文件设置在 `simulation/vcs/dut.json`。现场工程从基准 DUT 换到其他 RTL 前，先保留这一轮通过的结果。

- 商业 Formal 任务由系统 `python3` 启动。正式运行使用实际安装的商业工具及对应的 License。
- VCS 回放和覆盖分析另需 VCS、Verdi、URG 及相应库。带运行时的 Linux 发行包提供 GUI 与 Open Formal 所需运行时。首次使用可按根目录 README 检查运行时。
- 使用目标机 Bash 和本地文件系统。图形操作需要可用的桌面会话。批处理可先通过 Make 完成，随后在读取同一结果目录的 GUI 上验证。

<!-- pagebreak -->

## 02 加载工具环境并运行普通用例

向现场管理员取得已经验证的环境脚本、商业 Formal 启动器和原生批处理示例。先在终端加载环境。若站点用模块系统，执行现场提供的模块加载命令。下例路径需替换成站点的实际值。

```bash
source /site/eda/setup.sh
command -v python3 make vcs verdi urg
export EDA_RESULT_ROOT="$HOME/studio-formal-port-results"
mkdir -p "$EDA_RESULT_ROOT/port-checks"
CHECK_DIR="$EDA_RESULT_ROOT/port-checks"
set -o pipefail
make doctor 2>&1 | tee "$CHECK_DIR/doctor.log"
make run CASE=tc_directed SEED=17 WAVES=1 \
  2>&1 | tee "$CHECK_DIR/ordinary.log"
ORDINARY_BATCH=$(sed -n 's/^BATCH \([^ ]*\) TOTAL .*/\1/p' \
  "$CHECK_DIR/ordinary.log")
printf '%s\n' "$ORDINARY_BATCH"
```

`EDA_RESULT_ROOT` 使用绝对 ASCII 路径且无空格。如主目录不满足，改用目标机上可写的 ASCII 路径，再启动 Make 和 GUI。显式设置能让 CLI 与桌面直接找到同一批任务。未设置时，普通 ASCII 工程默认使用 `build/vcs`，其它路径可能使用 `/tmp/test_mode-studio-<uid>/results-<key>`。以日志打印的绝对路径为准。

`make doctor` 检查 VCS、Verdi、URG 和相关库。普通用例成功后记下 `BATCH` 后的编号，查看 `regressions/该编号/summary.json` 的 `status` 和 `coverage.status`。联合选集需要后者为 `PASS`。许可、仿真或覆盖率异常先按第 19 节修复。

在 GUI 的「本机工具 → 高级选项」可以保存本机路径。也可设置 `EDA_TOOL_SETUP`、`VCS_HOME`、`VERDI_HOME`。显式环境变量覆盖本机保存值。适配器路径可在商业页指定，也可通过 `EDA_COMMERCIAL_FORMAL_ADAPTER` 提供默认值。站点驱动变量应从同一终端导出。以 `make gui` 从该终端启动。

<!-- pagebreak -->

## 03 只准备任务并核对快照

首次接入用 `PREPARED` 检验 Studio 到站点的输入。准备任务无需签出商业 Formal License。以下命令假设第 02 节已设置结果目录。

```bash
PREP_ID="port_prepare_$(date +%Y%m%d_%H%M%S)_$$"
make formal-commercial COMMERCIAL_PREPARE=1 \
  COMMERCIAL_RUN_ID="$PREP_ID" COMMERCIAL_MODE=cover \
  COMMERCIAL_TARGETS=C_XAU0_AR COMMERCIAL_DEPTH=48 \
  COMMERCIAL_DEFINES=SYNTHESIS
PREP_JOB="$EDA_RESULT_ROOT/commercial_formal/$PREP_ID"
python3 -m json.tool "$PREP_JOB/result.json"
```

预期任务日志打印 `COMMERCIAL_FORMAL_RESULT` 与结果文件路径，结果 `status` 为 `PREPARED`。准备任务和正式求解各用新的运行编号，保存各自的输入快照。重复的编号会触发目录已存在。默认编号由 Studio 生成。

下面逐个核对 `request.json` 与快照字节。成功时输出任务编号、顶层和已校验文件数。

```bash
python3 - "$PREP_JOB/request.json" <<'PY'
import hashlib
import json
from pathlib import Path
import sys

request = Path(sys.argv[1]).resolve()
data = json.loads(request.read_bytes())
assert data["schema"] == "test_mode.commercial_formal.v1"
assert data["run_id"] == request.parent.name
source = request.parent / data["inputs"]["directory"]
for name, expected in data["inputs"]["sha256"].items():
    actual = hashlib.sha256((source / name).read_bytes()).hexdigest()
    assert actual == expected, name
print(data["run_id"], data["design"]["top"], len(data["inputs"]["sha256"]))
PY
```

GUI 入口为左侧「07 Formal 验证」中的「商业 Formal」。选「DUT Cover」，填 `C_XAU0_AR`、深度 `48`、附加编译宏 `SYNTHESIS`，点「只准备任务」。用「查看请求」核对同样字段。

<!-- pagebreak -->

## 04 按请求读取原生 RTL 与属性

每次求解都读取第二级任务目录中的 `request.json`。例如 `commercial_formal/port_prepare_…/request.json`。工作目录通过第一个参数的父目录确定。

| 请求字段 | 按字段做的事 |
| --- | --- |
| `schema`、`run_id` | 读取 `test_mode.commercial_formal.v1`。结果回填同一个 `run_id`，校验值取原始请求文件字节的 SHA-256。 |
| `inputs.directory`、`filelist` | 先进入 `source/` 或它的副本，再按站点工具支持的方式加载 `files.f`。相对路径以该目录为准。 |
| `inputs.files`、`include_dirs`、`defines` | 工具无法直接读 filelist 时，按此顺序传原生源文件，并单独传 include 目录和宏。保持原有编译单元语义。 |
| `inputs.sha256` | 记录整个快照的字节摘要。编译输出、工作库、日志另放在任务目录内，工具需要写输入时操作快照副本。 |
| `design` | 从 `top` 展开。用 `clock` 的上升沿。按 `reset`、`reset_active=0` 和 `initialize_reset=true` 初始化并释放复位。 |
| `mode`、`kinds` | `cover` 搜索 Cover，`bmc` 有界检查 Assert，`prove` 处理验证包的 Assert 和 Cover。Assume 按原生环境约束加载。 |
| `targets`、`required_labels` | 非空目标按完整实例名或源码标签精确匹配。一个源码标签匹配多个实例时全部查询。验证包列出的标签逐一返回。 |
| `depth`、`solve_timeout_seconds` | 深度按释放复位后的采样拍理解。超时只用于求解，`0` 表示不限时。按站点版本核对单位与起算点。 |

DUT 模式的顶层为 `test_mode_commercial_formal_top`，时钟 `clk`，低有效复位 `rst_n`。`formal_top.sv` 包含 DUT 例化、`port_checks` 和对 `user_properties.sv` 的 include。DUT 的原生 Cover 可能在子实例中。编译顺序以请求为准，include 文件由 wrapper 或检查器引入。

基准 DUT 的 `SYNTHESIS` 宏隔离包含 `$stop` 的仿真诊断。其它 DUT 以该版源码和真实展开结果决定宏。附加宏与 DUT 已有宏同名会报错。验证包模式的顶层和时钟由包自身给出。

<!-- pagebreak -->

## 05 在厂商工具中先单独跑通一条属性

1. 从已授权且能运行的本地批处理入口开始。记录可执行路径、产品版本和支持的属性验证模式。用安装版本的手册、帮助或站点示例确认入口，同时核对工具签出许可的结果。
2. 用第 03 节的快照副本替换本地示例的 RTL 输入。依次设置文件、include、宏和 top。用原生命令列出编译错误和已实例化的属性。首先找到 `test_mode_commercial_formal_top.C_XAU0_AR`。
3. 按 `request.design` 配置时钟，真实施加低电平复位，再释放。确认初始化完成、采样开始以及环境约束可满足。`disable iff (!rst_n)` 控制属性使能，复位初始化由工具执行。
4. 用厂商原生属性查询取得名称、种类和源码标签。列出匹配目标的完整实例，按选定模式运行。让 `assume` 继续约束搜索。未选中的 Assert / Cover 保留原生语义并从结果行中过滤。
5. 记录厂商使用的第 0 拍或第 1 拍定义、是否计初始化拍，以及超时是否为求解阶段。用一个容易命中的属性检查拍号换算。以实际求解深度回填有界状态。
6. 在独立工作目录导出原始报告和 witness。先确认 witness 与目标实例对应，再在第 11 节选择 Studio 的回放接口。

每个求解任务使用自己的临时工作库。记录命令参数、工作目录、top、编译结果、已枚举属性和厂商原始状态。许可证服务器、凭据、受限日志和二进制由站点保管。

厂商编译器若拒绝宏、命名序列、Cover 动作块或 `##[1:$]`，先使用短属性完成接口定位。再对原始写法逐项验证，记下具体源码行和原生诊断。采用厂商认可的编译选项前，核对预处理、属性名称及命中语义。保留实际验收结论。

<!-- pagebreak -->

## 06 安装站点驱动

推荐复用仓库的 `site_adapter.py`。Studio 调用 `ADAPTER REQUEST_JSON ADAPTER_RESULT_JSON`。适配器在同一任务目录启动 `DRIVER REQUEST_JSON SITE_RESULT_TSV`。驱动把真实厂商结果写成下一节的 TSV。

在 `local-config/formal-port/` 保存目标机专用脚本。也可使用站点批准的工作目录。下面的驱动准备编译快照副本和 JSON 运行计划。厂商启动器按此计划运行，通过第二个参数写 TSV。实际厂商命令在第 05 节验证后写入该启动器。

```bash
PORT_DIR="$ROOT/local-config/formal-port"
mkdir -p "$PORT_DIR"
cat >"$PORT_DIR/site-driver.py" <<'PY'
#!/usr/bin/env python3
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

if len(sys.argv) != 3:
    sys.exit("usage: site-driver.py REQUEST_JSON SITE_RESULT_TSV")
request_path, report = (Path(arg).resolve() for arg in sys.argv[1:])
request = json.loads(request_path.read_bytes())
if request["schema"] != "test_mode.commercial_formal.v1":
    sys.exit("unsupported request schema")
native = shutil.which(os.environ.get("EDA_SITE_NATIVE_FORMAL", ""))
if native is None:
    sys.exit("set EDA_SITE_NATIVE_FORMAL to the site native launcher")
job = request_path.parent
work = job / "vendor_work"
source = work / "source"
shutil.copytree(job / request["inputs"]["directory"], source)
plan = work / "native-plan.json"
plan.write_text(json.dumps({
    "job_dir": str(job), "source_dir": str(source),
    "filelist": str(source / request["inputs"]["filelist"]),
    "request": request,
}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
subprocess.run([native, str(plan), str(report)], cwd=work, check=True)
if not report.is_file():
    sys.exit("native launcher did not generate the requested TSV")
PY
chmod u+x "$PORT_DIR/site-driver.py"
```

<!-- pagebreak -->

### 实现厂商启动器

下面按执行顺序填写站点脚本。每一步使用第 05 节已在目标机实际运行的命令。

1. 从参数 1 读取 `native-plan.json`。取得 `job_dir`、`source_dir` 和 `request`。创建厂商工作库及报表目录，在工具支持的编译工作目录中执行。若 filelist 含相对路径，将编译目录设为 `source_dir`，或用绝对路径传编译项。
2. 补齐工具本版本的启动参数和属性验证模式。站点脚本复用已确认可签出许可的入口。将启动命令以参数数组执行或交给厂商 Tcl。路径有空格时按工具的 list/argv 规则引用。
3. 从 `request.inputs` 传文件、include 和宏，按 `request.design` 展开、初始化复位。等待编译与属性枚举结束。把实际顶层和原生属性清单存入本次任务。
4. 按 `targets` 在原生清单筛选，核对 `required_labels`。逐个保存 `原生句柄 → 完整实例名 → 源码标签` 的映射。若厂商显示名有不同层级前缀，只在明确知道层级对应关系后调整为 Studio 所识别的路径。
5. 设求解深度和求解超时。`cover` 请求用 Cover 搜索，`bmc` 用 Assert 有界检查，`prove` 查询包的 Assert 与终点 Cover。用厂商原生机器可读输出取每条结果。单独记录工具退出码和属性语义结论。
6. 对每个目标分别查询原始状态、真实深度及对应轨迹。先验证导出能力和输入映射，再填所获文件的任务内相对路径。输出第 07 节规定的 UTF-8 TSV 到参数 2 指定位置。核对报告路径与工作目录。
7. 启动器退出后，外层站点驱动检查 TSV，内置适配器写原子 JSON。若中途失败，在标准错误指出失败阶段和原生日志路径，保留目录供排查。

若工具只有交互界面，先确认厂商是否提供批处理、Tcl 或结果 API。可从 GUI 中单独验证轨迹导出并记录命令。自动化接入所需入口由站点确认。GUI 导出可用但无法批量调用时，先记录已验证的手动能力。

<!-- pagebreak -->

## 07 将原生报告转换成适配器结果

### 配置真实驱动

```bash
SITE_ADAPTER="$ROOT/simulation/vcs/formal_adapters/site_adapter.py"
chmod u+x "$SITE_ADAPTER"
export EDA_COMMERCIAL_FORMAL_ADAPTER="$SITE_ADAPTER"
export EDA_COMMERCIAL_FORMAL_DRIVER="$PORT_DIR/site-driver.py"
export EDA_SITE_NATIVE_FORMAL=/site/bin/native-formal
test -x "$EDA_SITE_NATIVE_FORMAL"
```

将 `/site/bin/native-formal` 换为站点实际可执行程序。上节模板要求它接收 `NATIVE_PLAN_JSON SITE_RESULT_TSV` 两个参数。它读取计划中的 `source_dir`、`filelist` 和完整请求，在 `job_dir` 下保存原生证据。`site_adapter.py` 最后封装 JSON 并写入请求指定的输出文件。已有同接口驱动时可直接设置 `EDA_COMMERCIAL_FORMAL_DRIVER`。

### TSV 字段顺序

使用 UTF-8 和真实制表符。Python 可用 `csv.writer(stream, delimiter="\t", lineterminator="\n")`。每个属性写一行，空值写为空字段并保留该列。

| 首列 | 后续列 |
| --- | --- |
| `engine` | 工具名称、安装版本。恰好一行。 |
| `complete` | `1` 或 `0`。恰好一行。`1` 须有完整的属性枚举和真实状态。 |
| `property` | `kind`、`label`、`name`、`status`、`depth`、VCD 相对路径、可选逐拍 JSON 相对路径。前六列均占位。 |
| `report` | 任务内已有文件的相对路径。每个文件一行。 |
| `message` | 一条简短的诊断说明，可省略。 |

下面展示真实命中记录应使用的列位置。将名称和深度取自实际报告，并确认输出文件存在。`None` 由 `csv.writer` 输出为空字段。

```python
writer.writerow(["engine", tool_name, tool_version])
writer.writerow(["complete", "1" if enumeration_is_complete else "0"])
writer.writerow(["property", "cover", source_label, full_instance,
                 "REACHED", actual_depth, vcd_path, stimulus_path])
writer.writerow(["report", "vendor_work/native-summary.txt"])
```

启动、许可证或编译异常时让驱动返回非零，并在标准错误输出原生诊断。求得反例、到界、求解超时等已有结构化结论时写对应状态，适配器仍可正常退出。

<!-- pagebreak -->

## 08 映射状态和校验 JSON

| 厂商原始结果 | Studio 逐属性状态 |
| --- | --- |
| 原生 Assert 完成无界证明 | `PROVEN`。无实际深度时可留空。 |
| Assert 检查到确定的边界且无反例 | `BOUNDED_PASS`。提供实际完成深度。 |
| 有原生 Assert 反例 | `COUNTEREXAMPLE`。有 VCD 时可供查看。 |
| 厂商明确报告 Assert 空触发 | `VACUOUS`。保留空触发诊断。 |
| Cover 真实命中 | `REACHED`。深度为非负整数且不超过请求深度。 |
| Cover 在已完成的边界内未命中 | `UNREACHED_IN_BOUND`。填写实际深度。 |
| 厂商给出 Cover 不可达证明 | `UNREACHABLE`。留存证明结论。 |
| 无确定结论、求解超时或单属性错误 | 对应记录 `UNKNOWN`、`TIMEOUT` 或 `ERROR`。 |

未知厂商状态先映射到 `UNKNOWN` 并记录原始值。跳过或未枚举项需报告实际完整性。Studio 校验每一条结果与请求的种类、目标和深度。整次任务可能是 `PASS`、`FAIL`、`INCOMPLETE`、`ERROR` 或 `CANCELLED`。只准备的状态为 `PREPARED`。以归档 `result.json` 为准。

若直接实现 JSON 适配器，需从原请求读取 `schema`、`run_id`，对原始字节算 `request_sha256`。以下是字段示例。用真实路径、版本、深度和摘要替换示例。先在同目录写临时文件，再以原子重命名写入第二个参数指定的位置。

```json
{
  "schema": "test_mode.commercial_formal.v1",
  "run_id": "site_cover",
  "request_sha256": "<SHA-256 of original request.json bytes>",
  "engine": {"name": "<installed tool>", "version": "<version>"},
  "complete": true,
  "properties": [{
    "kind": "cover", "label": "C_XAU0_AR",
    "name": "test_mode_commercial_formal_top.C_XAU0_AR",
    "status": "REACHED", "depth": 1,
    "trace": "waves/xau0.vcd", "stimulus": "witness/xau0.json"
  }],
  "reports": ["vendor_work/native-summary.txt"]
}
```


<!-- pagebreak -->

## 09 离线验证接口和诊断

在第 03 节的 `PREPARED` 任务上执行一次样例适配器。以下离线探针验证参数、TSV 和摘要，明确回传 `UNKNOWN` 与 `complete=false`。它把结果写到独立文件，原任务保持 `PREPARED`。

```bash
PORT_DIR="$ROOT/local-config/formal-port"
mkdir -p "$PORT_DIR"
cat >"$PORT_DIR/probe-driver.py" <<'PY'
#!/usr/bin/env python3
import csv
from pathlib import Path
import sys

report = Path(sys.argv[2])
with report.open("w", encoding="utf-8", newline="") as stream:
    writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
    writer.writerow(["engine", "ProtocolProbe", "1"])
    writer.writerow(["complete", "0"])
    writer.writerow(["property", "cover", "C_XAU0_AR",
                     "test_mode_commercial_formal_top.C_XAU0_AR",
                     "UNKNOWN", "", ""])
    writer.writerow(["message", "Protocol only. No commercial solve."])
PY
chmod u+x "$PORT_DIR/probe-driver.py"
EDA_COMMERCIAL_FORMAL_DRIVER="$PORT_DIR/probe-driver.py" \
  python3 "$ROOT/simulation/vcs/formal_adapters/site_adapter.py" \
  "$PREP_JOB/request.json" "$PREP_JOB/probe-adapter-result.json"
sha256sum "$PREP_JOB/request.json"
python3 -m json.tool "$PREP_JOB/probe-adapter-result.json"
python3 -m unittest discover -s simulation/vcs/tests \
  -p 'test_commercial*.py'
```

期望 JSON 中 `run_id` 与 `PREP_ID` 相同，`request_sha256` 与 `sha256sum` 的第一列相同，`properties[0].status` 为 `UNKNOWN`。当前任务下次由真实适配器运行时用新编号。用第 07 节配置实际驱动，确认 `EDA_COMMERCIAL_FORMAL_DRIVER` 指向 `site-driver.py` 或已验收的站点脚本。

<!-- pagebreak -->

## 10 运行首个真实 DUT Cover 和 Assert

以下命令假设已有真实站点驱动。先检查 `EDA_COMMERCIAL_FORMAL_ADAPTER` 和 `EDA_COMMERCIAL_FORMAL_DRIVER` 对应的文件可执行。基准 DUT 使用 `SYNTHESIS` 附加宏。

```bash
COMM_ID="port_cover_$(date +%Y%m%d_%H%M%S)_$$"
make formal-commercial COMMERCIAL_MODE=cover \
  COMMERCIAL_RUN_ID="$COMM_ID" COMMERCIAL_TARGETS=C_XAU0_AR \
  COMMERCIAL_DEPTH=48 COMMERCIAL_TIMEOUT=600 \
  COMMERCIAL_DEFINES=SYNTHESIS
COMM_JOB="$EDA_RESULT_ROOT/commercial_formal/$COMM_ID"
python3 -m json.tool "$COMM_JOB/result.json"
```

确认有真实引擎版本、`C_XAU0_AR` 的 `REACHED` 和实际命中深度。对照 `adapter.log`、原生报告和适配器 JSON。若 `trace` 和 `stimulus` 均为空，下一步先使驱动按第 11 节选择导出方式，再发起新任务。GUI 等效操作是点「运行商业 Formal」，在历史下拉框选择该次任务。

```bash
BMC_ID="port_bmc_$(date +%Y%m%d_%H%M%S)_$$"
make formal-commercial COMMERCIAL_MODE=bmc \
  COMMERCIAL_RUN_ID="$BMC_ID" COMMERCIAL_TARGETS=A_R_READY \
  COMMERCIAL_DEPTH=16 COMMERCIAL_TIMEOUT=600 \
  COMMERCIAL_DEFINES=SYNTHESIS
python3 -m json.tool \
  "$EDA_RESULT_ROOT/commercial_formal/$BMC_ID/result.json"
```

`A_R_READY` 在基准环境检查器中。首轮检查可接受真实 `PROVEN` 或完成请求深度的 `BOUNDED_PASS`。若工具报告 `VACUOUS` 或其它未完成状态，检查复位、Assume 和真实覆盖上下文。

目标列表使用逗号分隔且不得重复。留空表示该模式下全部目标。`COMMERCIAL_TIMEOUT=0` 取消求解上限。适配器退出码与 Studio 整体状态分别理解。Studio 内部返回 `0` 表示 `PASS` 或 `PREPARED`，`1` 表示 `FAIL`，`2` 表示 `ERROR`，`3` 表示 `INCOMPLETE`，`130` 对应取消。Make 通常把子命令异常统一表现为自身失败。直接查看结果文件。

<!-- pagebreak -->

## 11 选择可回放的轨迹来源

逐条记录 Cover 完整实例名、命中深度、原始 witness 路径及取值方式。按该目标实际拿到的文件走下表。命中轨迹的输入要覆盖复位释放后的首个采样沿至命中沿，保留原始顺序和空拍。

| 可用证据 | 操作 |
| --- | --- |
| 标准 VCD | 写入 `trace`，执行第 12 节。`.vcd.gz` 先解压到任务目录。 |
| 逐拍 API、CSV、TXT 或 JSON | 保留原始导出，核对端口和采样沿，生成 `stimulus`。参见第 13 节。 |
| 厂商可执行 SV testbench | 用同版 RTL 和 SVA 核对该 testbench 的时钟复位及目标命中。适配到 `simulation/vcs/tc/` 的 SV `main_phase` 用例模板后，以 `make run CASE=/path/to/replay_tc.sv WAVES=1` 归档为普通回归。 |
| USDB、FSDB 或其它私有数据库 | 先用现有许可下的厂商导出或读取接口获得 VCD / 逐拍输入。原始库可保留在 `reports`。是否具备转换能力由站点实测决定。 |
| 仅有状态、深度或无法读取的库 | 保留商业求解结论，把原轨迹回放记为待补证据。若只需 VCS 命中同一 Cover，可另做新激励并记录来源。 |

若没有 VCD，也没有私有波形提取 License，优先查询商业引擎是否可用现有授权导出逐拍输入或 SV testbench。存在任一可读激励即可继续。只剩命中状态和深度时，可用已支持该目标的 Open Formal 重搜，或编写定向用例。当使用新输入时，在交付记录注明来源和新运行编号。

```bash
NEW_CAMPAIGN="port_research_$(date +%Y%m%d_%H%M%S)_$$"
make formal-cover CAMPAIGN="$NEW_CAMPAIGN" TARGETS=C_XAU0_AR \
  FORMAL_DEPTH=48 COVER_TIMEOUT=600
make formal-replay CAMPAIGN="$NEW_CAMPAIGN" WAVES=1
```

保留原始文件。适配器结果中 `reports`、`trace`、`stimulus` 都使用任务目录中的普通文件相对路径。若原始文件位于外部，获取允许的副本后再引用。

<!-- pagebreak -->

## 12 让 Studio 读取 VCD

1. 在厂商工具中为已命中的属性导出 VCD，包含该请求的 Formal wrapper 顶层作用域。可有外层作用域。时钟 `clk`、复位 `rst_n` 和第 13 节列出的 19 个 DUT 输入应在同一匹配作用域内。名称和位宽应逐一对应。重名或缺失时，通过厂商设置修正导出，或按第 13 节使用逐拍输入。
2. 从初始化轨迹开始，确认低有效复位及首个有效上升沿。保留上升沿之前的输入值及真实时钟跳变。Studio 按上升沿时间戳之前的值采样。同一时间戳写入的新输入供后续沿使用。厂商按求解步记录而无可区分沿前值时，把已确认的逐拍值转换成 JSON。
3. 确认每一拍关键输入为确定二态值。当前商业 Cover 自动回放使用单上升沿时钟，并支持初次复位后连续运行。中途复位、多时钟或只能获得模糊值时，先保留原始产物，再评估能保持原时序的独立 SV 用例。
4. 在商业任务目录中存放标准 VCD，例如 `waves/xau0.vcd`。TSV 的 VCD 列或 JSON 的 `trace` 写这个相对路径。GUI 可选中该行后点「查看选中 VCD」检查波形。文件存在且路径可读时才显示可用的回放入口。

在厂商工作目录找到 `xau0.vcd.gz` 时，适配器可在任务目录执行以下命令。其中 `JOB_DIR` 是本次请求所在目录。

```bash
mkdir -p "$JOB_DIR/waves"
gzip -dc "$JOB_DIR/vendor_work/xau0.vcd.gz" \
  >"$JOB_DIR/waves/xau0.vcd"
head -n 45 "$JOB_DIR/waves/xau0.vcd"
```

若站点返回标准 FSDB 且目标设备的 Verdi 工具链能运行 `fsdb2vcd`，可在 Studio 工程根目录验证下方转换。然后按本节重新检查顶层、完整端口和沿前时序，一并保留原始 FSDB。

```bash
make wave-convert FSDB="$JOB_DIR/vendor_work/xau0.fsdb" \
  OUTPUT="$JOB_DIR/waves/xau0.vcd"
```

当结果同时提供 `trace` 和 `stimulus` 时，VCS 入口优先读取 `stimulus`。优先项校验失败时据日志修复该文件或在下一次结果中只提交通过验证的 VCD。

<!-- pagebreak -->

## 13 生成逐拍 JSON

`stimulus` 指向 `test_mode.cover_stimulus.v1` 文件。`frames[0]` 对应复位释放后的第一个有效上升沿，后续帧按采样顺序排列。源数据若每行对应求解状态，先实测哪一行是沿前输入，并剔除初始化段。时钟和复位由 Studio 的回放器生成。DUT 输出由 VCS 的 RTL 重新计算。

| 端口 | 位宽 |
| --- | --- |
| `arvalid`、`pc_err` | 1 |
| `src`、`arresp` | 2 |
| `araddr` | 18 |
| `arid`、`ar_rd_gnt`、`rw_rd_gnt` | 8 |
| `ar_port_id`、`rready` | 分别为 5 和 3 |
| `rd_mem_status[0]` 至 `rd_mem_status[7]` | 每项 2，共八项 |
| `mem_rdata` | 128，建议十六进制字符串 |

下面只展示一帧格式。完整文件须有 1 至 1000000 帧，每帧键集合相同。整数、带 `0x` 的十六进制字符串、带 `0b` 的二进制字符串都可用。保留输入数值和位宽，精确复制包括空拍在内的所有帧。

```json
{
  "schema": "test_mode.cover_stimulus.v1",
  "frames": [{
    "arvalid": 0, "src": 3, "araddr": 0, "arid": 0,
    "ar_port_id": 0, "arresp": 0, "rready": 0,
    "ar_rd_gnt": 0, "rw_rd_gnt": 0,
    "rd_mem_status[0]": 0, "rd_mem_status[1]": 0,
    "rd_mem_status[2]": 0, "rd_mem_status[3]": 0,
    "rd_mem_status[4]": 0, "rd_mem_status[5]": 0,
    "rd_mem_status[6]": 0, "rd_mem_status[7]": 0,
    "mem_rdata": "0x0", "pc_err": 0
  }]
}
```

字段可按 `simulation/vcs/functional_properties.py` 的 `INPUTS` 核对。站点导出的总线名称需映射到 wrapper 端口。按下标逐项拆分 `rd_mem_status`，核对厂商导出的端序。遇到缺少列、越界、X/Z 或未知行顺序时，补采实际值或使用厂商明确给出的合法具体化。保留具体化的依据。

<!-- pagebreak -->

### 将已核对的逐拍 CSV 转成 JSON

先从厂商输出制作归一 CSV。首行为 `cycle` 加上前页的全部端口名，列顺序不限。`cycle` 从 `0` 连续递增。每行对应一个有效上升沿的沿前输入，宽总线用十六进制字符串。保留原始导出和归一版本。

将下方代码保存为 `$PORT_DIR/csv-to-stimulus.py`。它使用 Studio 本身的格式校验，成功后写出整数和字符串均可读的回放文件。

```python
import csv
import json
from pathlib import Path
import sys

from commercial_replay import STIMULUS_SCHEMA, json_frames
from functional_properties import INPUTS

source, output = (Path(arg).resolve() for arg in sys.argv[1:])
columns = {"cycle", *(name for name, _ in INPUTS)}
frames = []
with source.open(encoding="utf-8-sig", newline="") as stream:
    reader = csv.DictReader(stream)
    header = reader.fieldnames or []
    if set(header) != columns or len(header) != len(columns):
        raise ValueError("CSV must contain cycle and one column per port")
    for expected, row in enumerate(reader):
        if None in row or any(value is None for value in row.values()):
            raise ValueError(f"invalid column count at cycle {expected}")
        if int(row.pop("cycle")) != expected:
            raise ValueError(f"unexpected cycle, wanted {expected}")
        frames.append({name: value.strip() for name, value in row.items()})
output.parent.mkdir(parents=True, exist_ok=True)
temporary = output.with_suffix(".json.tmp")
temporary.write_text(json.dumps(
    {"schema": STIMULUS_SCHEMA, "frames": frames}, indent=2
) + "\n", encoding="utf-8")
print(f"validated {len(json_frames(temporary))} frames")
temporary.replace(output)
```

保存脚本后，厂商启动器在本次求解目录中执行下方命令。`JOB_DIR` 使用计划的 `job_dir`。

```bash
PYTHONPATH="$ROOT/simulation/vcs" \
  python3 "$PORT_DIR/csv-to-stimulus.py" \
  "$JOB_DIR/vendor_work/normalized.csv" "$JOB_DIR/witness/xau0.json"
```

转换成功后在该属性的 `stimulus` 记录 `witness/xau0.json`，并把原始波形或报告纳入 `reports`。首次转换可对照厂商查看器确认前两拍和命中拍。

<!-- pagebreak -->

## 14 启动 VCS 回放并验证真实命中

在商业页的任务下拉框选择 `COMM_ID`，选中带 VCD 或逐拍 JSON 的已命中 DUT Cover，点「VCS 回放选中」或「VCS 回放全部」。默认勾选「生成波形」。任务完成后点「结果与波形」，核对批次来源为「商业 Formal 回放」。

CLI 示例如下。`TRACES` 对应 `result.json` 中 `properties` 的零基序号。第一行为 `p0`。省略该参数时回放所有有轨迹的已命中项。首次定位问题可只选一条。

```bash
make formal-commercial-replay COMMERCIAL_RUN_ID="$COMM_ID" \
  TRACES=p0 WAVES=1 JOBS=1 TIMEOUT=120 \
  2>&1 | tee "$CHECK_DIR/commercial-replay.log"
REPLAY_BATCH=$(sed -n 's/^BATCH \([^ ]*\) TOTAL .*/\1/p' \
  "$CHECK_DIR/commercial-replay.log")
printf '%s\n' "$REPLAY_BATCH"
```

`TIMEOUT` 从 VCS 仿真实际启动后计时。商业搜索使用 `COMMERCIAL_TIMEOUT`。修改 DUT、RTL include、端口检查器、实例例化或属性后，重新求解并回放该版本。回放核对历史快照和当前 VCS 构建。商业任务的附加宏随请求归档，VCS 使用当前 DUT 配置的编译宏。两边行为一致性以真实命中和检查证据核对。

在 `regressions/回放批次/summary.json` 核对 `commercial_formal.run_id` 与原任务编号、`coverage.status` 和 `coverage.included`。进入该批次 `cases/用例编号/` 再核对以下文件。

| 文件 | 验收内容 |
| --- | --- |
| `result.json` | 用例 `PASS`、运行完成。`formal_origin.commercial` 指向商业任务和轨迹。 |
| `functional_hits.json` | `complete=true`，`hits` 中包括用例 `formal_origin.goals` 的所有目标。 |
| `stimulus.txt`、`samples.csv`、`clock_reset.csv` | 核对原始输入和每拍实际采样。回放从释放复位后的首个有效沿开始，原轨迹之后由 VCS 收尾。 |
| `waves.fsdb`、`simv.vdb` | 波形可从 GUI 打开。VDB 为该用例真实运行的覆盖库。 |

<!-- pagebreak -->

### 从归档自动核对回放

下面检查刚才日志提取出的 `REPLAY_BATCH`，要求这一批次的全部回放用例通过。演示命令开启波形。若现场选择了关闭波形，则单独记录波形待生成，选集仍以真实覆盖记录为准。

```bash
python3 - "$EDA_RESULT_ROOT" "$REPLAY_BATCH" "$COMM_ID" <<'PY'
import json
from pathlib import Path
import sys

root, batch_id, run_id = sys.argv[1:]
batch = Path(root) / "regressions" / batch_id
summary = json.loads((batch / "summary.json").read_bytes())
assert summary["commercial_formal"]["run_id"] == run_id
assert summary["coverage"]["status"] == "PASS"
assert summary["results"], "missing replay cases"
for row in summary["results"]:
    case = batch / "cases" / row["id"]
    assert row["status"] == "PASS" and row["completed"], case
    assert row["id"] in summary["coverage"]["included"], case
    origin = row["formal_origin"]
    assert origin["commercial"]["run_id"] == run_id, case
    evidence = json.loads((case / "functional_hits.json").read_bytes())
    assert evidence["complete"], case
    assert set(origin["goals"]) <= set(evidence["hits"]), case
    assert (case / "waves.fsdb").is_file(), case
    assert (case / "simv.vdb").is_dir(), case
    print(row["id"], sorted(origin["goals"]), "PASS")
PY
```

随后切换至「结果与波形」，选择同一批次和用例，点「Verdi 波形」，核对目标相关信号。查一次「联合最小集」的勾选候选。脚本中的任一断言失败时，按打印的阶段及目录检查原始证据。

<!-- pagebreak -->

## 15 用普通回归和商业回放提取联合最小集

选择同一版 DUT 源码、例化、覆盖率配置、功能属性与端口约束的批次。每个候选批次应有覆盖合并 `PASS`、逐用例 `simv.vdb` 和通过用例的完整功能覆盖记录。

在商业页点「联合最小集」，当前商业回放批次会预选。勾选第 02 节记录的同版本普通回归，求解秒数填 `0` 表示不限时，然后点「开始计算」。也可从「结果与波形」右侧进入「联合最小集」。同版本 Open Formal 回放是可选候选。

```bash
test -n "$REPLAY_BATCH" && test -n "$ORDINARY_BATCH" || exit 1
make coverage-minimize-joint \
  REGRESSIONS="$REPLAY_BATCH,$ORDINARY_BATCH" SOLVE_SECONDS=0 \
  2>&1 | tee "$CHECK_DIR/joint.log"
ANALYSIS_RESULT=$(sed -n 's/^COVERAGE_RESULT //p' "$CHECK_DIR/joint.log")
test -f "$ANALYSIS_RESULT" || exit 1
COVERAGE_SET_ID=$(basename "$(dirname "$ANALYSIS_RESULT")")
```

这两个变量来自前面相应日志。新终端中可从普通和回放日志的 `BATCH` 行取出编号后赋值。打开 `COVERAGE_RESULT` 指向的 `coverage_analysis/分析编号/result.json`。核对 `batches`、`verified`、`functional.verified` 和 `solver.optimal`。最优标志为真且重合并通过时，才能验收为已证明最小。用例可能被其他覆盖等价的用例替代。核对两批次均参与候选，选中集保留原并集的命中即可。

```bash
make coverage-fsdb COVERAGE_SET="$COVERAGE_SET_ID" \
  FSDB_OUT="$EDA_RESULT_ROOT/port-fsdb" TIMEOUT=120
```

`COVERAGE_SET_ID` 取结果文件父目录名。GUI 对应「最小用例集」中的「单独导出 FSDB」。在 `FSDB_EXPORT_RESULT` 指向的目录核对 `pattern_*.fsdb` 数量与 `selected` 一致。已有波形可直接复制。缺失时会使用原归档输入和二进制补跑。交付时同时保留 `selected_tests.csv` 和分析结果。


<!-- pagebreak -->

## 16 用商业 Formal 验证 FSDB 包

在「最小用例集」点「生成选集 Formal」，或从已知 FSDB 在「FSDB → Formal」生成。也可用已有分析编号执行下面命令。该步使用站点 Verdi NPI，从逐个 FSDB 提取原采样值。

```bash
EXPORT_ID="port_package_$(date +%Y%m%d_%H%M%S)_$$"
make coverage-formal COVERAGE_SET="$COVERAGE_SET_ID" \
  FORMAL_ID="$EXPORT_ID" TIMEOUT=120
```

日志 `NPI_PACKAGE` 后是 `package.zip`。选择同目录中已有的原生包，或解压 ZIP。将 `PACKAGE_DIR` 指向含 `SHA256SUMS.json`、`integration.json`、`summary.json`、`files.f`、`trace_properties.sv`、`formal_top.sv` 和 RTL 快照的目录。

```bash
PACKAGE_DIR=/absolute/path/to/unpacked-package
test -f "$PACKAGE_DIR/SHA256SUMS.json" || exit 1
PROVE_ID="port_prove_$(date +%Y%m%d_%H%M%S)_$$"
make formal-commercial COMMERCIAL_MODE=prove \
  COMMERCIAL_RUN_ID="$PROVE_ID" COMMERCIAL_PACKAGE="$PACKAGE_DIR" \
  COMMERCIAL_TIMEOUT=600 COMMERCIAL_DEFINES=SYNTHESIS
python3 -m json.tool \
  "$EDA_RESULT_ROOT/commercial_formal/$PROVE_ID/result.json"
```

示例宏适用于基准 DUT 生成、且包内尚未定义该宏的包。其它包按原生清单确认，已有相同宏时省略附加宏。GUI 使用「商业 Formal → FSDB 验证包」，选解压目录，或在「验证包证明」已选包时点「使用当前包」。深度留空使用包的推荐深度，目标来自包。

加载包的原生 SVA 与原生 filelist。按 `request.design` 初始化复位。保留 `pattern`、`reset_segment` 的原生选择约束。返回全部必需 Assert 和每个 `C_END_P*_S*`。用含多个 pattern 或复位段的包验收端点完整性。包 `PASS` 证实这组有限轨迹的约束和输出检查。原包受摘要保护，发现校验异常时重新导出或解压。

有 USDB 作为验证包的原始输入时，当前入口仍需标准 FSDB 或厂商读取适配。新适配需按 `fsdb_formal/core.py` 的读取约定产出 `meta`、`reset`、`edge` 记录并验证时钟、复位段、沿前采样与输出有效性。这条导入路径独立于 `stimulus` 的 DUT Cover 回放。

<!-- pagebreak -->

## 17 核实 FormalMC 和其他厂商能力

### FormalMC 与 USDB

先从二进制的实际启动信息、安装手册或站点提供的示例确认厂商、产品、版本及应用。阿卡思产品页称其 Formal MC 可为被证伪属性生成仿真激励。华大九天另有 Empyrean FormalMC 产品。合见工软的 UVS+ 产品页将 USDB 列为其专有波形格式。依据目标机确认实际组合，并分别验证 Assert 反例与 Cover 命中能导出哪些数据。[阿卡思 Formal MC](https://www.arcas-da.com/html/products/AveMC.html) · [华大九天形式化属性验证](https://www.empyrean.com.cn/products/eda/digital-soc/formal-property-verification.html) · [合见工软 UVS+](https://www.univista-isg.com/site/product_detail/441)

对实际安装的 FormalMC，使用本版帮助查找 witness、counterexample、trace、export、dump、testbench、VCD 和逐拍接口。先选定一条 `C_XAU0_AR` 试跑，逐一查询导出功能与许可反馈。记录命令、退出码、原始文件和可读端口。按站点命令实际指定的工作目录检查输出。

```bash
VENDOR_WORK=/absolute/path/to/vendor-work
find "$VENDOR_WORK" -type f \
  \( -iname '*.vcd' -o -iname '*.vcd.gz' -o -iname '*.usdb' \
     -o -iname '*.fsdb' -o -iname '*.csv' -o -iname '*.txt' \
     -o -iname '*.json' -o -iname '*.sv' \) -print
```

如果现有 FormalMC 批处理沿用 UCAgent 的接口，可在本站点帮助核对 `read_design`、`def_clk`、`def_rst`、`prove` 和 `show_prop` 的含义及参数。先拿到实例化属性和求解报告，再实现轨迹导出。

UCAgent 公开示例的 `fanin -cover -list -dump` 属于 COI 覆盖分析步骤。示例中的 VCD 处理逻辑遍历已有文件。判断 Cover 导出可用性需回到当前安装版本的真实命令和产物。[UCAgent 的原生批处理示例](https://raw.githubusercontent.com/XS-MLVP/UCAgent/ac007684b2c6b1b4b7a1ca6e3090fef174d459fa/ucagent/lang/zh/skills/formal/lib/templates/tests/formal.tcl.j2#L38)

### 其它常见站点入口

- **Synopsys VC Formal FPV**。从站点 FPV 批处理脚本定位原生 RTL 加载、clock / reset、属性选择、结果查询及 trace 导出。记录对应安装版本。[VC Formal](https://www.synopsys.com/verification/static-and-formal-verification/vc-formal.html)
- **Cadence Jasper FPV**。从站点 Jasper FPV 脚本检查 analyze、elaborate、clock 和 reset，在相同属性上下文查询结论及可用 Visualize 导出。[Jasper FPV](https://www.cadence.com/en_US/home/tools/system-design-and-verification/formal-and-static-verification/jasper-core-formal-apps.html)
- **Siemens Questa Formal**。验证现有 Property Verification 流程的编译和属性查询，核对指定工作库、完整层级与 trace 能力。[Questa Formal](https://www.siemens.com/en-us/products/ic/questa-one/formal-verification/)

<!-- pagebreak -->

## 18 按用例验收并留存证据

先跑基准 DUT 的正例，再用独立工程测试故意失败的属性和复杂写法。各行记录任务编号、实际状态、参考报告和产物路径。若某能力不具备，把该行标为待支持并保存诊断。

| 检查项目 | 判定依据 |
| --- | --- |
| 离线请求与协议 | `PREPARED`、输入摘要一致、探针 JSON 正确。参见 03 和 09。 |
| 已知可达 | 基准 `C_XAU0_AR` 命中。记录深度、完整实例和 witness。 |
| 有界内未命中 | 用压测 DUT 的 `C_DELAY_OPEN_BAD`，或已知延迟超过上界的属性。按原始证据区分到界与不可达证明。 |
| Assert 正例和反例 | 参见 `A_R_READY`。另在独立工程增加下方故意失败的 Assert，预期有真实 `COUNTEREXAMPLE` 且任务 `FAIL`。 |
| 原生复杂 Cover | 同一份压测 RTL 检查 `C_DUT_READ_OPEN`、`C_DUT_NESTED_MACRO_READ`、`C_DUT_NESTED_MACRO_LATE_RETIRE` 和负例。记录两层宏在工具中的展开和动作块处理。 |
| VCS 回放及覆盖 | 参见 14 和 15。核对命中、波形、VDB、结果页和联合选集产物。若工具同时有 VCD 和逐拍 API，分别测两条输入路径。 |
| FSDB 验证包 | 多 pattern 或复位段。原生报告、所需标签和全部终点一一对应。 |
| 超时与取消 | 分别观察许可排队与引擎求解超时。用「取消任务」验证当前任务退出。若提交了站点独立作业，由驱动负责撤销对应作业并保存状态。 |

压测源码在 `verification/dut_cover_stress_20261115/sva_cover_stress_dut.sv`。按根目录 README 将副本置于 `simulation/vcs/dut_sources/`，替换三处历史模块名前缀。保留后续两行 RTL 和原例化，在「DUT 集成」换首行并保存。复杂 Cover 的实际结果以原生报告和 VCS 验证为准。

在仅用于反例测试的工程，把下面的断言附加到属性编辑器，保存后按第 10 节方式以 `COMMERCIAL_TARGETS=A_PORT_COUNTEREXAMPLE` 单独运行 `bmc`。联合选集使用同一份基准工程属性。

```systemverilog
A_PORT_COUNTEREXAMPLE: assert property (
    @(posedge clk) disable iff (!rst_n) 1'b0
);
```

<!-- pagebreak -->

## 19 按症状定位故障

| 症状 | 先查证据并执行 |
| --- | --- |
| 找不到工具或 GUI 路径不生效 | 用同一 Bash 执行 `command -v`。检查脚本可执行权限、shebang、Unix 换行、本机配置和导出变量，再从该终端 `make gui`。 |
| `PREPARED` 失败 | 从 `COMMERCIAL_FORMAL_ERROR` 看 DUT 配置、空文件清单、重复宏和快照错误。更正输入后给任务新编号。 |
| 启动或许可失败 | 查 `adapter.log` 和厂商日志，按站点模板单独复验启动、许可签出、工作目录权限。 |
| 找不到属性或名称歧义 | 核对 top、宏控制分支、generate 层级和原生编译后的属性列表。用完整实例名重试，保留同名标签的实际实例表。 |
| 结果摘要或相对路径错误 | 核对原始 `request.json` 的字节 SHA-256、运行编号、快照未改动和任务内实存文件。查 `site-result.tsv` 的列数与绝对 / 相对路径。 |
| 深度不足或结果不完整 | 核对复位起点、实际完成拍、约束可满足性、求解器状态、属性完整性和所需端点。顶层 `INCOMPLETE` 可同时含真实命中行，可按那些行继续回放。 |
| VCS 回放按钮不可用 | 核对已选中一条 `REACHED` 的 DUT Cover、`trace` 或 `stimulus` 存在。查 Linux、本机任务空闲状态和版本是否含该按钮。 |
| VCS 开始失败或快照不同 | 比对 `request.json`、`source/` 与 `build.json` 中的 RTL、宏、检查器、属性和例化。重新保存并对同版本再求解。 |
| Cover 在 VCS 未命中或用例失败 | 对照 `sim.log`、`functional_hits.json`、`drive_audit.json`、`samples.csv`、`clock_reset.csv`。检查采样相位、首帧、时钟复位、端序、环境约束和最后命中帧。 |
| 联合最小集只有部分批次 | 到「结果与波形」逐批查 `coverage.status`，比对功能清单、端口检查器、DUT 与逐用例 VDB。同一版本补跑缺失的普通回归。 |
| NPI、URG 或 FSDB 失败 | 检查 `make doctor`、`urg.log`、NPI 日志、FSDB PLI 或厂商许可。定位到实际产物缺失处再重试。 |
| FSDB 包缺少终点 | 核对各选择器、各复位段、真实初始化、包的建议深度和 `required_labels`。对照厂商原生报告。 |

按请求、厂商编译求解、结果转换、轨迹解码、VCS、覆盖六个阶段定位。对首次失败，保存首次错误行、对应路径和重现命令。一次只改产生该错误的输入或适配步骤。

<!-- pagebreak -->

## 20 交付站点移植结果

交付可执行站点驱动、厂商启动脚本、公共适配器入口和本机启动说明。保留每个映射的依据。设备独有配置放在站点目录、环境变量或本机高级选项中。交付记录只包含可以分享的证据路径和版本。

```text
Studio 版本或包摘要 =
目标 Linux 和工具版本 =
试验 DUT 文件和宏 =
已验证的启动命令及工作目录 =
EDA_COMMERCIAL_FORMAL_ADAPTER =
EDA_COMMERCIAL_FORMAL_DRIVER =
EDA_SITE_NATIVE_FORMAL =
EDA_RESULT_ROOT =
原生批处理与状态映射的位置 =
PREPARED 接口检查 ID =
商业 Cover ID / 目标 / 原生状态和深度 =
命中 witness 类型 / 实例 / 采样映射 / 文件摘要 =
商业 Assert 正例和反例 ID =
VCS 回放批次 ID / 用例 ID / PASS 与命中凭证 =
同版本普通回归批次 ID =
联合最小集 ID / verified / solver.optimal =
选中用例 FSDB 导出目录 =
FSDB 验证包和端点验收 ID =
尚待验证的能力 / 首个错误 / 原生证据路径 =
```

对每个商业任务保留 `request.json`、`source/`、`adapter.log`、`adapter-result.json`、`result.json`、原生结果和轨迹。VCS 保留 `regressions/`，选集保留 `coverage_analysis/`。后续升级时在新任务目录重复 03、09、10、14 和 15。切回原站点配置时，恢复此前使用的驱动路径或取消本次为适配设置的环境变量，原批次仍可从归档查看。
