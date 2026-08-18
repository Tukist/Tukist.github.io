# -*- coding: utf-8 -*-
"""
把《肠子》PDF 提取为章节化 Markdown，供 reader.html 阅读界面使用。

用法：
    python extract_book.py

输出：
    book/ch00.md ... book/chNN.md   每章一个 markdown 文件（# 标题 + 段落）
    book/index.json                 章节列表 [{id, title}, ...]

说明：
    - 用 pymupdf (import fitz) 逐页提取文本。
    - 该 PDF 把大量单字偏旁类汉字编码到了「康熙部首区」U+2F00~U+2FD5，
      用 NFKC 归一化即可还原为标准汉字。
    - 唯一已知 bug：字体把「口」错编码成 U+2F1C（其实是「又」的部首），
      需要单独纠正为「口」。
    - 段落按行缩进重建：续行 x0≈77，段首缩进 x0≈107，诗句居中 x0≈107/122，
      标题居中 x0≥180。
"""
import io
import json
import os
import re
import unicodedata

import fitz  # pymupdf

PDF = "C:/Users/HP/Desktop/肠子.pdf"
OUTDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "book")

# 行坐标阈值（PDF 页面宽 612pt，单栏正文）
BODY_X_MAX = 85.0    # x0 <= 85 视为段落续行
HEADING_X_MIN = 180.0  # x0 >= 180 视为居中标题/副标题


def fix_char(ch):
    """修正单个字符的编码。"""
    if ch == "\u2f1c":  # ⼜ —— 本书字体把「口」错编码成这个码点
        return "\u53e3"  # 口
    o = ord(ch)
    # 康熙部首区 / CJK 部首补充区 / CJK 兼容表意字区 → NFKC 还原为标准汉字
    if 0x2F00 <= o <= 0x2FD5 or 0x2E80 <= o <= 0x2EFF or 0xF900 <= o <= 0xFAFF:
        return unicodedata.normalize("NFKC", ch)
    return ch


def fix_text(s):
    """修正整段文本的编码。"""
    return "".join(fix_char(c) for c in s)


def canon(s):
    """去掉所有空白，用于标题匹配（避免排版空格差异导致漏匹配）。"""
    return re.sub(r"\s+", "", s).strip()


def get_lines(page):
    """按阅读顺序取出一页的所有文本行，返回 [(x0, y0, text), ...]。"""
    d = page.get_text("dict")
    lines = []
    for blk in d.get("blocks", []):
        if blk.get("type") != 0:
            continue
        for line in blk.get("lines", []):
            text = "".join(span.get("text", "") for span in line.get("spans", []))
            text = text.strip()
            if not text:
                continue
            bbox = line["bbox"]
            lines.append((bbox[0], bbox[1], text))
    # 单栏书按 y 排序即可，x 作次要键防止同行分块
    lines.sort(key=lambda t: (t[1], t[0]))
    return lines


def build_chapters(doc):
    """根据 PDF 目录（level-1 条目）切分章节，返回列表。"""
    toc = doc.get_toc()
    chapters = []
    cur = None
    for entry in toc:
        level, title, page = entry
        title = fix_text(title).strip()
        if level == 1:
            if title == "版权信息":
                cur = None  # 跳过版权页，不生成章节
                continue
            cur = {"title": title, "page": page, "subs": []}
            chapters.append(cur)
        elif level == 2 and cur is not None:
            cur["subs"].append(title)
    return chapters


def chapter_to_markdown(doc, chapter, next_page):
    """把某一章（页范围 [chapter.page-1, next_page-1)）转成 markdown 文本。"""
    start_idx = chapter["page"] - 1
    end_idx = next_page - 1 if next_page else doc.page_count

    title = chapter["title"]
    # 用于跳过正文里重复出现的章标题（避免和 H1 重复）
    title_key = canon(title)

    # 先收集整章所有行
    raw_lines = []
    for pi in range(start_idx, end_idx):
        raw_lines.extend(get_lines(doc[pi]))

    out = ["# " + title, ""]
    para = []  # 当前段落缓存

    def flush():
        if para:
            out.append("".join(para))
            out.append("")
            para.clear()

    i = 0
    n = len(raw_lines)
    while i < n:
        x0, _y0, raw = raw_lines[i]
        text = fix_text(raw).strip()
        if not text:
            i += 1
            continue

        text_key = canon(text)

        # 1) 章标题行：跳过（H1 已经写了）
        if text_key == title_key:
            i += 1
            continue

        # 2) 居中标题（副标题，如「地标」「一首关于圣无肠的诗」）
        if x0 >= HEADING_X_MIN:
            flush()
            parts = [text]
            i += 1
            # 合并连续居中的几行，组成一个标题
            while i < n:
                nx0, _ny0, nraw = raw_lines[i]
                ntext = fix_text(nraw).strip()
                if nx0 >= HEADING_X_MIN and ntext:
                    parts.append(ntext)
                    i += 1
                else:
                    break
            merged = " ".join(parts)
            # 若合并后正好是章标题（正文标题被拆成多行），跳过，避免和 H1 重复
            if canon(merged) == title_key:
                continue
            out.append("## " + merged)
            out.append("")
            continue

        # 3) 正文：x0<=BODY_X_MAX 是续行，否则是新段落（段首缩进或诗句）
        if x0 <= BODY_X_MAX:
            para.append(text)
        else:
            flush()
            para.append(text)
        i += 1

    flush()
    return "\n".join(out).rstrip() + "\n"


def main():
    doc = fitz.open(PDF)
    chapters = build_chapters(doc)

    os.makedirs(OUTDIR, exist_ok=True)
    index = []
    print(f"共 {len(chapters)} 章\n")

    for idx, chapter in enumerate(chapters):
        cid = "ch%02d" % idx
        next_page = chapters[idx + 1]["page"] if idx + 1 < len(chapters) else None
        md = chapter_to_markdown(doc, chapter, next_page)
        path = os.path.join(OUTDIR, cid + ".md")
        with io.open(path, "w", encoding="utf-8") as f:
            f.write(md)
        index.append({"id": cid, "title": chapter["title"]})

        # 统计字数（按字符数，去掉空白）
        char_count = len(re.sub(r"\s", "", md))
        print(f"  {cid}  {chapter['title']:<24} {char_count} 字")

    index_path = os.path.join(OUTDIR, "index.json")
    with io.open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

    doc.close()
    print(f"\n已生成 {len(index)} 个章节文件到 {OUTDIR}")


if __name__ == "__main__":
    main()
