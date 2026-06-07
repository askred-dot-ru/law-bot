#!/usr/bin/env python3
"""Fetch documents from consultant.ru and append to output.md.

Uses the same approach as fetch_constitution.py: parse TOC, fetch each article page.
Works for any document on consultant.ru that has a nested TOC structure.
"""

import re
import urllib.request
import urllib.error
import time
import sys
import os

DELAY = 0.5  # seconds between requests

def fetch_html(url, timeout=30):
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode('utf-8', errors='replace')
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
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

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

def extract_li_info(li_html):
    m = re.search(r'<a\s+href="([^"]*)">(.*?)</a>', li_html, re.DOTALL)
    if not m:
        return None, None
    url = m.group(1).strip()
    title = m.group(2).strip()
    if url.startswith('#'):
        url = None  # In-page hash - not a separate page
    elif not url.startswith('http'):
        url = 'https://www.consultant.ru' + url
    return title, url

def parse_toc_items(html):
    """Parse nested UL/LI structure from consultant.ru TOC."""
    m = re.search(r'<div class="document-page__toc[^>]*>(.*?)</div>', html, re.DOTALL)
    if not m:
        return []
    toc_html = m.group(1)
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
            if title:
                items.append((depth, title, url))
    return items

def fetch_page_content(url):
    """Fetch an article/section page and return text paragraphs."""
    html = fetch_html(url)
    if not html:
        return []
    content = extract_content_div(html)
    if not content:
        return []
    content = re.sub(r'<h1[^>]*>.*?</h1>', '', content, flags=re.DOTALL)
    paras = re.findall(r'<p[^>]*>(.*?)</p>', content, re.DOTALL)
    lines = []
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

def fetch_preamble(html):
    """Extract preamble/header text from main page."""
    m = re.search(r'<div class="document-page__content[^>]*>(.*?)</div>\s*<div class="document-page__toc"', html, re.DOTALL)
    if not m:
        m = re.search(r'<div class="document-page__content[^>]*>(.*?)</div>', html, re.DOTALL)
    if not m:
        return []
    content = m.group(1)
    paras = re.findall(r'<p[^>]*>(.*?)</p>', content, re.DOTALL)
    lines = []
    for p in paras:
        inner = p.strip()
        if not inner:
            continue
        if '<div class="document__insert' in inner or '<hr' in inner:
            continue
        text = strip_tags(inner)
        if not text or text in ('(см. Обзор изменений данного документа)', 'Обзор изменений данного документа'):
            continue
        lines.append(text)
    return lines

def is_skip_title(text):
    """Skip duplicate heading-like titles in content."""
    stripped = text.strip()
    if re.match(r'^Статья\s+\d+(\.\d+)?\.?\s*$', stripped):
        return True
    if re.match(r'^Глава\s+\d+', stripped):
        return True
    if re.match(r'^Раздел\s+\w+', stripped):
        return True
    if re.match(r'^§\s*\d+', stripped):
        return True
    return False

def fetch_document(doc_title, doc_id, force_two_level=False):
    """Fetch a document from consultant.ru.
    
    Args:
        doc_title: Title for the document (use as # heading)
        doc_id: LAW number (XXXXX from cons_doc_LAW_XXXXX)
        force_two_level: If True, only use two levels of TOC depth
    
    Returns:
        List of markdown lines
    """
    base_url = f"https://www.consultant.ru/document/cons_doc_LAW_{doc_id}"
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"Fetching: {doc_title} (LAW_{doc_id})", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)
    
    print("  Fetching main page...", file=sys.stderr)
    html = fetch_html(base_url)
    if not html:
        print(f"  ERROR: Could not fetch main page for LAW_{doc_id}", file=sys.stderr)
        return []
    
    # Check if document exists on consultant.ru
    title_tag = re.search(r'<title>(.*?)</title>', html, re.DOTALL)
    if title_tag and ('не найден' in title_tag.group(1) or '404' in title_tag.group(1)):
        print(f"  ERROR: Document LAW_{doc_id} not found", file=sys.stderr)
        return []
    
    print("  Parsing preamble...", file=sys.stderr)
    preamble = fetch_preamble(html)
    print(f"  Preamble: {len(preamble)} paragraphs", file=sys.stderr)
    
    print("  Parsing TOC...", file=sys.stderr)
    toc_items = parse_toc_items(html)
    
    if not toc_items:
        print("  No TOC found (flat document?)", file=sys.stderr)
        # Try to get content directly from main page
        content = extract_content_div(html)
        if content:
            paras = re.findall(r'<p[^>]*>(.*?)</p>', content, re.DOTALL)
            lines = []
            lines.append(f"# {doc_title}")
            lines.append("")
            for p_html in paras:
                text = strip_tags(p_html)
                if text:
                    lines.append(text)
                    lines.append("")
            return lines
        return []
    
    depth_heading = {1: '###', 2: '######', 3: '######'}
    if force_two_level:
        depth_heading = {1: '###', 2: '######', 3: '######'}
    
    lines = []
    lines.append(f"# {doc_title}")
    lines.append("")
    
    for p in preamble:
        lines.append(p)
        lines.append("")
    
    item_count = 0
    for depth, title, url in toc_items:
        heading = depth_heading.get(depth, '######')
        
        if url:
            # This item has a separate page - fetch content
            item_count += 1
            if item_count % 5 == 0:
                print(f"  Fetched {item_count} items...", file=sys.stderr)
            
            lines.append(f"{heading} {title}")
            lines.append("")
            
            content_lines = fetch_page_content(url)
            for t in content_lines:
                if not is_skip_title(t):
                    lines.append(t)
                    lines.append("")
            
            time.sleep(DELAY)
        else:
            # In-page hash or no URL - just add as heading
            lines.append(f"{heading} {title}")
            lines.append("")
    
    print(f"  Total items: {item_count}", file=sys.stderr)
    return lines


# Document list: (title, law_id, {options})
# law_id from cons_doc_LAW_XXXXX
DOCUMENTS = [
    # ===== FEDERAL LAWS =====
    ("Федеральный закон «Об электроэнергетике» от 26.03.2003 № 35-ФЗ", 41502),
    ("Федеральный закон «Об энергосбережении и о повышении энергетической эффективности» от 23.11.2009 № 261-ФЗ", 93978),
    ("Федеральный закон «О теплоснабжении» от 27.07.2010 № 190-ФЗ", 102975),
    ("Федеральный закон «О водоснабжении и водоотведении» от 07.12.2011 № 416-ФЗ", 122867),
    ("Федеральный закон «О естественных монополиях» от 17.08.1995 № 147-ФЗ", 7578),
    
    # ===== GOVERNMENT RESOLUTIONS =====
    # These need their LAW IDs found
    # ("Постановление Правительства РФ от 27.12.2004 № 861 «О Правилах недискриминационного доступа к услугам по передаче электрической энергии...»", 0),
    # ("Постановление Правительства РФ от 06.05.2011 № 354 «О предоставлении коммунальных услуг собственникам...»", 0),
    # ("Постановление Правительства РФ от 04.05.2012 № 442 «О функционировании розничных рынков электрической энергии...»", 0),
]

def main():
    output_path = "/Users/red/Documents/ai/s/law-bot/output.md"
    all_lines = []
    
    for i, doc_info in enumerate(DOCUMENTS):
        title, doc_id = doc_info[0], doc_info[1]
        options = doc_info[2] if len(doc_info) > 2 else {}
        
        if doc_id == 0:
            print(f"\nSKIPPING: {title} (ID not found yet)", file=sys.stderr)
            continue
        
        doc_lines = fetch_document(title, doc_id, **options)
        
        if doc_lines:
            if i > 0:
                all_lines.append("")
                all_lines.append("---")
                all_lines.append("")
            all_lines.extend(doc_lines)
    
    if not all_lines:
        print("No documents to write!", file=sys.stderr)
        return
    
    # Append to output.md
    existing = ""
    if os.path.exists(output_path):
        with open(output_path, "r", encoding="utf-8") as f:
            existing = f.read()
    
    new_content = existing + "\n\n---\n\n" + "\n".join(all_lines)
    
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(new_content)
    
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"Appended to {output_path}", file=sys.stderr)
    print(f"New document lines: {len(all_lines)}", file=sys.stderr)
    total_lines = len(new_content.split('\n'))
    print(f"Total lines in file: {total_lines}", file=sys.stderr)
    file_size = os.path.getsize(output_path)
    print(f"File size: {file_size} bytes ({file_size/1024/1024:.1f} MB)", file=sys.stderr)

if __name__ == "__main__":
    main()
