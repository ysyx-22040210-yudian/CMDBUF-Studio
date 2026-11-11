# CMDBUF Studio 工程约定

- 此目录是独立的 CMDBUF Studio 仓库。运行、打包、测试不得依赖同级其他仓库或旧设备的固定目录；设备配置只写入已忽略的 `eda.local.json` 或环境变量。
- 编译、运行、Verdi 波形、Verdi 覆盖率和 NPI 任务保持 Make 入口。产品使用原生 Tk 窗口，直接在 Linux 目标设备执行 Make，不依赖浏览器或 SSH/VM 连接；VM 仅用于开发调试。不得把许可证、设备凭据、EDA 安装或 build/ 缓存提交到 Git。
- 用户要求通过 GitHub Desktop 提交和上传。发布后核对远端提交与文件。
- 用户说明文档使用 PDF 或 Word。新增和修订说明交付 `documents/`，优先同时提供可编辑 Word 与 PDF；README 可作为导航和快速入口。截图必须来自真实运行，导出文档后检查排版。
- 当前用例使用普通 SV task `main_phase`，在 `@(posedge clk)` 后按模板时序使用过程赋值和 `if/else` 实现约束。GUI 编辑器仅显示 main_phase。不得将用户写法擅自改回 WaveDrom、rand/constraint 或 UVM。
- 随机激励严格遵守已登记的协议约束；driver 只校验和驱动，不隐式修正非法值。新增约束需要更新清单、检查器及有意义的正反例，不能宣称自动推断任意 RTL 的全部约束。
- RTL 集成入口为 `rtl/files.f`，当前三份 `.sv` 为基准 DUT。同目录 `.v` 只保留历史语义。RTL 不使用二态 bit；二维 Bank 状态端口保留四态 `input wire [1:0] rd_mem_status [7:0]`。
- 当前协议基准为 `docs/timing/protocol_corrections_20261021.json`；原生检查器及参考模型在 `formal/protocol_20261021/`。端口约束及接口变化必须显式评审，历史证据不可充当新规格的验证结论。
- 原生 SVA 使用 `@(posedge clk) disable iff (!rst_n)`；不能将 disable iff 当作复位初始化。单 XAU R 响应顺序由独立接收 FIFO 检查；无效 arb_addr 和 R 负载不比较。
- FSDB 转 Formal 使用目标设备的 Verdi NPI 直接解析，复位释放后逐上升沿提取，一份 FSDB 一个 pattern；多次复位使用 reset_segment，保留空拍、背压和完整周期号。真实复位初始化、原生 SVA 与执行引擎适配分别说明。
- 现有操作说明在 documents/32 至 36（36 为当前 Tk 本机版入口，33/35 的浏览器与 SSH 界面属于历史版本）。verification/ 内已封存的输入、日志、SHA256 和结论保持不变；新增验证另建目录，明确版本和有限验证范围。
