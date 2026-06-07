#!/usr/bin/env python3
"""Fetch all RF codes from actual.pravo.gov.ru and convert to markdown."""

import json
import re
import urllib.request
import urllib.error
import time
import sys
import os

API_BASE = "http://actual.pravo.gov.ru:8000/api/ebpi"

def fetch_json(url):
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"  ERROR fetching {url}: {e}", file=sys.stderr)
        return None

def get_actual_redaction(doc_hash):
    url = f"{API_BASE}/redactions/?bpa=ebpi&t={{\"hash\":\"{doc_hash}\"}}"
    data = fetch_json(url)
    if not data or "redactions" not in data:
        return None
    # Try actual redaction with content
    for r in data["redactions"]:
        if r.get("actual") and r.get("hascontent") and r.get("contentcomplete"):
            return r["redid"]
    # Fallback: actual (even without content - use this to get the latest)
    # but check it has content
    for r in data["redactions"]:
        if r.get("actual"):
            if r.get("hascontent"):
                return r["redid"]
    # Fallback: latest with content
    for r in data["redactions"]:
        if r.get("hascontent") and r.get("contentcomplete"):
            return r["redid"]
    # Ultimate fallback
    if data["redactions"]:
        return data["redactions"][0]["redid"]
    return None

def fetch_redtext(redid):
    url = f"{API_BASE}/redtext?bpa=ebpi&t={redid}&ttl=0"
    data = fetch_json(url)
    if data and "redtext" in data:
        return data["redtext"]
    return None

def extract_paras(html):
    match = re.search(r'<body>(.*?)</body>', html, re.DOTALL)
    if not match:
        return []
    body = match.group(1)
    paras = re.findall(r'<p[^>]*>(.*?)</p>', body, re.DOTALL)
    result = []
    for p_html in paras:
        cls_match = re.search(r'class=\"([^\"]+)\"', p_html)
        cls = cls_match.group(1) if cls_match else ""
        # Convert superscript (W9) and subscript (W8) spans to dot notation
        # e.g. "Статья 406<span class="W9">1</span>" → "Статья 406.1"
        p_html = re.sub(r'<span class="W9">(\d+)</span>', r'.\1', p_html)
        p_html = re.sub(r'<span class="W8">(\d+)</span>', r'.\1', p_html)
        text = re.sub(r'<[^>]+>', '', p_html)
        text = text.replace('&nbsp;', ' ').replace('&mdash;', '—')
        text = text.replace('&laquo;', '«').replace('&raquo;', '»')
        text = text.replace('&ndash;', '–').replace('&quot;', '"')
        text = text.replace('&lt;', '<').replace('&gt;', '>')
        text = text.replace('&#160;', ' ').replace('&#8212;', '—')
        text = text.replace('&#8220;', '«').replace('&#8221;', '»')
        text = text.strip()
        if text:
            result.append((cls, text))
    return result

def classify_heading(text):
    if re.match(r'^Ч[аа]сть\s+(первая|вторая|третья|четвертая|пятая|шестая|седьмая|восьмая|девятая|десятая)\b', text, re.IGNORECASE):
        return 'part'
    if re.match(r'^Часть\s+[IVXLCDM]+\b', text):
        return 'part'
    if re.match(r'^ЧАСТЬ\s+(ПЕРВАЯ|ВТОРАЯ|ТРЕТЬЯ|ЧЕТВЕРТАЯ|ПЯТАЯ|ШЕСТАЯ|СЕДЬМАЯ|ВОСЬМАЯ|ДЕВЯТАЯ|ДЕСЯТАЯ)\b', text):
        return 'part'
    if re.match(r'^Раздел\b', text):
        return 'section'
    if re.match(r'^Подраздел\b', text):
        return 'subsection'
    if re.match(r'^Глава\b', text):
        return 'chapter'
    if re.match(r'^Глава\s+[IVXLCDM]+\b', text):
        return 'chapter'
    if re.match(r'^Статья\s+\d+', text):
        return 'article'
    if re.match(r'^§\s*\d+', text):
        return 'paragraph'
    return 'text'

def is_annotation(cls, text):
    if 'mark' in cls:
        return True
    if (text.startswith('(Статья в редакции') or text.startswith('(В редакции') or 
        text.startswith('(Утратила силу') or text.startswith('(Утратил силу') or
        text.startswith('(Особенности применения') or text.startswith('(Федеральный закон') or
        text.startswith('(в ред.') or text.startswith('(Изменение') or
        text.startswith('(С учетом') or text.startswith('(Дополнение') or
        text.startswith('(Пункт') or text.startswith('(Часть') or
        text.startswith('(Абзац')):
        return True
    return False

def is_prelude(text):
    """Check if text is pre-document boilerplate."""
    t = text.strip()
    if t in ('РОССИЙСКАЯ ФЕДЕРАЦИЯ',):
        return True
    if t == 'Гражданский кодекс Российской Федерации':
        return True
    if t.startswith('Принят Государственной Думой'):
        return True
    if t.startswith('Одобрен Советом Федерации'):
        return True
    return False

def paras_to_markdown(paras, codex_name, part_label, skip_part_in_doc=True):
    lines = []
    
    lines.append(f"# {codex_name}")
    lines.append("")
    if part_label:
        lines.append(f"## {part_label}")
        lines.append("")
    
    in_article = False
    article_text_lines = []
    
    for cls, text in paras:
        # Skip prelude
        if is_prelude(text):
            continue
        
        heading_type = classify_heading(text)
        
        # Skip part headings from document if we're providing them
        if heading_type == 'part' and skip_part_in_doc and part_label:
            continue
        
        if heading_type in ('part', 'section', 'subsection', 'chapter', 'article', 'paragraph'):
            if article_text_lines:
                for line in article_text_lines:
                    lines.append(line)
                lines.append("")
                article_text_lines = []
            
            levels = {'part': '##', 'section': '###', 'subsection': '####', 'chapter': '#####', 'article': '######', 'paragraph': '######'}
            lines.append(f"{levels[heading_type]} {text}")
            lines.append("")
            
            in_article = (heading_type == 'article')
        elif is_annotation(cls, text):
            continue
        elif in_article:
            article_text_lines.append(text)
        else:
            lines.append(text)
            lines.append("")
    
    if article_text_lines:
        for line in article_text_lines:
            lines.append(line)
        lines.append("")
    
    return lines

def process_document(doc_hash, codex_name, part_label=""):
    print(f"  Fetching redactions...", file=sys.stderr)
    redid = get_actual_redaction(doc_hash)
    if not redid:
        print(f"  ERROR: No redaction found!", file=sys.stderr)
        return []
    
    print(f"  Fetching document text (redid={redid})...", file=sys.stderr)
    html = fetch_redtext(redid)
    if not html:
        print(f"  ERROR: No document text!", file=sys.stderr)
        return []
    
    print(f"  Parsing paragraphs...", file=sys.stderr)
    paras = extract_paras(html)
    print(f"  Found {len(paras)} paragraphs", file=sys.stderr)
    
    print(f"  Converting to markdown...", file=sys.stderr)
    lines = paras_to_markdown(paras, codex_name, part_label)
    print(f"  Generated {len(lines)} lines", file=sys.stderr)
    
    return lines

def main():
    codes = [
        # ГК РФ - Гражданский кодекс (4 части)
        ("ba747b7c430fdfb9405741d818463a26af1577a680f7a9ab6318cc6f4faa1121", "Гражданский кодекс Российской Федерации (ГК РФ)", "Часть первая"),
        ("c5515b06c932bdfbd67b34f5b856bb8317deaabded3eacae2ebadfd4c6703785", "Гражданский кодекс Российской Федерации (ГК РФ)", "Часть вторая"),
        ("4420d99c9eb3a210be9c946b1668b59acfb944f1129f0beaf1bd82d03449122c", "Гражданский кодекс Российской Федерации (ГК РФ)", "Часть третья"),
        ("3edc1b4f0e70ca1c9191422c7c01743f4316210d72f59b4a45581dd70060fb13", "Гражданский кодекс Российской Федерации (ГК РФ)", "Часть четвертая"),
        # НК РФ - Налоговый кодекс (2 части)
        ("b113c2e08341853ef53a8dad4585b513d96f85e0f3d0d246a25ecf52e40608db", "Налоговый кодекс Российской Федерации (НК РФ)", "Часть первая"),
        ("6d30a92f830312a1b62d0a002c635c3e667c52e1059a82525dd65cb8e2bbe0a6", "Налоговый кодекс Российской Федерации (НК РФ)", "Часть вторая"),
        # Single-part codes
        ("03bdee8f44a71247d7ba3342484326dbc3fa292d3caae9f88ada4b1ee38c20d9", "Трудовой кодекс Российской Федерации (ТК РФ)", ""),
        ("6639c6c6580e8aa0bdf84170d25823669dfb6a4144b03da245ef4889f24765c0", "Кодекс Российской Федерации об административных правонарушениях (КоАП РФ)", ""),
        ("301b8e7807e422d78de841e939939cb07d46851f945e2039d13fa8b38623557f", "Кодекс административного судопроизводства Российской Федерации (КАС РФ)", ""),
        ("fc01bd4ee09272d641c80b86f8be9f750ca1137e1402dd267b5b991e64ad45b1", "Градостроительный кодекс Российской Федерации", ""),
        ("34099dcc0eb7647de8e2af5a8ff35414410745ba6b4f742a81842946c1a6670e", "Гражданский процессуальный кодекс Российской Федерации (ГПК РФ)", ""),
        ("2464a0020ca4de3955dedd4369ba3d1bbbc846a28422a1b7c06ff6098086e8c3", "Арбитражный процессуальный кодекс Российской Федерации (АПК РФ)", ""),
        ("f29a592d348b400651a6760273edea9dfcbb75f4d56777c1c5b99233244b404c", "Уголовный кодекс Российской Федерации (УК РФ)", ""),
        ("fe66afca01e13d9cd5c9372505774708c90daa44c9410084df9c1b64e0635a03", "Уголовно-процессуальный кодекс Российской Федерации (УПК РФ)", ""),
        ("7f2d72fdcce53161ff2e24ed5f79b59257bb81285e5e7a77c33b32bc88d32848", "Уголовно-исполнительный кодекс Российской Федерации (УИК РФ)", ""),
        ("f7e8a05ab96295ade2be3c550c15fdb66646f0b16f0f65c5aad57b59577acb74", "Земельный кодекс Российской Федерации", ""),
        ("8b4a1920bd1b392ecc9684dc74ddebb02da5af465df06bc4a7ff8c2baf3915ce", "Жилищный кодекс Российской Федерации (ЖК РФ)", ""),
        ("67c8f9935a6c27bd4075a7e8c1965e7acabbdf019e8dcf764d9203a17ddfcf67", "Семейный кодекс Российской Федерации (СК РФ)", ""),
        # Additional codes from pravo.gov.ru
        ("22ab398631ebf15f4c8e7cfbc229e36cd2c98f61dd095934c64eb2106d370190", "Водный кодекс Российской Федерации", ""),
        ("d7e701370b68103b6266adeef0600c62b8fdd7c8517eb57fcfdf22995ab90958", "Лесной кодекс Российской Федерации", ""),
        ("24690944a51bce854e2e6669730e80e6290a77c2c2b574149c999989143e3a0c", "Воздушный кодекс Российской Федерации", ""),
        ("1e6024ba3941a2db83058b219a73643f87ad32f9f6bb5c9fd9b4bcb5aaa3f979", "Бюджетный кодекс Российской Федерации", ""),
        ("1e0511819057ac27ce824e6862488cd24ce5a98760c6aa2116112515fd7fd3d0", "Кодекс внутреннего водного транспорта Российской Федерации", ""),
        ("779dc3826f8590b9834ef488b94ccf833f26cde0f112edc741f9becd629adf96", "Кодекс торгового мореплавания Российской Федерации", ""),
    ]
    
    all_lines = []
    
    for i, (doc_hash, name, part) in enumerate(codes):
        print(f"\n{'='*60}", file=sys.stderr)
        print(f"[{i+1}/{len(codes)}] {name} {part}", file=sys.stderr)
        print(f"{'='*60}", file=sys.stderr)
        
        lines = process_document(doc_hash, name, part)
        
        if i > 0 and lines:
            all_lines.append("")
            all_lines.append("---")
            all_lines.append("")
        
        all_lines.extend(lines)
        time.sleep(0.5)
    
    output_path = "/Users/red/Documents/ai/s/law-bot/output.md"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(all_lines))
    
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"Written to {output_path}", file=sys.stderr)
    print(f"Total lines: {len(all_lines)}", file=sys.stderr)
    file_size = os.path.getsize(output_path)
    print(f"File size: {file_size} bytes ({file_size/1024/1024:.1f} MB)", file=sys.stderr)

if __name__ == "__main__":
    main()
