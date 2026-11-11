"""Generate the short user supplement with the bundled document runtime."""
from pathlib import Path
from docx import Document
from docx.shared import Inches,Pt,RGBColor
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT

root=Path(__file__).resolve().parents[2]
doc=Document();sec=doc.sections[0]
sec.page_width=Inches(8.5);sec.page_height=Inches(11)
sec.top_margin=sec.bottom_margin=Inches(.65);sec.left_margin=sec.right_margin=Inches(.7)
for grid in list(sec._sectPr.findall(qn('w:docGrid'))):sec._sectPr.remove(grid)
for name,size,bold in [('Normal',11,False),('Title',22,True),('Heading 1',14,True)]:
    style=doc.styles[name];style.font.name='Arial';style.font.size=Pt(size);style.font.bold=bold;style.font.color.rgb=RGBColor(0,0,0)
    style.element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'),'Microsoft YaHei')
    style.paragraph_format.line_spacing=Pt(size+4);style.paragraph_format.space_after=Pt(6)
    props=style.element.get_or_add_pPr()
    for border in list(props.findall(qn('w:pBdr'))):props.remove(border)
    snap=OxmlElement('w:snapToGrid');snap.set(qn('w:val'),'0');props.append(snap)
doc.styles['Heading 1'].paragraph_format.space_before=Pt(9)
doc.core_properties.title='CMDBUF Studio License 等待与执行超时'
doc.core_properties.author='CMDBUF Studio'
p=doc.add_paragraph
h=lambda text:doc.add_heading(text,level=1)
p('License 等待与执行超时','Title')
p('CMDBUF Studio · 原生 Tk 本机版补充 39 · 2026 年 11 月 11 日')
p('VCS 编译已加入 -lca。回归会自动排队等待许可证，等待时间没有上限；无需在用例或 GUI 中手动填写等待参数。所有操作继续通过本机 Make 执行。')
h('自动使用的命令参数')
t=doc.add_table(rows=1,cols=3);t.autofit=False
widths=[.9,3.6,2.6]
for col,width in zip(t.columns,widths):col.width=Inches(width)
rows=[['阶段','自动参数','等待与进程期限'],
      ['VCS 编译','-lca -licqueue\n+vcs+lic+wait','许可证排队；编译进程无外层超时。'],
      ['simv 仿真','+vcs+lic+wait','启动前不限时；启动后才计算执行超时。'],
      ['URG 合并','+urg+lic+wait','许可证排队；合并进程无外层超时。']]
for i,values in enumerate(rows):
    row=t.rows[0] if i==0 else t.add_row()
    for j,(cell,text) in enumerate(zip(row.cells,values)):
        cell.text=text;cell.width=Inches(widths[j]);cell.vertical_alignment=WD_CELL_VERTICAL_ALIGNMENT.CENTER
        props=cell._tc.get_or_add_tcPr();mar=OxmlElement('w:tcMar')
        for side in ['top','left','bottom','right']:
            item=OxmlElement('w:'+side);item.set(qn('w:w'),'90');item.set(qn('w:type'),'dxa');mar.append(item)
        props.append(mar)
        if i==0:
            item=OxmlElement('w:shd');item.set(qn('w:fill'),'EAF0F6');props.append(item)
        for para in cell.paragraphs:
            para.paragraph_format.space_after=Pt(1)
            for run in para.runs:run.font.size=Pt(10.5);run.bold=(i==0)
borders=OxmlElement('w:tblBorders')
for side in ['top','left','bottom','right','insideH','insideV']:
    item=OxmlElement('w:'+side);item.set(qn('w:val'),'single');item.set(qn('w:sz'),'4');item.set(qn('w:color'),'D9D9D9');borders.append(item)
t._tbl.tblPr.append(borders)
h('TIMEOUT 只约束启动后的执行')
p('默认 TIMEOUT=120：VCS 触发 cbStartOfSimulation 后，才开始计算 120 秒执行时间。排队和启动时间不计入；这是实际经过的秒数，与 SV 仿真时间、#1 或周期数不同。TIMEOUT=0 可关闭执行超时。启动回调不驱动 DUT，也不增加仿真时间延迟。')
for line in ['make compile','make regress SEEDS=1,2,42 JOBS=2 WAVES=1 TIMEOUT=120','make regress SEEDS=1,2,42 JOBS=2 WAVES=1 TIMEOUT=0']:
    para=p('');para.paragraph_format.line_spacing=Pt(13);para.paragraph_format.space_after=Pt(1)
    run=para.add_run(line);run.font.name='Consolas';run.font.size=Pt(10)
h('GUI 与运行记录')
p('回归计划中的“仿真执行超时 / 秒”可填 0。任务状态区分等待 License / 启动与正在执行，并显示并行任务数量。等待不会计作 FAIL 或 TIMEOUT；实际启动后超时仍判 TIMEOUT。')
p('“结果与波形”可打开 sim.process.json，查看 startup_wait_seconds、execution_seconds 和超时范围。启动等待耗时包含许可证排队及进程初始化，不能当作精确的许可证排队时长。')
h('旧结果重放与验证范围')
p('旧归档没有启动回调时，保留原二进制并关闭整个重放进程的超时，日志提示 LEGACY_LICENSE_WAIT。新归档重放保留启动回调校验及执行超时；输入、时钟复位和完整采样均已核对一致。')
p('Linux 57 项单元测试、76 项检查模式与 fatal 检查、11 个 TC 正例及 13 个预期失败反例通过。真实 VCS / URG 回归已验证参数和结果；长等待用受控延迟启动测试验证，未人为耗尽或更改许可证服务器。')
path=root/'documents/39_cmdbuf_license_wait_20261111.docx';doc.save(str(path));print(path)
