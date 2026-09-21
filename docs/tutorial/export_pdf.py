#!/usr/bin/env python3
"""Export the deep tutorial Markdown as a readable, image-safe A4 PDF."""

from __future__ import annotations

from datetime import date
import os
from pathlib import Path
import re

from bs4 import BeautifulSoup, Tag
import fitz
import markdown


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "OPS_AGENT_CHAT_DEEP_TUTORIAL.md"
OUTPUT = ROOT / "OPS_AGENT_CHAT_DEEP_TUTORIAL.pdf"
FONT_FILE = Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc")
PAGE = fitz.paper_rect("a4")
CONTENT = fitz.Rect(32, 42, PAGE.width - 32, PAGE.height - 62)

CSS = r"""
@font-face {
  font-family: "WQY";
  src: url("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc");
}
html, body {
  font-family: "WQY", "DejaVu Sans", sans-serif;
  color: #172033;
  font-size: 10.2pt;
  line-height: 1.48;
  margin: 0;
  padding: 0;
  width: 100%;
}
h1 {
  color: #10233f;
  font-size: 21pt;
  margin: 18pt 0 10pt;
  border-bottom: 1.2pt solid #b8d3ef;
  padding-bottom: 5pt;
  page-break-after: avoid;
}
h2 {
  color: #174f88;
  font-size: 15.5pt;
  margin: 15pt 0 7pt;
  border-bottom: 0.7pt solid #c9d9ea;
  padding-bottom: 4pt;
  page-break-after: avoid;
}
h3 {
  color: #1f5d95;
  font-size: 12pt;
  margin: 11pt 0 5pt;
  page-break-after: avoid;
}
p {
  margin: 5pt 0 7pt;
}
ul, ol {
  margin: 4pt 0 8pt 18pt;
  padding-left: 9pt;
}
li {
  margin: 2pt 0;
}
a {
  color: #0563c1;
  text-decoration: underline;
}
blockquote {
  margin: 7pt 0 10pt;
  padding: 7pt 10pt;
  border-left: 3pt solid #5ba7ed;
  background: #eef6ff;
  color: #43556b;
}
code {
  font-family: "DejaVu Sans Mono", monospace;
  font-size: 8.4pt;
  background: #f0f4f8;
  color: #18324e;
}
pre {
  font-family: "DejaVu Sans Mono", monospace;
  font-size: 7.7pt;
  line-height: 1.34;
  white-space: pre-wrap;
  background: #f4f7fa;
  border: 0.7pt solid #d4dee8;
  padding: 8pt;
  margin: 7pt 0 9pt;
  page-break-inside: avoid;
}
pre code {
  background: transparent;
}
table {
  width: 100%;
  border-collapse: collapse;
  margin: 7pt 0 10pt;
  font-size: 8.5pt;
  page-break-inside: auto;
}
tr {
  page-break-inside: avoid;
  page-break-after: auto;
}
th, td {
  border: 0.6pt solid #c8d6e5;
  padding: 4pt 5pt;
  vertical-align: top;
}
th {
  background: #ffffff;
  color: #183d65;
  font-weight: bold;
}
.toc {
  page-break-after: always;
  font-size: 8.5pt;
  line-height: 1.12;
}
.toc-heading {
  color: #174f88;
  font-size: 17pt;
  font-weight: bold;
  border-bottom: 1pt solid #c9d9ea;
  padding-bottom: 5pt;
  margin: 0 0 6pt;
  page-break-after: avoid;
}
.toc ul {
  margin: 0 0 0 10pt;
  padding-left: 7pt;
}
.toc li {
  margin: 0.6pt 0;
}
.toc > ul {
  margin-left: 0;
  padding-left: 0;
  list-style: none;
}
.diagram-intro {
  page-break-inside: avoid;
}
.diagram-page-break {
  page-break-before: always;
}
.diagram-block {
  width: 100%;
  margin: 4pt 0 10pt;
  padding: 0;
  page-break-inside: avoid;
}
.diagram-block img {
  display: block;
  width: 100%;
  height: auto;
  margin: 0;
  padding: 0;
}
.codehilite {
  page-break-inside: avoid;
}
"""


def _build_html(source: str) -> str:
    body_source = re.sub(r"^# .+?\n", "", source, count=1)
    converter = markdown.Markdown(
        extensions=["extra", "sane_lists", "codehilite", "toc"],
        extension_configs={"toc": {"toc_depth": "1-2", "permalink": False}},
        output_format="html5",
    )
    html = converter.convert("[TOC]\n\n" + body_source)
    soup = BeautifulSoup(html, "html.parser")

    toc = soup.select_one("div.toc")
    if toc:
        toc_title = toc.find(class_="toctitle")
        if toc_title:
            toc_title.decompose()
        toc_heading = soup.new_tag("div")
        toc_heading["class"] = ["toc-heading"]
        toc_heading.string = "目录"
        toc.insert_before(toc_heading)

    # These two sections otherwise leave only a short tail from the preceding
    # section on its own page. Move that tail with the next full-width diagram.
    early_break_blocks = {
        "5": 1,
        "8": 3,
        "11-llm-agentdecision": 3,
        "15-action-hash-approval": 6,
        "17-action": 3,
        "21-ssh": 4,
    }
    inline_diagram_sections = {
        "3",
        "4-docker",
        "13-capability-registry",
        "14-action",
        "19-agentrunworker",
        "22-precheckexecuteverifierrollback",
        "25",
    }
    separate_after_early_break = {"15-action-hash-approval"}
    diagrams = 0
    for image in soup.find_all("img"):
        parent = image.parent
        if not isinstance(parent, Tag):
            continue
        parent["class"] = [*(parent.get("class") or []), "diagram-block"]
        heading = parent.find_previous_sibling()
        if isinstance(heading, Tag) and heading.name == "h2":
            break_lead = early_break_blocks.get(str(heading.get("id")), 0)
            wrapper = soup.new_tag("div")
            wrapper["class"] = [
                "diagram-intro",
                *(
                    ["diagram-page-break"]
                    if (
                        break_lead == 0
                        and str(heading.get("id")) not in inline_diagram_sections
                    )
                    or str(heading.get("id")) in separate_after_early_break
                    else []
                ),
            ]
            if break_lead:
                break_anchor: Tag = heading
                for _ in range(break_lead):
                    previous = break_anchor.find_previous_sibling()
                    if not isinstance(previous, Tag):
                        break
                    break_anchor = previous
                break_anchor["class"] = [
                    *(break_anchor.get("class") or []),
                    "diagram-page-break",
                ]
            heading.insert_before(wrapper)
            wrapper.append(heading.extract())
            wrapper.append(parent.extract())
        else:
            parent["class"] = [
                *(parent.get("class") or []),
                "diagram-page-break",
            ]
        diagrams += 1
    if diagrams != 16:
        raise RuntimeError(f"Expected 16 tutorial diagrams, found {diagrams}")

    final_summary = soup.find(
        "h2",
        string=lambda value: bool(
            value and value.startswith("36. 最后形成一个完整心智模型")
        ),
    )
    if isinstance(final_summary, Tag):
        final_summary["class"] = [
            *(final_summary.get("class") or []),
            "diagram-page-break",
        ]

    return str(soup)


def _render_body(html: str) -> fitz.Document:
    story = fitz.Story(
        html=html,
        user_css=CSS,
        em=10,
        archive=fitz.Archive(str(ROOT)),
    )

    def rectfn(_rect_number: int, _filled: fitz.Rect):
        return PAGE, CONTENT, fitz.Identity

    return story.write_with_links(rectfn)


def _draw_cover(document: fitz.Document, commit_sha: str) -> None:
    page = document.new_page(width=PAGE.width, height=PAGE.height)
    page.draw_rect(page.rect, color=None, fill=(0.94, 0.975, 1.0))
    page.draw_rect(fitz.Rect(0, 0, PAGE.width, 28), color=None, fill=(0.13, 0.43, 0.78))
    page.draw_rect(
        fitz.Rect(68, 129, 126, 187),
        color=None,
        fill=(0.07, 0.12, 0.20),
    )
    font_name = "WQY"
    if FONT_FILE.exists():
        page.insert_font(fontname=font_name, fontfile=str(FONT_FILE))
    else:
        font_name = "helv"
    page.insert_text((80, 167), ">_", fontsize=20, color=(1, 1, 1), fontname=font_name)
    page.insert_textbox(
        fitz.Rect(68, 231, PAGE.width - 68, 290),
        "Ops Agent Chat 源码深度教学",
        fontsize=24,
        color=(0.06, 0.12, 0.21),
        fontname=font_name,
    )
    page.insert_textbox(
        fitz.Rect(68, 328, PAGE.width - 68, 365),
        "从请求入队、LangGraph 决策到受治理的 SSH 执行",
        fontsize=12,
        color=(0.05, 0.35, 0.68),
        fontname=font_name,
    )
    page.draw_line(
        fitz.Point(68, 485),
        fitz.Point(PAGE.width - 68, 485),
        color=(0.38, 0.68, 0.94),
        width=0.8,
    )
    page.insert_textbox(
        fitz.Rect(68, 505, PAGE.width - 68, 545),
        f"代码基线：{commit_sha}\n导出日期：{date.today().isoformat()}",
        fontsize=7.5,
        lineheight=1.25,
        color=(0.28, 0.39, 0.52),
        fontname=font_name,
    )


def _add_page_numbers(document: fitz.Document) -> None:
    font_name = "WQY"
    for page in document:
        if page.number == 0:
            continue
        if FONT_FILE.exists():
            page.insert_font(fontname=font_name, fontfile=str(FONT_FILE))
        else:
            font_name = "helv"
        label = (
            f"Ops Agent Chat 源码深度教学    "
            f"第 {page.number + 1} 页 / 共 {document.page_count} 页"
        )
        page.insert_textbox(
            fitz.Rect(40, PAGE.height - 39, PAGE.width - 40, PAGE.height - 20),
            label,
            fontsize=7.2,
            color=(0.34, 0.43, 0.53),
            fontname=font_name,
            align=fitz.TEXT_ALIGN_CENTER,
        )


def _validate_layout(document: fitz.Document) -> None:
    placements: list[tuple[int, int, float]] = []
    empty_pages: list[int] = []

    for page_number, page in enumerate(document, start=1):
        images = page.get_images(full=True)
        if page_number > 2 and not page.get_text().strip() and not images:
            empty_pages.append(page_number)

        for image_info in images:
            extracted = document.extract_image(image_info[0])
            for rect in page.get_image_rects(image_info[0]):
                placements.append(
                    (
                        extracted["width"],
                        extracted["height"],
                        rect.width / page.rect.width,
                    )
                )

    if empty_pages:
        raise RuntimeError(f"PDF contains empty pages: {empty_pages}")
    if len(placements) != 16:
        raise RuntimeError(
            f"Expected 16 diagram placements, found {len(placements)}"
        )
    if any(
        (width, height) != (1672, 941)
        for width, height, _ratio in placements
    ):
        raise RuntimeError("A diagram was not embedded at its source resolution")
    if any(ratio < 0.89 for _width, _height, ratio in placements):
        raise RuntimeError("A diagram was rendered below 89% of the page width")


def export() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    match = re.search(r"`([0-9a-f]{40})`", source)
    commit_sha = match.group(1) if match else "unknown"
    body = _render_body(_build_html(source))

    document = fitz.open()
    _draw_cover(document, commit_sha)
    document.insert_pdf(body)
    body.close()
    _validate_layout(document)
    _add_page_numbers(document)
    document.set_metadata(
        {
            "title": "Ops Agent Chat 源码深度教学",
            "author": "Ops Agent Chat",
            "subject": f"Source tutorial for commit {commit_sha}",
            "keywords": "Ops Agent, LangGraph, Capability Registry, Approval, SSH",
        }
    )

    temporary = OUTPUT.with_name(f".{OUTPUT.name}.tmp")
    document.save(temporary, garbage=4, deflate=True)
    document.close()
    os.replace(temporary, OUTPUT)


if __name__ == "__main__":
    export()
