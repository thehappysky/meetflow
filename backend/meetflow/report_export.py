"""Two file formats generated from exactly the same confirmed report blocks."""
from datetime import datetime
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo


def local_time(value):
    return datetime.fromisoformat(value).astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M 北京时间")


def blocks(body):
    yield "title", body["title"]
    yield "text", f"报告期间 {body['start']} 至 {body['end']}　整理人 {body['author']}"
    yield "text", f"模板 {body['template_name']} v{body['template_version']}　确认时间 {body['confirmed_at']}"
    yield "text", "确认编号 " + body["content_id"][:16]
    yield "text", "本报告为本人确认的资料快照，实际日期与上传日期分开。人工补充不等于客户确认；来源后续更正不覆盖此版本。"
    if any(e["mode"] == "demo" for e in body["entries"]):
        yield "text", "包含规则演示生成内容，未经真实语言模型效果验证。"
    for unknown in (False, True):
        entries = [e for e in body["entries"] if bool(e["meeting_date"]) != unknown]
        if not entries:
            continue
        yield "h1", "时间待确认的会谈" if unknown else "期间会谈与推进记录"
        if unknown:
            yield "text", "以下会谈日期未知，不计入期间已发生会谈数量。上传时间仅用于找回资料。"
        for e in entries:
            yield "h2", e["customer"] or "客户待确认"
            yield "text", f"{e['title']}　实际日期 {e['meeting_date'] or '时间待确认'}"
            if e.get("date_supplemented"):
                yield "text", "实际日期由销售在本报告补充，尚未修改来源会谈。"
            yield "text", f"来源 {'个人会谈' if e['kind']=='personal' else '本人已上报会谈'} v{e['revision']}　{'上传' if e['kind']=='personal' else '上报'} {local_time(e['at'])}"
            for label, field in (("会谈摘要", "summary"), ("后续安排与反馈", "next_steps"), ("风险与待确认", "risks")):
                yield "label", label
                yield "text", e[field] or "暂无已记录内容"
            if e.get("note"):
                yield "label", "本人补充"
                yield "text", e["note"]
            if e.get("edits"):
                yield "text", "销售核对修改字段 " + "、".join(e["edits"])
            if e["evidence"]:
                yield "label", "原文依据"
                for index, span in enumerate(e["evidence"].values(), 1):
                    yield "text", f"[{index}] {span['text']}"
    yield "h1", "下阶段计划与需协助事项"
    yield "text", body.get("plan") or "暂无本人补充"


def word_bytes(body):
    from docx import Document
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor
    document = Document()
    section = document.sections[0]
    section.page_width, section.page_height = Cm(21.59), Cm(27.94)
    section.top_margin = section.bottom_margin = Cm(2)
    section.left_margin = section.right_margin = Cm(2.2)
    for name in ("Normal", "Title", "Heading 1", "Heading 2"):
        style = document.styles[name]
        style.font.name = "SimSun"
        style.font.color.rgb = RGBColor(0, 0, 0)
        style.element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "宋体")
        style.paragraph_format.space_after = Pt(7)
    document.styles["Normal"].font.size = Pt(11)
    document.styles["Normal"].paragraph_format.line_spacing = 1.25
    document.styles["Title"].font.size = Pt(22)
    document.styles["Heading 1"].font.size = Pt(15)
    document.styles["Heading 2"].font.size = Pt(12)
    for kind, text in blocks(body):
        style = {"title": "Title", "h1": "Heading 1", "h2": "Heading 2"}.get(kind, "Normal")
        paragraph = document.add_paragraph(text, style=style)
        if kind == "h1" and text == "时间待确认的会谈" and any(e["meeting_date"] for e in body["entries"]):
            paragraph.paragraph_format.page_break_before = True
        if kind == "label":
            paragraph.runs[0].bold = True
            paragraph.paragraph_format.keep_with_next = True
        if kind == "title":
            paragraph.paragraph_format.keep_with_next = True
    # Freeze descriptive properties as well as text; there is no model/file tool access here.
    document.core_properties.title = body["title"]
    document.core_properties.author = body["author"]
    document.core_properties.identifier = body["content_id"]
    settings = document.settings.element
    update = OxmlElement("w:updateFields")
    update.set(qn("w:val"), "false")
    settings.append(update)
    result = BytesIO()
    document.save(result)
    return result.getvalue()


def pdf_bytes(body, font_path=""):
    from hashlib import sha256

    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer
    candidates = [font_path] if font_path else ["C:/Windows/Fonts/simhei.ttf", "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"]
    path = next((p for p in candidates if p and Path(p).is_file()), None)
    if font_path and not path:
        raise ValueError("配置的报告字体不存在")
    if path:
        font = "MeetFlow" + sha256(path.encode()).hexdigest()[:12]
        if font not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(font, path))
    else:
        font = "STSong-Light"
        if font not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(UnicodeCIDFont(font))
    styles = {k: ParagraphStyle(k, fontName=font, fontSize=size, leading=size*1.5,
        wordWrap="CJK", spaceAfter=7, keepWithNext=k in {"title", "h1", "h2", "label"})
        for k, size in (("title",22), ("h1",15), ("h2",12), ("label",11), ("text",11))}
    output = BytesIO()
    doc = SimpleDocTemplate(output, pagesize=(21.59*cm, 27.94*cm), rightMargin=2.2*cm,
        leftMargin=2.2*cm, topMargin=2*cm, bottomMargin=2*cm,
        title=body["title"], author=body["author"])
    flow = []
    for kind, text in blocks(body):
        if kind == "h1" and text == "时间待确认的会谈" and any(e["meeting_date"] for e in body["entries"]):
            flow.append(PageBreak())
        flow.append(Paragraph(escape(text).replace("\n", "<br/>"), styles[kind]))
        if kind in {"title", "h1"}:
            flow.append(Spacer(1, 5))
    doc.build(flow)
    return output.getvalue()
