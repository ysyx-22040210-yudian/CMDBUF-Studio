"""Build the current native desktop manual with the bundled document runtime."""
from pathlib import Path
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT

ROOT=Path(__file__).resolve().parents[2]
EV=Path(__file__).resolve().parent
OUT=ROOT/'documents/36_cmdbuf_native_tk_20261111.docx'
doc=Document();sec=doc.sections[0]
sec.page_width=Inches(8.5);sec.page_height=Inches(11)
sec.top_margin=sec.bottom_margin=Inches(.65)
sec.left_margin=sec.right_margin=Inches(.7)
for name,size,bold in [('Normal',11,False),('Title',24,True),('Heading 1',16,True),('Heading 2',12,True),('Caption',9,False)]:
    s=doc.styles[name];s.font.name='Arial';s.font.size=Pt(size);s.font.bold=bold;s.font.color.rgb=RGBColor(0,0,0)
    s.element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'),'Microsoft YaHei')
    s.paragraph_format.space_after=Pt(7);s.paragraph_format.line_spacing=1.15
doc.styles['Title'].paragraph_format.space_after=Pt(12)
doc.core_properties.title='CMDBUF Studio 原生 Tk 操作手册'
doc.core_properties.author='CMDBUF Studio'
doc.core_properties.subject='Linux 本机自动检测与 EDA 验证流程'

def p(text,style=None):return doc.add_paragraph(text,style)
def h(text):return doc.add_heading(text,level=1)
def code(text):
    for line in text.splitlines():
        paragraph=p('');paragraph.paragraph_format.space_after=Pt(1)
        r=paragraph.add_run(line);r.font.name='Consolas';r.font.size=Pt(9.5)
        r._element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'),'Microsoft YaHei')
def pic(name,caption,width=7.1):
    paragraph=p('');paragraph.paragraph_format.space_after=Pt(4)
    inline=paragraph.add_run().add_picture(str(EV/name),width=Inches(width))
    inline._inline.docPr.set('descr',caption)
    p(caption,'Caption')
def table(headers,rows,widths):
    t=doc.add_table(rows=1,cols=len(headers));t.alignment=WD_TABLE_ALIGNMENT.CENTER;t.autofit=False
    for col,width in zip(t.columns,widths):col.width=Inches(width)
    for cell,text in zip(t.rows[0].cells,headers):cell.text=text
    for values in rows:
        for cell,text in zip(t.add_row().cells,values):cell.text=str(text)
    for i,row in enumerate(t.rows):
        for j,cell in enumerate(row.cells):
            cell.width=Inches(widths[j]);cell.vertical_alignment=WD_CELL_VERTICAL_ALIGNMENT.CENTER
            props=cell._tc.get_or_add_tcPr();m=OxmlElement('w:tcMar')
            for name in ('top','left','bottom','right'):
                e=OxmlElement('w:'+name);e.set(qn('w:w'),'90');e.set(qn('w:type'),'dxa');m.append(e)
            props.append(m);b=OxmlElement('w:tcBorders')
            for name in ('top','left','bottom','right'):
                e=OxmlElement('w:'+name);e.set(qn('w:val'),'single');e.set(qn('w:sz'),'4');e.set(qn('w:color'),'D1D7E0');b.append(e)
            props.append(b)
            if i==0:
                shade=OxmlElement('w:shd');shade.set(qn('w:fill'),'EAEFF5');props.append(shade)
            for paragraph in cell.paragraphs:
                paragraph.paragraph_format.space_after=Pt(2)
                for run in paragraph.runs:run.font.size=Pt(10);run.bold=i==0
    repeat=OxmlElement('w:tblHeader');t.rows[0]._tr.get_or_add_trPr().append(repeat)
    return t
def page():doc.add_page_break()

p('CMDBUF Studio 原生 Tk 操作手册','Title')
p('Linux 本机版  2026 年 11 月 11 日')
p('本版使用 Python Tk / ttk 原生窗口，在目标 Linux 设备上直接调用 VCS、Verdi 和 NPI。工具自动识别工程位置、启动目录及已有 EDA 命令。普通使用无需填写环境脚本或安装目录；浏览器和 SSH 均不属于运行流程。')
h('一 自动识别本机环境')
p('在能够直接执行 vcs、verdi、urg 的 Linux 图形桌面终端，进入解压后的工程目录并运行：')
code('python3 -c "import tkinter; print(tkinter.TkVersion)"\nmake env-info\nmake doctor\nmake smoke\nmake gui')
p('env-info 显示检测结果；doctor 检查工具和组件；smoke 真实运行 tc_directed（seed 1）和 tc_random（seed 17、42），并合并覆盖率。gui 打开原生窗口。全部编译、回归、波形和覆盖率操作继续使用 Make。')
table(['项目','要求'],[
    ('系统与界面','Linux x86_64 图形桌面；有效的 DISPLAY 和桌面授权'),
    ('Python','Python ≥ 3.6 与 Tk ≥ 8.5；GUI 无需 pip 第三方包'),
    ('EDA 工具','VCS、Verdi、URG、fsdb2vcd；有效许可证与 Verdi PLI / NPI'),
    ('构建工具','Bash、GNU Make、与本站点 EDA 版本兼容的 C/C++ 编译器')],[1.35,5.75])
p('若系统缺少 Tk，Ubuntu/Debian 的包名为 python3-tk；RHEL/CentOS 对应包通常为 python3-tkinter。GUI 需要图形会话，但命令行仿真本身可以无桌面运行。')
p('自动检测优先继承启动终端环境，必要时再尝试当前用户的登录环境，并从命令路径推导安装目录。许可证内容不显示。特殊安装才进入“高级选项”显式启用覆盖；覆盖绑定当前设备，旧 VM 和其他设备的配置默认忽略。')

page();h('二 接入 DUT 与编辑用例')
p('“DUT 集成”可导入 RTL 文件或目录。文件复制到当前工程后，核对编译顺序、include 目录和宏定义。在右侧维护例化与端口适配代码，实例名保留 dut，随后保存并编译。其他 CMDBUF 实现应满足当前协议；接口语义变化须同步检查器。')
pic('03_main_phase.png','图 1  Linux 实测的 main_phase 编辑器  保存仅替换 task 内容')
p('“新建用例”选择 tc_user_example 或 tc_random 模板。编辑器只展示 main_phase；Ctrl + S 保存时保留外层 class 与构造函数，并检查外部修改冲突。')
code("@(posedge clk);\n#1;\n// 保留模板的控制驱动和 sample_context 顺序\nif (!rst_n || arready == 3'b000) arvalid = 0;\nelse arvalid = $urandom_range(0, 1);")
p('定向用例用 if (cycle == 8) 等条件指定拍号；随机值只在合法分支中选择。时钟高低电平、暂停与 rst_n 均按模板变量控制。保留 MEM 返回日历与 drive_inputs()；driver 只检查并原样驱动，不替用例修正非法输入。完整协议写法参见说明 32。')

page();h('三 设置每个用例的回归次数')
pic('04_regression.png','图 2  三个启用用例共运行六次  每个用例可设置独立次数和起始 seed')
p('选中一行后，在下方设置启用、运行次数、起始 seed 和主体周期数，点击“应用到此用例”。双击行可切换启用。“保存计划”与“开始回归”会应用当前选中行的编辑值。')
p('运行次数 N 表示 seed 从起始值递增到起始值 + N − 1。示例中 tc_directed 运行 seed 17、18；tc_random 运行 101、102、103；新建定向用例运行 seed 31。')
p('全局可设置并行任务数、单用例超时以及是否生成 FSDB 和 VCD。关闭波形仍保存仿真日志与代码覆盖率，但该轮不能再提供 FSDB 提取。')
code('make compile\nmake regress SEEDS=1,2,42 CYCLES=2000 JOBS=2 WAVES=1\nmake run CASE=tc_directed SEED=17 WAVES=0')
p('底部显示任务阶段和进度，“任务日志”展示实际 Make 命令与输出。执行中不能保存工程修改或启动冲突任务；窗口继续响应查看操作。回归结束后进入结果页，逐例确认 PASS、FAIL 或超时。')

page();h('四 查看波形与代码覆盖率')
pic('06_waveform.png','图 3  Tk Canvas 显示实际 VCD 跳变  支持 X 和 Z 数值以及 A B 游标')
p('在“结果与波形”选择批次及用例，点击“内置波形”。左侧筛选、添加信号；顶部可全览、缩放、平移或输入时间范围。单击设置游标 A，Shift 加单击设置 B。时间单位随波形显示，可切换十六进制、二进制和无符号数。密集跳变会提示放大，以免显示错误边沿。')
p('“Verdi 波形”和“Verdi 覆盖率”分别打开本机 Verdi 的 FSDB 与合并 VDB。覆盖率行展示行、条件、翻转、分支等指标；详细未覆盖代码在 Verdi 中查看。')
code('make wave REGRESSION=latest CASE=tc_random SEED=101\nmake coverage REGRESSION=latest\nmake wave-file FSDB=/absolute/path/to/input.fsdb')
p('内置查看器读取 VCD；导入 FSDB 时调用本机 fsdb2vcd 转换后索引。界面可以直接导入已有 VCD。FSDB 转 Formal 的数值提取使用 NPI 直接解析原始 FSDB，不依赖此转换结果。')

page();h('五 从 FSDB 生成 Formal 代码')
pic('07_formal.png','图 4  导入波形与回归波形共同生成验证包  pattern 分别为 7 和 42')
p('点击“导入 FSDB”，或在结果页选择一个或多个运行并点击“加入 Formal”。每份波形设置唯一的 pattern 编号及 DUT 实例路径。先“解析选中层级”确认信号；端口不同名时通过“端口映射”设置完整路径。')
p('启用所需波形后点击“生成 Formal”。NPI 在每个时钟上升沿之前读取采样值，保留复位释放后的全部周期，包括空拍和背压；多次复位用 reset_segment 分段，trace_cycle 保留周期号。输入生成 assume，应比较的输出生成 assert，并以 pattern == 常量区分用例。')
p('生成后可预览 trace_properties.sv、formal_top.sv、逐拍 CSV、事务汇总与集成说明，再通过原生保存对话框导出完整 ZIP。有效输入或有效输出出现 X/Z、复位前序缺失等异常会阻止生成；无效输出负载保留记录，但按当前协议不强制比较。')
p('验证包生成成功不代表证明通过。接入 Formal 引擎时先建立 DUT 的真实复位初态，再运行断言与终点 cover，检查不可达约束导致的空证明。原生 SVA 与 SBY 适配副本分别提供。详细语义参见说明 34。')

page();h('六 迁移与问题定位')
p('向其他设备分发 packages/ 下的 ZIP 或 tar.gz，或在工程根目录执行 make dist 制作当前版本。新设备解压后直接使用自身终端中的 EDA 命令，无需复制旧机器的地址、环境脚本路径或许可证配置。')
code('python3 simulation/vcs/portable.py --verify\nmake env-info\nmake doctor\nmake smoke\nmake gui')
table(['现象','处理方式'],[
    ('缺少 tkinter','安装目标发行版的 Python Tk 包后重新启动'),
    ('没有 DISPLAY 或无法打开窗口','在本机图形桌面终端启动；检查该会话是否可正常打开其他图形应用'),
    ('工具未被发现','在同一终端确认 vcs、verdi、urg 可运行后重新检测；特殊站点才使用高级覆盖'),
    ('PLI 或 NPI 未就绪','检查本机 Verdi 是否安装对应组件及支持的平台目录'),
    ('运行失败','查看任务日志、逐例 sim.log 和 constraint_failure.json；不要以自动修正掩盖输入约束错误'),
    ('没有回归波形','确认该批次启用了生成波形，WAVES=0 的运行不会补产生 FSDB'),
    ('Windows 上运行 EDA','Windows 原生窗口用于编辑与已有 VCD 查看；将工程放到 Linux 目标设备执行 EDA')],[1.7,5.4])
doc.add_heading('已完成的验证',level=2)
p('本版在 Linux Python 3.6.8、Tk 8.5.13、VCS O-2018.09-1 和 Verdi O-2018.09-SP2 上运行真实 GUI 与 Make 流程。自动检测测试未提供 EDA_TOOL_SETUP、VCS_HOME 或 VERDI_HOME，未创建机器配置文件，仅使用现有 PATH 中的工具。')
p('六次带波形回归与一次关闭波形回归均通过；Verdi 波形、覆盖率和原生画布成功打开。两份 FSDB 生成 2 个 pattern、344 个采样周期、344 组输入 assume、344 组输出 assert 和 2 个终点 cover。1280×700 窗口通过滚动仍可访问页面底部。')
p('verification/tk_studio_20261111/ 保存当前验收日志、截图与文件签名。此前封存的验证记录保持不变。此次验收验证 GUI 与工具集成，不扩大为任意 EDA 版本或所有输入的完备证明。说明 33、35 的浏览器和 SSH 流程属于历史版本，当前以本手册为准。')

footer=sec.footer.paragraphs[0];footer.alignment=WD_ALIGN_PARAGRAPH.CENTER
run=footer.add_run();run.font.size=Pt(9)
field=OxmlElement('w:fldSimple');field.set(qn('w:instr'),'PAGE');run._r.addnext(field)
doc.save(str(OUT));print(OUT)
