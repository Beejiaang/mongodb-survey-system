"""Export the report's Markdown paragraphs, tables and architecture diagram to PDF.

Requires reportlab. Default fonts use Windows SimSun and SimHei; provide TrueType
font paths explicitly on other systems. Unknown Markdown blocks fail visibly.
"""

import argparse
from html import escape
from pathlib import Path
import re
from urllib.parse import quote, urljoin

from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)


ROOT = Path(__file__).resolve().parents[1]
REPO = "https://github.com/Beejiaang/mongodb-survey-system/blob/main/"
INK = colors.HexColor("#243c35")
GREEN = colors.HexColor("#25574b")
GRAY = colors.HexColor("#63706a")
WIDTH = A4[0] - 92


def inline(text, base_url=REPO + "docs/"):
    """Escape source text and support only the inline forms used by this report."""
    parts = []
    pattern = r"(`[^`]+`|\[[^\]]+\]\([^)]+\)|\*\*[^*]+\*\*)"
    for token in re.split(pattern, text):
        if token.startswith("`") and token.endswith("`"):
            parts.append('<font color="#25574b">' + escape(token[1:-1]) + "</font>")
        elif token.startswith("[") and (match := re.fullmatch(r"\[([^\]]+)\]\(([^)]+)\)", token)):
            label, target = match.groups()
            if not target.startswith(("https://", "http://", "#")):
                target = urljoin(base_url, quote(target, safe="/"))
            parts.append('<a color="#25574b" href="' + escape(target, quote=True) + '">' + escape(label) + "</a>")
        elif token.startswith("**") and token.endswith("**"):
            parts.append('<font name="ReportHeading">' + escape(token[2:-2]) + "</font>")
        else:
            parts.append(escape(token))
    return "".join(parts)


def architecture(source):
    # Render the report's existing Mermaid graph as vector nodes and arrows.
    edges = re.findall(r"^\s*([A-Z])(?:\[[^\]]+\])?\s*-->\s*([A-Z])", source, re.M)
    expected = [("B", "V"), ("V", "L"), ("L", "P"), ("P", "U"),
                ("P", "S"), ("P", "R"), ("V", "T"), ("T", "B")]
    if edges != expected:
        raise ValueError("Architecture graph changed; update its PDF renderer first")
    d = Drawing(WIDTH, 252)
    boxes = {
        "B": (80, 218, ["浏览器", "创建、填写、统计"]),
        "V": (250, 218, ["Flask路由", "登录、权限、表单、CSRF"]),
        "L": (420, 218, ["业务逻辑", "校验、跳转、进度、提交、统计"]),
        "T": (80, 130, ["Jinja模板", "本地SVG图表"]),
        "P": (420, 130, ["PyMongo", "条件更新与聚合"]),
        "U": (80, 35, ["users", "用户账号"]),
        "S": (250, 35, ["surveys", "问卷配置"]),
        "R": (420, 35, ["responses", "填写与答案"]),
    }
    def arrow(x1, y1, x2, y2):
        import math
        angle = math.atan2(y2-y1, x2-x1)
        d.add(Line(x1, y1, x2, y2, strokeColor=GRAY, strokeWidth=0.8))
        p = [x2, y2]
        for delta in (-0.45, 0.45):
            p.extend([x2-6*math.cos(angle+delta), y2-6*math.sin(angle+delta)])
        d.add(Polygon(p, fillColor=GRAY, strokeColor=None))
    arrow(153,218,177,218)
    arrow(323,218,347,218)
    arrow(420,195,420,153)
    arrow(210,195,119,153)
    arrow(80,153,80,195)
    # The database fan-out has separate arrowheads at each collection.
    d.add(Line(420,107,420,82,strokeColor=GRAY,strokeWidth=0.8))
    d.add(Line(80,82,420,82,strokeColor=GRAY,strokeWidth=0.8))
    for x in (80,250,420):
        arrow(x,82,x,58)
    for x, y, lines in boxes.values():
        d.add(Rect(x-73,y-23,146,46,rx=5,ry=5,
                   fillColor=colors.HexColor("#f1f6f3"),strokeColor=colors.HexColor("#b9cbc3"),strokeWidth=0.7))
        for i,line in enumerate(lines):
            d.add(String(x,y+6-i*14,line,fontName="ReportHeading" if i==0 else "ReportText",
                         fontSize=9.5 if i==0 else 8.3,fillColor=INK,textAnchor="middle"))
    return d


def export(source, output, font, heading_font):
    source = source.resolve()
    base_url = REPO + quote(source.parent.relative_to(ROOT).as_posix(), safe="/") + "/"
    render_inline = lambda text: inline(text, base_url)
    pdfmetrics.registerFont(TTFont("ReportText", str(font), subfontIndex=0))
    pdfmetrics.registerFont(TTFont("ReportHeading", str(heading_font), subfontIndex=0))
    styles = {
        "body": ParagraphStyle("body",fontName="ReportText",fontSize=10.5,leading=17,
                               textColor=INK,spaceAfter=9,wordWrap="CJK",splitLongWords=True),
        "title": ParagraphStyle("title",fontName="ReportHeading",fontSize=21,leading=30,
                                textColor=GREEN,spaceBefore=10,spaceAfter=16,wordWrap="CJK",keepWithNext=True),
        "h2": ParagraphStyle("h2",fontName="ReportHeading",fontSize=15,leading=22,
                             textColor=GREEN,spaceBefore=17,spaceAfter=9,wordWrap="CJK",keepWithNext=True),
        "h3": ParagraphStyle("h3",fontName="ReportHeading",fontSize=12,leading=18,
                             textColor=GREEN,spaceBefore=11,spaceAfter=7,wordWrap="CJK",keepWithNext=True),
        "cell": ParagraphStyle("cell",fontName="ReportText",fontSize=8.6,leading=13.5,
                               textColor=INK,wordWrap="CJK",spaceAfter=0,splitLongWords=True),
        "header": ParagraphStyle("header",fontName="ReportHeading",fontSize=9,leading=14,
                                 textColor=GREEN,wordWrap="CJK",spaceAfter=0),
    }
    story=[]
    lines=source.read_text(encoding="utf-8").splitlines()
    i=0
    while i<len(lines):
        line=lines[i].strip()
        if not line:
            i+=1; continue
        if line.startswith("```"):
            kind=line[3:]; block=[]; i+=1
            while i<len(lines) and not lines[i].strip().startswith("```"):
                block.append(lines[i]); i+=1
            if i==len(lines) or kind!="mermaid":
                raise ValueError("Unsupported or unterminated code block")
            story.append(KeepTogether([architecture("\n".join(block)),Spacer(1,8)]))
            i+=1; continue
        if line.startswith("|"):
            rows=[]
            while i<len(lines) and lines[i].strip().startswith("|"):
                row=[x.strip() for x in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-+:?",x) for x in row):
                    rows.append(row)
                i+=1
            n=len(rows[0])
            if not all(len(r)==n for r in rows):
                raise ValueError("Inconsistent Markdown table columns")
            proportions={2:[0.25,0.75],3:[0.22,0.36,0.42],4:[0.19,0.25,0.30,0.26]}
            widths=[WIDTH*x for x in proportions.get(n,[1/n]*n)]
            table=Table([[Paragraph(render_inline(x),styles["header" if ri==0 else "cell"])
                          for x in row] for ri,row in enumerate(rows)],colWidths=widths,repeatRows=1,hAlign="LEFT")
            table.setStyle(TableStyle([
                ("VALIGN",(0,0),(-1,-1),"TOP"),
                ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#e8f0ec")),
                ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,colors.HexColor("#f7f9f8")]),
                ("LINEBELOW",(0,0),(-1,0),0.7,colors.HexColor("#9db9ac")),
                ("LINEBELOW",(0,1),(-1,-1),0.3,colors.HexColor("#dce5df")),
                ("LEFTPADDING",(0,0),(-1,-1),7),("RIGHTPADDING",(0,0),(-1,-1),7),
                ("TOPPADDING",(0,0),(-1,-1),7),("BOTTOMPADDING",(0,0),(-1,-1),7),
            ]))
            story.extend([table,Spacer(1,9)]); continue
        if match:=re.match(r"^(#{1,3})\s+(.+)$",line):
            level=len(match[1]); story.append(Paragraph(render_inline(match[2]),styles[{1:"title",2:"h2",3:"h3"}[level]]))
            i+=1; continue
        if re.match(r"^(?:[-*] |\d+\. |>|#{4})",line):
            raise ValueError("Unsupported Markdown block: "+line)
        paragraph=[line]; i+=1
        while i<len(lines) and lines[i].strip() and not lines[i].lstrip().startswith(("#","|","```")):
            paragraph.append(lines[i].strip()); i+=1
        story.append(Paragraph(render_inline(" ".join(paragraph)),styles["body"]))
    output.parent.mkdir(parents=True,exist_ok=True)
    title = next(line[2:].strip() for line in lines if line.startswith("# "))
    stage = "第二阶段" if "第二阶段" in title else "第一阶段"
    doc=SimpleDocTemplate(str(output),pagesize=A4,rightMargin=46,leftMargin=46,
                          topMargin=48,bottomMargin=45,title=title,
                          author="",subject=f"当代数据管理系统第一次大作业，{stage}项目完成报告")
    def page(canvas,doc):
        canvas.saveState()
        canvas.setFont("ReportText",8); canvas.setFillColor(GRAY)
        canvas.drawString(46,A4[1]-28,"当代数据管理系统 · 第一次大作业")
        canvas.setStrokeColor(colors.HexColor("#d6e1da")); canvas.setLineWidth(0.4)
        canvas.line(46,A4[1]-35,A4[0]-46,A4[1]-35)
        canvas.drawCentredString(A4[0]/2,25,f"第 {doc.page} 页")
        canvas.restoreState()
    doc.build(story,onFirstPage=page,onLaterPages=page)
    print(output)


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source",type=Path,default=ROOT/"docs/项目完成报告.md")
    parser.add_argument("--output",type=Path,default=ROOT/"output/pdf/项目完成报告.pdf")
    parser.add_argument("--font",type=Path,default=Path("C:/Windows/Fonts/simsun.ttc"))
    parser.add_argument("--heading-font",type=Path,default=Path("C:/Windows/Fonts/simhei.ttf"))
    args=parser.parse_args()
    export(args.source,args.output,args.font,args.heading_font)
