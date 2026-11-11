"""Create the startup supplement and plot measured FSDB transitions."""
import json
from pathlib import Path
import subprocess
import sys
from reportlab.graphics.shapes import Drawing,String
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics import renderPDF
from reportlab.lib.colors import HexColor
from docx import Document
from docx.shared import Inches,Pt,RGBColor
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1]
data=json.loads((HERE/'gui/figure_source.json').read_text(encoding='utf-8'))
drawing=Drawing(510,220)
for index,(name,color) in enumerate(zip(['clk','rst_n','arvalid'],['#24486c','#247660','#345aff'])):
    values=[(t/1000,int(v,2)) for t,v in data['signals'][name] if t<=30000]
    values.append((30,values[-1][1]))
    step=[values[0]]
    for before,after in zip(values,values[1:]):step.extend([(after[0],before[1]),after])
    plot=LinePlot();plot.x=65;plot.y=165-index*65;plot.width=435;plot.height=43
    plot.data=[step];plot.lines[0].strokeColor=HexColor(color);plot.lines[0].strokeWidth=1.6
    plot.xValueAxis.valueMin=0;plot.xValueAxis.valueMax=30;plot.xValueAxis.valueSteps=[0,1,5,10,12,15,20,25,30]
    plot.xValueAxis.labels.fontSize=9;plot.xValueAxis.visible=(index==2)
    plot.yValueAxis.valueMin=-.2;plot.yValueAxis.valueMax=1.2;plot.yValueAxis.valueSteps=[0,1]
    plot.yValueAxis.labels.fontSize=9
    drawing.add(plot);drawing.add(String(3,plot.y+20,name,fontName='Helvetica',fontSize=12))
drawing.add(String(265,4,'Time (ns)',fontName='Helvetica',fontSize=11))
scratch=ROOT/'build/checks_dev/startup_render';scratch.mkdir(parents=True,exist_ok=True)
plot_pdf=scratch/'timing_plot.pdf';renderPDF.drawToFile(drawing,str(plot_pdf))
poppler=Path(sys.executable).parents[1]/'native/poppler/Library/bin/pdftoppm.exe'
chart=HERE/'startup_timing.png'
subprocess.check_call([str(poppler),'-png','-singlefile','-r','144',str(plot_pdf),str(chart.with_suffix(''))])

doc=Document();sec=doc.sections[0]
sec.page_width=Inches(8.5);sec.page_height=Inches(11)
sec.top_margin=sec.bottom_margin=Inches(.65);sec.left_margin=sec.right_margin=Inches(.7)
for grid in list(sec._sectPr.findall(qn('w:docGrid'))):sec._sectPr.remove(grid)
for name,size,bold in [('Normal',11,False),('Title',22,True),('Heading 1',15,True),('Caption',9,False)]:
    style=doc.styles[name];style.font.name='Arial';style.font.size=Pt(size);style.font.bold=bold;style.font.color.rgb=RGBColor(0,0,0)
    style.element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'),'Microsoft YaHei')
    style.paragraph_format.line_spacing=Pt(size+4);style.paragraph_format.space_after=Pt(6)
    props=style.element.get_or_add_pPr()
    for border in list(props.findall(qn('w:pBdr'))):props.remove(border)
    snap=OxmlElement('w:snapToGrid');snap.set(qn('w:val'),'0');props.append(snap)
doc.styles['Heading 1'].paragraph_format.space_before=Pt(8)
doc.core_properties.title='CMDBUF Studio 时钟与复位启动时序'
doc.core_properties.author='CMDBUF Studio'

def p(text,style=None):return doc.add_paragraph(text,style)
def h(text):return doc.add_heading(text,level=1)
def code(text):
    for line in text.splitlines():
        para=p('');para.paragraph_format.space_after=Pt(1);para.paragraph_format.line_spacing=Pt(13)
        run=para.add_run(line);run.font.name='Consolas';run.font.size=Pt(10)

p('CMDBUF Studio 时钟与复位启动时序','Title')
p('原生 Tk 本机版操作补充  2026 年 11 月 11 日')
p('当前 SV 激励环境从仿真开始运行时钟，clk 初始为 1。低有效 rst_n 在 t=0 拉低，于 #1 释放，之后一直保持 1。Testbench 的时间单位为 1 ns、精度为 1 ps，所以这里的 #1 就是 1 ns。')
h('默认时序')
t=doc.add_table(rows=1,cols=3);t.autofit=False
widths=[.8,1.1,5.2]
for col,width in zip(t.columns,widths):col.width=Inches(width)
for cell,text in zip(t.rows[0].cells,['时间','clk / rst_n','行为']):cell.text=text
for row in [('0 ns','1 / 0','时钟初始高电平，复位有效。'),('1 ns','1 / 1','释放复位，此后 rst_n 一直为 1。'),
            ('5 ns','0 / 1','默认时钟第一次下降。'),('10 ns','1 / 1','首个正常上升沿，启动 main_phase 的 cycle=0。'),
            ('12 ns','1 / 1','经过模板的两段 #1，驱动 cycle=0 输入。'),('20 ns','1 / 1','DUT 采样 cycle=0 的输入；本拍之后准备下一组。')]:
    for cell,text in zip(t.add_row().cells,row):cell.text=text
borders=OxmlElement('w:tblBorders')
for name in ['top','left','bottom','right','insideH','insideV']:
    item=OxmlElement('w:'+name);item.set(qn('w:val'),'single');item.set(qn('w:sz'),'4');item.set(qn('w:color'),'D9D9D9');borders.append(item)
t._tbl.tblPr.append(borders)
for i,row in enumerate(t.rows):
    for j,cell in enumerate(row.cells):
        cell.width=Inches(widths[j]);props=cell._tc.get_or_add_tcPr();mar=OxmlElement('w:tcMar')
        for side in ['top','left','bottom','right']:
            item=OxmlElement('w:'+side);item.set(qn('w:w'),'90');item.set(qn('w:type'),'dxa');mar.append(item)
        props.append(mar)
        if i==0:
            item=OxmlElement('w:shd');item.set(qn('w:fill'),'EAF0F6');props.append(item)
        for para in cell.paragraphs:
            para.paragraph_format.space_after=Pt(2)
            for run in para.runs:run.font.size=Pt(10.5);run.bold=(i==0)
p('下图来自 VCS 实际生成的 FSDB，经 Verdi 转换后提取跳变。首拍正例在 cycle=0 拉高 arvalid，12 ns 完成驱动，并在 20 ns 接受请求。')
para=p('');para.paragraph_format.line_spacing=1
para.add_run().add_picture(str(chart),width=Inches(7.1))
p('实测首拍用例  main_first_cycle  seed 17','Caption')

doc.add_page_break();h('用例和 GUI 中如何使用')
p('在“用例编辑”中继续只修改 main_phase。所有随项目提供的 TC 已改为 rst_n=1；不需要自行编写启动复位。环境在运行 main_phase 之前已建立复位时序，时钟低高时间和低电平暂停仍可通过原有 clk_*_ns 字段设置。')
code('for (int unsigned cycle=0; cycle<num_cycles; cycle++) begin\n    @(posedge clk);\n    #1;\n    begin_cycle(cycle);\n    rst_n = 1;\n    rready = 3\'b111;\n    clk_low_ns = 5; clk_high_ns = 5; clk_pause_ns = 0;\n    drive_control();\n    #1;\n    sample_context();\n    // Keep the template AR / Bank / MEM if blocks here.\n    drive_inputs();\nend')
p('原先的 rst_n=(cycle>=3) 会在运行中再次请求拉低复位，现应改为 rst_n=1。环境会用 MAIN_RESET_FIXED_HIGH_AFTER_1NS 明确拒绝重新拉低的请求，即使关闭断言 fatal 也一样；不会静默改写用户赋值。tc_reset 名称保留用于计划兼容，现检查启动复位和停钟后保留事务，不再中途复位取消事务。')
h('查看记录和重放')
p('“结果与波形”的运行记录增加 clock_reset.csv，保存实际时钟与复位跳变，时间单位为 ps。输入审计会检查初值、1 ns 释放、每个上升沿和下降沿，以及整个运行期间复位保持高电平。关闭波形生成时仍保留该记录。')
code('make run CASE=tc_directed SEED=17 WAVES=1\nmake wave REGRESSION=latest CASE=tc_directed SEED=17\nmake tc-test JOBS=2\nmake replay REGRESSION=latest CASE=tc_directed SEED=17')
p('新归档重放会额外比较 clock_reset.csv 的精确内容。旧归档使用当时保存的仿真二进制和时序，不会被新启动策略改写。说明 38 覆盖说明 32 等旧文档中的多周期复位和中途再次复位示例；历史显式波形回放仍按其文件内容执行。')
h('实际验证')
p('本次启动验收 47 项检查通过，包含 10 个 FSDB 的时钟复位逐跳变核对、首拍有效请求、非法再次复位、9 次通用回归、新旧归档重放，以及 NPI 从 1 ns 复位释放后提取首个 10 ns 工作沿。main_phase 验收为 11 个正例和 13 个预期失败反例；检查模式与 fatal 专项 76 项检查也全部通过。')
p('复位采用当前低有效异步复位接口。工具按要求保留 1 ns 脉冲，不会为了其他 RTL 的同步复位需求自行插入时钟脉冲或延长复位。NPI 已验证生成结果，本次未重新执行 Formal 证明。')
path=ROOT/'documents/38_cmdbuf_startup_20261111.docx';doc.save(str(path));print(path)
