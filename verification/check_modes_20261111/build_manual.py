"""Author the checking-scope supplement using bundled python-docx."""
from pathlib import Path
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

HERE=Path(__file__).resolve().parent; ROOT=HERE.parents[1]
doc=Document(); sec=doc.sections[0]
sec.page_width=Inches(8.5);sec.page_height=Inches(11)
sec.top_margin=sec.bottom_margin=Inches(.65)
sec.left_margin=sec.right_margin=Inches(.7)
for grid in list(sec._sectPr.findall(qn('w:docGrid'))): sec._sectPr.remove(grid)
for name,size,bold in [('Normal',11,False),('Title',23,True),('Heading 1',16,True),('Heading 2',12,True),('Caption',9,False)]:
    style=doc.styles[name];style.font.name='Arial';style.font.size=Pt(size);style.font.bold=bold;style.font.color.rgb=RGBColor(0,0,0)
    style.element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'),'Microsoft YaHei')
    style.paragraph_format.space_after=Pt(6);style.paragraph_format.space_before=Pt(0)
    style.paragraph_format.line_spacing=Pt(size+4)
    props=style.element.get_or_add_pPr()
    for border in list(props.findall(qn('w:pBdr'))): props.remove(border)
    snap=OxmlElement('w:snapToGrid');snap.set(qn('w:val'),'0');props.append(snap)
doc.styles['Heading 1'].paragraph_format.space_before=Pt(10)
doc.core_properties.title='CMDBUF Studio 检查模式与断言处理'
doc.core_properties.author='CMDBUF Studio'
def p(text,style=None): return doc.add_paragraph(text,style)
def h(text): return doc.add_heading(text,level=1)
def code(text):
    for line in text.splitlines():
        paragraph=p('');paragraph.paragraph_format.space_after=Pt(2)
        run=paragraph.add_run(line);run.font.name='Consolas';run.font.size=Pt(10)
        run._element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'),'Microsoft YaHei')
def table(headers,rows,widths):
    t=doc.add_table(rows=1,cols=len(headers));t.autofit=False
    for col,width in zip(t.columns,widths):col.width=Inches(width)
    for cell,text in zip(t.rows[0].cells,headers):cell.text=text
    for values in rows:
        for cell,text in zip(t.add_row().cells,values):cell.text=text
    for i,row in enumerate(t.rows):
        for j,cell in enumerate(row.cells):
            cell.width=Inches(widths[j]);props=cell._tc.get_or_add_tcPr();mar=OxmlElement('w:tcMar')
            for side in ('top','left','bottom','right'):
                e=OxmlElement('w:'+side);e.set(qn('w:w'),'100');e.set(qn('w:type'),'dxa');mar.append(e)
            props.append(mar)
            if i==0:
                shade=OxmlElement('w:shd');shade.set(qn('w:fill'),'EDF1F6');props.append(shade)
            for paragraph in cell.paragraphs:
                paragraph.paragraph_format.space_after=Pt(3)
                for run in paragraph.runs:run.bold=i==0;run.font.size=Pt(10.5)
    repeat=OxmlElement('w:tblHeader');t.rows[0]._tr.get_or_add_trPr().append(repeat)
def image(name,caption):
    paragraph=p('');paragraph.paragraph_format.line_spacing=1
    run=paragraph.add_run();pic=run.add_picture(str(HERE/'gui'/name),width=Inches(7.1))
    pic._inline.docPr.set('descr',caption);p(caption,'Caption')

p('CMDBUF Studio 检查模式与断言处理','Title')
p('原生 Tk 本机版操作补充  2026 年 11 月 11 日')
p('接入其他 CMDBUF RTL 时，可先选择通用事务检查，按真实握手验证请求和返回；需要验证 20261021 规范的完整逐拍行为时，选择完整时序检查。两种模式都保持已登记输入约束，回归结果明确列出未检查的范围。')
h('一 在回归计划中选择')
image('01_regression_transaction.png','图 1  Linux 实际窗口  验证范围与断言失败立即停止开关')
table(['设置','用途'],[
    ('通用事务检查','默认的新计划模式。无需指定 DUT 内部流水级；按观测到的握手检查事务和数据。'),
    ('完整时序检查 20261021','执行原生 SVA 的全部接口要求，包含组合 pending、窗口选择和精确响应调度。'),
    ('断言失败立即停止','勾选时使用 $fatal；取消后使用 $error 继续记录，最终仍判 FAIL。')],[1.8,5.3])
p('保存计划会保存模式和 fatal 开关。没有新增字段的旧计划继续使用完整时序与 fatal，避免静默放宽原验证范围。用例窗口仍只编辑 main_phase，普通赋值、if 约束和既有驱动时序不变。')

doc.add_page_break();h('二 检查范围与采样含义')
table(['检查内容','通用事务','完整时序'],[
    ('同拍 E03 到 E06 输入约束、req 与 rvalid 独热、R ready、pending 范围、四态检查','检查','检查'),
    ('接受与返回的 ID、port、低地址、数据、错误优先级和单源保序','按实际握手检查','按原生参考模型检查'),
    ('MEM 数据提供时点','成功发射后 N+4','成功发射后 N+4'),
    ('pending 精确数值与更新时点','未检查','组合计入当前接收与发射'),
    ('精确 ready、五项发射窗口、重试补窗、跨源 RR 与最早 N+5 返回','未检查','按 20261021 规范检查')],[3.9,1.5,1.7])
p('通用模式允许在 MEM 捕获拍或之后返回，不规定固定响应周期或最大等待时间。它验证接口可观测的事务一致性；通过不等于符合完整时序规范，也不等于 Formal 证明通过。共享容量仍为九项，接口和 MEM N+4 契约仍需兼容。')
h('保存哪一拍 就检查哪一拍')
p('观察器使用 clocking input #1step 保存上升沿前的完整四态快照，下一上升沿处理上一帧；结束前显式处理最后一帧。已经采样的握手、输入和输出不再用沿后实时值替换。异步复位通过独立的复位序号取消未完成事务，但不会清除先前帧的失败记录。')
p('完整模式的原生并发 SVA 保留自身的 Preponed 采样和原表达式；失败动作使用 $sampled 值记录现场。没有统一增加 #1 延迟，也没有把同拍 |-> 规则改成跨拍 |=>。原生检查与事务观察器都保留原始采样拍号。')
p('如果某拍采样到 pending=0、last_pending=0、ar_fire=1、issue_fire=0，把整组数据保存后仍然不满足 0=0+1。通用模式将该精确递推标为未检查；完整模式继续按组合输出规范报错。工具不会把合法的寄存式输出擅自解释为组合输出，也不会宣称已经验证了未定义的时序。')
h('同地址与重复 ID')
p('下游发射口只有 Bank 和地址，没有事务 ID。监视器为每笔已接受 AR 建立独立 token，保留所有与发射序列相容的对应关系，再用后续返回和独立保存的 MEM 数据排除不相容候选。重复 arid 不充当 token，响应字段仍按该来源的最老请求核对。')
p('候选超过 4096 时，事务部分标为 INCOMPLETE；结束时仍有未返回事务也标为 INCOMPLETE。工具不会任意选取候选、每拍切换延迟解释或把超出判定能力的结果当作通过。')

doc.add_page_break();h('三 关闭 fatal 后怎样查看错误')
p('取消“断言失败立即停止”后，协议和事务断言记录失败并调用 $error，继续执行后续激励。用例完成时只要有断言失败，结果就是 FAIL；进程退出码为零也不能使它变成 PASS。失败用例的波形和原始覆盖率数据库保留，但不参与通过用例的覆盖率合并。')
image('04_nonfatal_failure.png','图 2  实测寄存 pending 变体  完整时序下记录六笔失败并运行至结束')
p('图中的接口与事务检查通过，完整时序失败，fatal 为关。这表示错误被记录且运行继续，不表示错误被忽略。fatal 为开时，在检查器发现并归档失败后立即停止；流水事务检查的发现时刻可能晚于原采样拍，报告仍标出原拍号和时间。')
table(['状态','含义'],[
    ('PASS','本次已启用范围内的检查完成且未发现错误。'),
    ('FAIL','发现断言、输入审计或工具执行错误。'),
    ('NOT_CHECKED','该检查范围在当前模式中明确未启用。'),
    ('INCOMPLETE','观察结束仍有事务、对应关系超限，或相关检查未完成；不得当作通过。')],[1.5,5.6])
p('非法 TC 赋值、驱动时点或所有权错误、文件与工具错误、watchdog 仍会终止。fatal 开关不会允许不合法的激励继续进入 DUT。')

doc.add_page_break();h('四 Make 命令与运行记录')
code('make gui\nmake compile\nmake regress CHECK_MODE=transaction ASSERT_FATAL=0 \\\n    SEEDS=1,2,42 CYCLES=2000 JOBS=2 WAVES=1\nmake run CASE=tc_directed CHECK_MODE=strict ASSERT_FATAL=1\nmake coverage REGRESSION=latest\nmake wave REGRESSION=latest CASE=tc_random SEED=42\nmake replay REGRESSION=latest CASE=tc_random SEED=42\nmake check-test JOBS=2')
p('无计划的 run、regress 和 smoke 默认采用 transaction 与 ASSERT_FATAL=1。显式 Make 参数优先于计划字段。replay 使用归档的检查模式和 fatal 开关，保证不会因当前 GUI 设置变化而改变重放的判定范围。')
table(['文件','内容'],[
    ('check_policy.json','本次请求使用的模式和 fatal 选项。'),
    ('check_frames.csv','完整四态沿前快照，包含原始拍号、时间和复位序号。'),
    ('assertions.jsonl','每笔失败的标签、采样拍号、时间和现场信息。'),
    ('check_runtime.json','完成状态、帧计数、失败数、事务数和候选峰值。'),
    ('check_scope.json','接口、事务、完整时序的分项结论、范围及限制。')],[1.9,5.2])
p('在“结果与波形”的“运行记录”下拉框可以直接查看这些文件。check_frames.csv 的拍号从第一次时钟上升沿开始，包含复位拍；TC 自己的 cycle 从驱动槽开始，两者不是同一个计数器。')
h('五 本次验证范围')
p('已在 VCS O-2018.09-1 与 Verdi O-2018.09-SP2 上执行专项正反例验收：76 项检查通过，包含十五个观察器边界场景和十二个完整 DUT 模式／fatal／错误注入组合。单独执行十二次通用模式回归和八次完整时序回归，均通过；原生 GUI 另实际启动六次用例，并验证 Make 参数、波形开关与 Verdi 覆盖率入口。')
p('专项覆盖沿后 NBA 变化、末拍刷新、异步复位、未知值 X、同地址歧义、重复 ID 越序、同拍出入及重复返回。寄存 pending 变体在通用模式通过，在完整时序模式失败；两种模式都能检出返回 ID 错误。')
p('这些结果是指定工具版本和已执行有限用例的证据。通用模式不能推断任意 RTL 的全部协议，也不验证被标为 NOT_CHECKED 的时序。复位会取消未完成事务；没有已确认最大响应延迟时，排空等待上限属于运行资源策略。')
out=ROOT/'documents/37_cmdbuf_check_modes_20261111.docx';doc.save(str(out));print(out)
