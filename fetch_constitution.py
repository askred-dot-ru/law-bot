#!/usr/bin/env python3
"""Fetch Constitution of the Russian Federation from consultant.ru and insert at beginning of output.md."""

import re
import urllib.request
import urllib.error
import time
import sys
import os

BASE_URL = "https://www.consultant.ru/document/cons_doc_LAW_28399"

def fetch_html(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode('utf-8')
    except Exception as e:
        print(f"  ERROR fetching {url}: {e}", file=sys.stderr)
        return None

def strip_tags(html):
    text = html.replace('&nbsp;', ' ').replace('&mdash;', '—')
    text = text.replace('&laquo;', '«').replace('&raquo;', '»')
    text = text.replace('&ndash;', '–').replace('&quot;', '"')
    text = text.replace('&lt;', '<').replace('&gt;', '>')
    text = text.replace('&#160;', ' ').replace('&#8212;', '—')
    text = text.replace('&#8220;', '«').replace('&#8221;', '»')
    text = re.sub(r'<br\s*/?>', '\n', text)
    text = re.sub(r'<a\s+[^>]*>', '', text)
    text = re.sub(r'</a>', '', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def parse_preamble(html):
    """Extract preamble text from main page content div."""
    m = re.search(r'<div class="document-page__content[^>]*>(.*?)</div>\s*<div class="document-page__toc"', html, re.DOTALL)
    if not m:
        m = re.search(r'<div class="document-page__content[^>]*>(.*?)</div>\s*(?:<div class="full-text"|<\w+\s+class="document-page__toc")', html, re.DOTALL)
    if not m:
        return []
    content = m.group(1)
    paras = re.findall(r'<p[^>]*>(.*?)</p>', content, re.DOTALL)
    lines = []
    for p in paras:
        inner = p.strip()
        if not inner:
            continue
        # Skip annotations and empty divs
        if '<div class="document__insert' in inner or '<hr' in inner:
            continue
        text = strip_tags(inner)
        if not text or text in ('(см. Обзор изменений данного документа)', 'Обзор изменений данного документа'):
            continue
        lines.append(text)
    return lines

def parse_toc(html):
    """Parse TOC structure: list of (level, title, url) tuples."""
    m = re.search(r'<div class="document-page__toc[^>]*>(.*?)</div>\s*<div class="full-text"', html, re.DOTALL)
    if not m:
        m = re.search(r'<div class="document-page__toc[^>]*>(.*?)</div>', html, re.DOTALL)
    if not m:
        print("  ERROR: Could not find TOC", file=sys.stderr)
        return [], []
    
    toc_html = m.group(1)
    
    sections = []
    articles = []
    
    # Parse the nested ul/li structure
    # Раздел I → li with next ul containing chapters
    # Each chapter → li with next ul containing articles
    # Раздел II → li without sub-ul
    
    # Find all top-level li items (sections)
    section_items = re.findall(r'<li>(.*?)</li>\s*(?:<ul>)?', toc_html, re.DOTALL)
    
    # Better approach: walk through the nested structure
    # Match pattern: <li><a href="...">...</a></li> optionally followed by <ul>...<ul>
    
    # Find all li items at the top level of the TOC
    # The TOC has structure like:
    # <ul>
    #   <li><a href="...">Раздел I</a></li>
    #   <ul>
    #     <li><a href="...">Глава 1...</a></li>
    #     <ul>...
    #   </ul>
    #   <li><a href="...">Раздел II...</a></li>
    # </ul>
    
    # Extract top-level sections
    top_lis = re.findall(r'<li>(.*?)</li>(.*?)(?=<li>|\s*</ul>)', toc_html, re.DOTALL)
    
    # Actually, let me use a simpler approach with regex
    # First, extract all section-level items
    section_pattern = r'<li><a href="([^"]*)">([^<]*)</a></li>\s*(<ul>.*?</ul>\s*)?'
    found = re.findall(section_pattern, toc_html, re.DOTALL)
    
    for url, title, inner_ul in found:
        url = url.strip()
        if not url.startswith('http'):
            url = 'https://www.consultant.ru' + url
        title = title.strip()
        
        # Check if section has chapters
        if inner_ul:
            # This is Раздел I (has chapters)
            sections.append(('section', title, url))
            # Parse chapters within
            chapter_pattern = r'<li><a href="([^"]*)">([^<]*)</a></li>\s*(<ul>.*?</ul>\s*)?'
            chapters = re.findall(chapter_pattern, inner_ul, re.DOTALL)
            for ch_url, ch_title, ch_inner in chapters:
                ch_url = ch_url.strip()
                if not ch_url.startswith('http'):
                    ch_url = 'https://www.consultant.ru' + ch_url
                ch_title = ch_title.strip()
                sections.append(('chapter', ch_title, ch_url))
                # Parse articles within chapter
                if ch_inner:
                    art_pattern = r'<li><a href="([^"]*)">([^<]*)</a></li>'
                    arts = re.findall(art_pattern, ch_inner, re.DOTALL)
                    for a_url, a_title in arts:
                        a_url = a_url.strip()
                        if not a_url.startswith('http'):
                            a_url = 'https://www.consultant.ru' + a_url
                        a_title = a_title.strip()
                        articles.append((a_title, a_url))
        else:
            # This is Раздел II (no chapters, direct text)
            sections.append(('section', title, url))
    
    return sections, articles

def extract_li_info(li_html):
    m = re.search(r'<a\s+href="([^"]*)">(.*?)</a>', li_html, re.DOTALL)
    if not m:
        return None, None
    url = m.group(1).strip()
    title = m.group(2).strip()
    if not url.startswith('http'):
        url = 'https://www.consultant.ru' + url
    return title, url

def parse_toc_items(toc_html):
    """Parse nested UL/LI structure, returning items with their nesting depth."""
    html_flat = re.sub(r'>\s+<', '><', toc_html)
    pattern = r'(<ul>|</ul>|<li>.*?</li>)'
    parts = re.findall(pattern, html_flat, re.DOTALL)
    
    items = []
    depth = 0
    for part in parts:
        if part == '<ul>':
            depth += 1
        elif part == '</ul>':
            depth -= 1
        else:
            title, url = extract_li_info(part)
            if title and url:
                items.append((depth, title, url))
    return items

def extract_content_div(html):
    m = re.search(r'<div class="document-page__content[^>]*>', html)
    if not m:
        return None
    start = m.end()
    depth = 1
    i = start
    while i < len(html) and depth > 0:
        next_open = html.find('<div', i)
        next_close = html.find('</div>', i)
        if next_close == -1:
            break
        if next_open != -1 and next_open < next_close:
            depth += 1
            i = next_open + 4
        else:
            depth -= 1
            i = next_close + 6
    return html[start:i - 6] if i >= 6 else html[start:i]

def is_article_title(text):
    stripped = text.strip()
    stripped = re.sub(r'\s*<$', '', stripped)
    return bool(re.match(r'^Статья\s+\d+(\.\d+)?\.?\s*(<|\*>)?\s*$', stripped))

def fetch_article_text(url):
    html = fetch_html(url)
    if not html:
        return []
    
    lines = []
    content = extract_content_div(html)
    if not content:
        return []
    
    content = re.sub(r'<h1[^>]*>.*?</h1>', '', content, flags=re.DOTALL)
    paras = re.findall(r'<p[^>]*>(.*?)</p>', content, re.DOTALL)
    
    for p_html in paras:
        inner = p_html.strip()
        if not inner:
            continue
        if '<div' in inner and '</div>' in inner:
            inner_paras = re.findall(r'<p[^>]*>(.*?)</p>', inner, re.DOTALL)
            for ip in inner_paras:
                t = strip_tags(ip)
                if t and not is_article_title(t):
                    lines.append(t)
            continue
        text = strip_tags(inner)
        if text and not is_article_title(text):
            lines.append(text)
    
    return lines

def fetch_section_text(url):
    html = fetch_html(url)
    if not html:
        return []
    
    lines = []
    content = extract_content_div(html)
    if not content:
        return []
    
    after_h1 = re.split(r'</h1>', content, maxsplit=1)
    if len(after_h1) > 1:
        content = after_h1[1]
    
    paras = re.findall(r'<p[^>]*>(.*?)</p>', content, re.DOTALL)
    
    for p_html in paras:
        inner = p_html.strip()
        if not inner:
            continue
        if '<hr' in inner:
            continue
        if '<div' in inner:
            inner_paras = re.findall(r'<p[^>]*>(.*?)</p>', inner, re.DOTALL)
            for ip in inner_paras:
                t = strip_tags(ip)
                if t:
                    lines.append(t)
            continue
        text = strip_tags(inner)
        if text:
            lines.append(text)
    
    return lines

def generate_constitution_markdown():
    print("Fetching main page...", file=sys.stderr)
    html = fetch_html(BASE_URL)
    if not html:
        print("ERROR: Could not fetch main page", file=sys.stderr)
        return []
    
    print("Parsing preamble...", file=sys.stderr)
    preamble = parse_preamble(html)
    print(f"  Preamble: {len(preamble)} paragraphs", file=sys.stderr)
    
    print("Parsing TOC...", file=sys.stderr)
    sections, articles = parse_toc(html)
    print(f"  Sections: {len([s for s in sections if s[0]=='section'])}", file=sys.stderr)
    print(f"  Chapters: {len([s for s in sections if s[0]=='chapter'])}", file=sys.stderr)
    print(f"  Articles: {len(articles)}", file=sys.stderr)
    
    # Build markdown
    lines = []
    lines.append("# Конституция Российской Федерации")
    lines.append("")
    
    # Preamble
    for p in preamble:
        lines.append(p)
        lines.append("")
    
    # Keep track of which section we're in
    current_section = None
    article_index = 0
    
    for item_type, title, url in sections:
        if item_type == 'section':
            # Раздел heading
            # Normalize title: "Раздел I" → "Раздел I" (keep as-is from TOC)
            lines.append(f"## {title}")
            lines.append("")
            current_section = title
            
            # Check if this section has direct text (Раздел II)
            # Раздел II has no articles, its text is on the section page itself
            if 'Раздел II' in title or 'Заключительные' in title:
                print(f"  Fetching section text: {title}", file=sys.stderr)
                section_text = fetch_section_text(url)
                for t in section_text:
                    lines.append(t)
                    lines.append("")
            
        elif item_type == 'chapter':
            # Глава heading
            lines.append(f"##### {title}")
            lines.append("")
            
            # Add articles belonging to this chapter
            # We need to determine how many articles are in this chapter
            # From the structure, articles are listed in order after each chapter
            
    # Now add all articles
    # The articles list is in order matching the TOC structure
    # We need to interleave them with the chapters
    
    # Let me redo this - I'll rebuild the structure more carefully
    # Actually, let me take a different approach
    
    return lines

def build_markdown():
    print("Fetching main page...", file=sys.stderr)
    html = fetch_html(BASE_URL)
    if not html:
        print("ERROR: Could not fetch main page", file=sys.stderr)
        return []
    
    print("Parsing preamble...", file=sys.stderr)
    preamble = parse_preamble(html)
    print(f"  Preamble: {len(preamble)} paragraphs", file=sys.stderr)
    
    print("Parsing TOC...", file=sys.stderr)
    m = re.search(r'<div class="document-page__toc[^>]*>(.*?)</div>\s*<div class="full-text"', html, re.DOTALL)
    if not m:
        m = re.search(r'<div class="document-page__toc[^>]*>(.*?)</div>', html, re.DOTALL)
    if not m:
        print("ERROR: Could not find TOC", file=sys.stderr)
        return []
    
    toc_html = m.group(1)
    toc_items = parse_toc_items(toc_html)
    
    # Depth mapping:
    # depth 1 → section (##)
    # depth 2 → chapter (#####)
    # depth 3 → article (######)
    depth_heading = {1: '##', 2: '#####', 3: '######'}
    
    lines = []
    lines.append("# Конституция Российской Федерации")
    lines.append("")
    
    for p in preamble:
        lines.append(p)
        lines.append("")
    
    article_count = 0
    for depth, title, url in toc_items:
        heading = depth_heading.get(depth, '######')
        lines.append(f"{heading} {title}")
        lines.append("")
        
        if depth == 3:
            # Article - fetch text
            article_count += 1
            if article_count % 10 == 0:
                print(f"  Fetched {article_count} articles...", file=sys.stderr)
            art_text = fetch_article_text(url)
            for t in art_text:
                lines.append(t)
                lines.append("")
            time.sleep(0.3)
        elif depth == 1 and 'Раздел II' in title:
            # Раздел II has text directly on its page
            print(f"  Fetching Раздел II text...", file=sys.stderr)
            sec_text = fetch_section_text(url)
            for t in sec_text:
                lines.append(t)
                lines.append("")
    
    print(f"  Total articles: {article_count}", file=sys.stderr)
    return lines

def main():
    print("="*60, file=sys.stderr)
    print("Fetching Constitution of the Russian Federation", file=sys.stderr)
    print("="*60, file=sys.stderr)
    
    lines = build_markdown()
    
    if not lines:
        print("ERROR: No content generated", file=sys.stderr)
        return
    
    # Insert at beginning of output.md
    output_path = "/Users/red/Documents/ai/s/law-bot/output.md"
    
    constitution_text = "\n".join(lines)
    
    if os.path.exists(output_path):
        with open(output_path, "r", encoding="utf-8") as f:
            existing = f.read()
        new_content = constitution_text + "\n\n---\n\n" + existing
    else:
        new_content = constitution_text
    
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(new_content)
    
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"Added Constitution to {output_path}", file=sys.stderr)
    print(f"Constitution lines: {len(lines)}", file=sys.stderr)
    new_lines_count = len(new_content.split('\n'))
    print(f"Total lines in file: {new_lines_count}", file=sys.stderr)
    file_size = os.path.getsize(output_path)
    print(f"File size: {file_size} bytes ({file_size/1024/1024:.1f} MB)", file=sys.stderr)

if __name__ == "__main__":
    main()
