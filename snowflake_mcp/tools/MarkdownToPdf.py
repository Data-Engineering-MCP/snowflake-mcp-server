import os
import re
import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class MarkdownToPdf:
    def convert_md_to_pdf(
        self,
        file_path: Optional[str] = None,
        markdown_content: Optional[str] = None,
        output_path: Optional[str] = None,
    ) -> dict:
        """Convert a Markdown file or string to PDF using reportlab (pure Python, no system deps).

        Args:
            file_path: Path to a .md file to read.
            markdown_content: Raw markdown string (used if file_path not given, or appended).
            output_path: Destination path for the PDF. Defaults to same dir as input file
                         (or cwd) with .pdf extension.

        Returns:
            dict with keys: success, output_path, file_size_kb, execution_time
        """
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
            from reportlab.lib.units import mm
            from reportlab.lib import colors
            from reportlab.platypus import (
                SimpleDocTemplate, Paragraph, Spacer, Preformatted,
                Table, TableStyle, HRFlowable
            )
        except ImportError as e:
            return {
                "success": False,
                "error": f"Missing dependency: {e}. Install with: pip install reportlab",
                "output_path": None,
                "file_size_kb": 0,
                "execution_time": 0,
            }

        start = time.time()

        if file_path is None and markdown_content is None:
            return {
                "success": False,
                "error": "Provide at least one of file_path or markdown_content.",
                "output_path": None,
                "file_size_kb": 0,
                "execution_time": 0,
            }

        # Read source
        if file_path:
            file_path = os.path.expanduser(file_path)
            if not os.path.isfile(file_path):
                return {
                    "success": False,
                    "error": f"File not found: {file_path}",
                    "output_path": None,
                    "file_size_kb": 0,
                    "execution_time": 0,
                }
            with open(file_path, "r", encoding="utf-8") as f:
                source = f.read()
            if markdown_content:
                source = source + "\n\n" + markdown_content
        else:
            source = markdown_content

        # Determine output path
        if output_path:
            out = os.path.expanduser(output_path)
        elif file_path:
            out = os.path.splitext(file_path)[0] + ".pdf"
        else:
            out = os.path.join(os.getcwd(), "output.pdf")

        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)

        # ── Build reportlab styles ──────────────────────────────────────────
        base_styles = getSampleStyleSheet()

        def _style(name, parent="Normal", **kwargs):
            return ParagraphStyle(name, parent=base_styles[parent], **kwargs)

        styles = {
            "h1": _style("H1", "Heading1", fontSize=22, spaceAfter=8, textColor=colors.HexColor("#1a1a2e")),
            "h2": _style("H2", "Heading2", fontSize=17, spaceAfter=6, textColor=colors.HexColor("#16213e")),
            "h3": _style("H3", "Heading3", fontSize=14, spaceAfter=4, textColor=colors.HexColor("#0f3460")),
            "h4": _style("H4", "Heading4", fontSize=12, spaceAfter=3),
            "h5": _style("H5", "Heading5", fontSize=11, spaceAfter=3),
            "h6": _style("H6", "Heading6", fontSize=10, spaceAfter=3),
            "body": _style("Body", fontSize=10, spaceAfter=6, leading=15),
            "bullet": _style("Bullet", fontSize=10, spaceAfter=3, leading=14,
                             leftIndent=14, bulletIndent=0),
            "code_inline": _style("CodeInline", fontSize=9, fontName="Courier",
                                  backColor=colors.HexColor("#f4f4f4")),
            "blockquote": _style("Blockquote", fontSize=10, leftIndent=20,
                                 textColor=colors.HexColor("#555555"), spaceAfter=6),
        }
        code_style = ParagraphStyle(
            "CodeBlock", fontName="Courier", fontSize=8.5, leading=13,
            backColor=colors.HexColor("#f6f8fa"), leftIndent=10, rightIndent=10,
            spaceBefore=4, spaceAfter=4,
        )

        # ── Parse markdown lines into flowables ────────────────────────────
        story = []
        lines = source.split("\n")
        i = 0

        def escape_xml(text):
            return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        def inline_format(text):
            """Convert inline markdown (bold, italic, code, links) to reportlab XML.

            Code spans are extracted first so italic/bold regexes can't interleave
            with <font> tags (reportlab rejects mis-nested XML).
            """
            # Step 1: pull out inline code spans and replace with placeholders
            code_spans = []
            def stash_code(m):
                idx = len(code_spans)
                code_spans.append(escape_xml(m.group(1)))
                return f"\x00CODE{idx}\x00"
            text = re.sub(r'`(.+?)`', stash_code, text)

            # Step 2: escape remaining XML special chars
            text = escape_xml(text)

            # Step 3: links — keep label only (before italic so [] isn't misread)
            text = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', text)

            # Step 4: bold+italic, bold, italic
            text = re.sub(r'\*\*\*(.+?)\*\*\*', r'<b><i>\1</i></b>', text)
            text = re.sub(r'___(.+?)___', r'<b><i>\1</i></b>', text)
            text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
            text = re.sub(r'__(.+?)__', r'<b>\1</b>', text)
            text = re.sub(r'\*(.+?)\*', r'<i>\1</i>', text)
            text = re.sub(r'(?<!\w)_(.+?)_(?!\w)', r'<i>\1</i>', text)

            # Step 5: restore code spans
            for idx, code in enumerate(code_spans):
                text = text.replace(
                    f"\x00CODE{idx}\x00",
                    f'<font name="Courier" size="9">{code}</font>'
                )
            return text

        while i < len(lines):
            line = lines[i]

            # ── Fenced code block ──────────────────────────────────────────
            if line.startswith("```"):
                code_lines = []
                i += 1
                while i < len(lines) and not lines[i].startswith("```"):
                    code_lines.append(lines[i])
                    i += 1
                code_text = "\n".join(code_lines)
                story.append(Preformatted(code_text, code_style))
                story.append(Spacer(1, 4))
                i += 1
                continue

            # ── Headings ──────────────────────────────────────────────────
            heading_match = re.match(r'^(#{1,6})\s+(.*)', line)
            if heading_match:
                level = len(heading_match.group(1))
                text = inline_format(heading_match.group(2))
                key = f"h{level}"
                story.append(Paragraph(text, styles[key]))
                if level <= 2:
                    story.append(HRFlowable(width="100%", thickness=0.5,
                                            color=colors.HexColor("#dddddd"), spaceAfter=4))
                i += 1
                continue

            # ── Horizontal rule ────────────────────────────────────────────
            if re.match(r'^(-{3,}|_{3,}|\*{3,})$', line.strip()):
                story.append(HRFlowable(width="100%", thickness=0.8,
                                        color=colors.HexColor("#cccccc"), spaceAfter=6))
                i += 1
                continue

            # ── Table (GFM) ────────────────────────────────────────────────
            if "|" in line and i + 1 < len(lines) and re.match(r'^[\|\s\-:]+$', lines[i + 1]):
                table_rows = []
                while i < len(lines) and "|" in lines[i]:
                    raw = lines[i].strip().strip("|")
                    cells = [c.strip() for c in raw.split("|")]
                    table_rows.append(cells)
                    i += 1
                # Remove separator row (---)
                table_rows = [r for r in table_rows if not all(re.match(r'^[-:]+$', c) for c in r)]
                if table_rows:
                    max_cols = max(len(r) for r in table_rows)
                    # Pad rows
                    data = [r + [""] * (max_cols - len(r)) for r in table_rows]
                    # Wrap cells in Paragraph for word-wrap
                    para_data = []
                    for row_idx, row in enumerate(data):
                        para_row = []
                        for cell in row:
                            s = styles["h4"] if row_idx == 0 else styles["body"]
                            para_row.append(Paragraph(inline_format(cell), s))
                        para_data.append(para_row)

                    col_width = (A4[0] - 40 * mm) / max_cols
                    tbl = Table(para_data, colWidths=[col_width] * max_cols)
                    tbl.setStyle(TableStyle([
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eaf6")),
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#1a1a2e")),
                        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
                        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
                         [colors.white, colors.HexColor("#f9f9f9")]),
                        ("TOPPADDING", (0, 0), (-1, -1), 5),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                        ("LEFTPADDING", (0, 0), (-1, -1), 6),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ]))
                    story.append(tbl)
                    story.append(Spacer(1, 8))
                continue

            # ── Blockquote ─────────────────────────────────────────────────
            if line.startswith("> "):
                text = inline_format(line[2:].strip())
                story.append(Paragraph(f"<i>{text}</i>", styles["blockquote"]))
                i += 1
                continue

            # ── Unordered list ─────────────────────────────────────────────
            ul_match = re.match(r'^(\s*)([-*+])\s+(.*)', line)
            if ul_match:
                indent = len(ul_match.group(1))
                text = inline_format(ul_match.group(3))
                bullet_style = _style(f"Bullet{indent}", "Normal", fontSize=10,
                                      leading=14, leftIndent=14 + indent * 10,
                                      spaceAfter=2)
                story.append(Paragraph(f"• {text}", bullet_style))
                i += 1
                continue

            # ── Ordered list ───────────────────────────────────────────────
            ol_match = re.match(r'^(\s*)\d+[.)]\s+(.*)', line)
            if ol_match:
                indent = len(ol_match.group(1))
                num = len([x for x in story if isinstance(x, Paragraph)
                           and x.style.name.startswith("OL")]) + 1
                text = inline_format(ol_match.group(2))
                ol_style = _style(f"OL{indent}", "Normal", fontSize=10,
                                  leading=14, leftIndent=14 + indent * 10,
                                  spaceAfter=2)
                story.append(Paragraph(f"{num}. {text}", ol_style))
                i += 1
                continue

            # ── Blank line ─────────────────────────────────────────────────
            if line.strip() == "":
                story.append(Spacer(1, 5))
                i += 1
                continue

            # ── Normal paragraph ───────────────────────────────────────────
            story.append(Paragraph(inline_format(line), styles["body"]))
            i += 1

        # ── Render PDF ─────────────────────────────────────────────────────
        doc = SimpleDocTemplate(
            out,
            pagesize=A4,
            leftMargin=20 * mm,
            rightMargin=20 * mm,
            topMargin=22 * mm,
            bottomMargin=22 * mm,
        )
        doc.build(story)

        size_kb = round(os.path.getsize(out) / 1024, 2)
        elapsed = round(time.time() - start, 3)

        return {
            "success": True,
            "output_path": os.path.abspath(out),
            "file_size_kb": size_kb,
            "execution_time": elapsed,
        }
