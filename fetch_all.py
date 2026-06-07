#!/usr/bin/env python3
"""Comprehensive document fetcher and auto-discoverer for consultant.ru."""

import re, urllib.request, time, sys, os

DELAY = 0.4
OUTPUT_PATH = "/Users/red/Documents/ai/s/law-bot/output.md"

def fetch_html(url, timeout=30):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode('utf-8', errors='replace')
    except Exception as e:
        return None

def make_doc_url(law_id):
    """Return (url, prefix) for the given LAW id, trying cons_doc_LAW_ first, then Cons_doc_LAW_."""
    for prefix in ('cons_doc_LAW_', 'Cons_doc_LAW_'):
        url = f"https://www.consultant.ru/document/{prefix}{law_id}/"
        html = fetch_html(url, timeout=8)
        if html and 'не найден' not in html[:500] and '404' not in html[:500]:
            return url, prefix
    return None, None

def get_title(law_id):
    url, _ = make_doc_url(law_id)
    if url:
        html = fetch_html(url, timeout=10)
        if html:
            m = re.search(r'<title>(.*?)</title>', html, re.DOTALL)
            if m:
                t = m.group(1)
                t = re.sub(r' \\\\ КонсультантПлюс.*', '', t)
                return t.strip()
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
        url = None
    elif not url.startswith('http'):
        url = 'https://www.consultant.ru' + url
    return title, url

def parse_toc_items(html):
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
        if part == '<ul>': depth += 1
        elif part == '</ul>': depth -= 1
        else:
            title, url = extract_li_info(part)
            if title:
                items.append((depth, title, url))
    return items

def fetch_page_content(url):
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
        if not inner or '<hr' in inner:
            continue
        if '<div' in inner:
            for ip in re.findall(r'<p[^>]*>(.*?)</p>', inner, re.DOTALL):
                t = strip_tags(ip)
                if t:
                    lines.append(t)
            continue
        text = strip_tags(inner)
        if text:
            lines.append(text)
    return lines

def fetch_preamble(html):
    m = re.search(r'<div class="document-page__content[^>]*>(.*?)</div>\s*<div class="document-page__toc"', html, re.DOTALL)
    if not m:
        m = re.search(r'<div class="document-page__content[^>]*>(.*?)</div>', html, re.DOTALL)
    if not m:
        return []
    content = m.group(1)
    lines = []
    for p in re.findall(r'<p[^>]*>(.*?)</p>', content, re.DOTALL):
        inner = p.strip()
        if not inner or '<div class="document__insert' in inner or '<hr' in inner:
            continue
        text = strip_tags(inner)
        if text and text not in ('(см. Обзор изменений данного документа)', 'Обзор изменений данного документа'):
            lines.append(text)
    return lines

def is_skip_title(text):
    return bool(re.match(r'^(Статья|Глава|Раздел|§)\s', text.strip()))

def fetch_document(doc_title, doc_id):
    base_url, prefix = make_doc_url(doc_id)
    if not base_url:
        print(f"\nERROR: Document not found (LAW_{doc_id})", file=sys.stderr)
        return []
    print(f"\nFetching: {doc_title} (LAW_{doc_id})", file=sys.stderr)
    
    html = fetch_html(base_url)
    if not html:
        print(f"  ERROR: Could not fetch", file=sys.stderr)
        return []
    
    preamble = fetch_preamble(html)
    toc_items = parse_toc_items(html)
    
    if not toc_items:
        print(f"  No TOC found", file=sys.stderr)
        content = extract_content_div(html)
        if content:
            lines = [f"# {doc_title}", ""]
            for p_html in re.findall(r'<p[^>]*>(.*?)</p>', content, re.DOTALL):
                text = strip_tags(p_html)
                if text:
                    lines.append(text); lines.append("")
            return lines
        return []
    
    depth_heading = {1: '###', 2: '######', 3: '######'}
    lines = [f"# {doc_title}", ""]
    for p in preamble:
        lines.append(p); lines.append("")
    
    count = 0
    for depth, title, url in toc_items:
        heading = depth_heading.get(depth, '######')
        if url:
            count += 1
            if count % 10 == 0:
                print(f"  {count}/{len(toc_items)}...", file=sys.stderr)
            lines.append(f"{heading} {title}")
            lines.append("")
            for t in fetch_page_content(url):
                if not is_skip_title(t):
                    lines.append(t); lines.append("")
            time.sleep(DELAY)
        else:
            lines.append(f"{heading} {title}")
            lines.append("")
    
    print(f"  Done: {count} items, {len(lines)} lines", file=sys.stderr)
    return lines

def append_to_output(lines):
    existing = ""
    if os.path.exists(OUTPUT_PATH):
        with open(OUTPUT_PATH, "r", encoding="utf-8") as f:
            existing = f.read()
    new_content = existing
    if existing and not existing.endswith('\n\n---\n\n'):
        if not existing.endswith('\n'):
            new_content += '\n'
        new_content += '\n---\n\n'
    new_content += '\n'.join(lines)
    if not new_content.endswith('\n'):
        new_content += '\n'
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(new_content)
    sz = os.path.getsize(OUTPUT_PATH)
    print(f"Written: {len(new_content.split(chr(10)))} lines, {sz/1024/1024:.1f} MB", file=sys.stderr)

# === DOCUMENTS list ===
# Format: (title, law_id)
DOCUMENTS = [
    ("Постановление Правительства РФ от 06.05.2011 № 354 «О предоставлении коммунальных услуг собственникам и пользователям помещений в многоквартирных домах и жилых домов»", 114247),
    ("Постановление Правительства РФ от 29.12.2011 № 1178 «О ценообразовании в области регулируемых цен (тарифов) в электроэнергетике»", 125116),
    ("Постановление Правительства РФ от 22.10.2012 № 1075 «О ценообразовании в сфере теплоснабжения»", 136932),
    ("Постановление Правительства РФ от 27.12.2010 № 1172 «Об утверждении Правил оптового рынка электрической энергии и мощности»", 112537),
    ("Постановление Правительства РФ от 30.11.2021 № 2115 «Об утверждении Правил подключения (технологического присоединения) к системам теплоснабжения»", 401940),
    ("Постановление Правительства РФ от 05.02.1998 № 162 «Об утверждении Правил поставки газа в Российской Федерации»", 17781),
    ("Постановление Правительства РФ от 15.04.1995 № 332 «О мерах по упорядочению государственного регулирования цен на газ и сырье для его производства»", 6375),
    ("Постановление Правительства РФ от 27.12.1997 № 1629 «О совершенствовании порядка государственного регулирования тарифов на электрическую и тепловую энергию»", 17259),
    ("Постановление Правительства РФ от 20.11.2000 № 878 «Об утверждении Правил охраны газораспределительных сетей»", 29306),
    ("Постановление Правительства РФ от 30.06.2004 № 331 «Об утверждении Положения о Федеральной антимонопольной службе»", 48611),
    ("Постановление Правительства РФ от 09.01.2009 № 14 «Об утверждении Правил урегулирования споров, связанных с применением платы за реализацию сетевой организацией мероприятий по технологическому присоединению»", 83794),
    ("Распоряжение Правительства РФ от 01.06.2021 № 1447-р «Об утверждении Плана мероприятий по реализации Энергетической стратегии Российской Федерации на период до 2035 года»", 386439),
    ("Приказ Минэнерго России от 14.10.2013 № 718 «Об утверждении Методических указаний по расчету уровня надежности и качества...»", 157706),
    ("Приказ Минэнерго России от 06.05.2014 № 250 «Об утверждении Методических указаний по определению степени загрузки...»", 164182),
    ("Приказ Минэнерго России от 07.08.2014 № 506 «Об утверждении Методики определения нормативов потерь электрической энергии...»", 169034),
    ("Приказ ФСТ России от 06.08.2004 № 20-э/2 «Об утверждении Методических указаний по расчету регулируемых тарифов и цен...»", 50075),
    ("Приказ ФСТ России от 16.09.2014 № 1442-э «Об утверждении Методических указаний по расчету тарифов на электрическую энергию (мощность) для населения...»", 170355),
    ("Приказ ФСТ России от 30.03.2012 № 228-э «Об утверждении Методических указаний по регулированию тарифов с применением метода доходности инвестированного капитала»", 128373),
    ("Приказ ФСТ России от 17.02.2012 № 98-э «Об утверждении Методических указаний по расчету тарифов на услуги по передаче электрической энергии...»", 126941),
    ("Приказ ФСТ России от 11.09.2012 № 209-э/1 «Об утверждении Методических указаний по определению размера платы за технологическое присоединение к электрическим сетям»", 138396),
    ("Приказ ФСТ России от 12.04.2012 № 53-э/1 «Об утверждении Порядка формирования сводного прогнозного баланса производства и поставок электрической энергии...»", 130162),
    ("Приказ ФСТ России от 11.09.2014 № 215-э/1 «Об утверждении Методических указаний по определению выпадающих доходов, связанных с осуществлением технологического присоединения к электрическим сетям»", 144445),
]

def find_missing_docs():
    """Auto-discover document IDs for remaining documents by scanning ID ranges."""
    print("\n=== Auto-discovering missing document IDs ===", file=sys.stderr)
    
    searches = [
        (120000, 125000, 'Постановление', '354', 'коммунальн'),
        (125000, 135000, 'Постановление', '1178', 'теплоснабж'),
        (130000, 140000, 'Постановление', '1075', 'водоснабж'),
        (140000, 150000, 'Постановление', '2050', 'присоединен'),
        (137000, 145000, 'Постановление', '1120', 'газификац'),
        (100000, 115000, 'Постановление', '14', ''),
        (75000, 85000, 'Постановление', '331', 'ФАС'),
        (130000, 140000, 'Постановление', '1172', ''),
        (55000, 65000, 'Постановление', '1629', 'энергетическ'),
        (47000, 51000, 'Постановление', '332', ''),
        (65000, 75000, 'Постановление', '878', 'газораспредел'),
        (95000, 110000, 'Постановление', '162', 'газа'),
        (100000, 115000, 'Распоряжение', '1447-р', ''),
        (120000, 140000, 'Приказ', '20-э/2', ''),
        (120000, 140000, 'Приказ', '98-э', ''),
        (120000, 140000, 'Приказ', '54-э/1', ''),
        (120000, 140000, 'Приказ', '229-э/2', ''),
        (120000, 140000, 'Приказ', '144-э/2', ''),
        (120000, 140000, 'Приказ Минэнерго', '103', ''),
        (120000, 140000, 'Приказ Минэнерго', '107', ''),
        (120000, 140000, 'Приказ Минэнерго', '340', ''),
        (120000, 140000, 'Приказ Минэнерго', '1029', ''),
        (120000, 140000, 'Приказ Минэнерго', '506', ''),
        (120000, 140000, 'Приказ Минэнерго', '453', ''),
        (130000, 150000, 'Приказ ФАС', '106/23', ''),
        (47000, 55000, 'Постановление', '861', ''),  # already known
        (13000, 20000, 'Постановление', '162', ''),
        (12000, 18000, 'Постановление', '332', ''),
        (11000, 16000, 'Постановление', '1629', ''),
        (13000, 16000, 'Постановление', '878', ''),
        (8000, 12000, 'Постановление', '1021', ''),
    ]
    
    found = []
    for start, end, dtype, dnum, kw in searches:
        print(f"  Scanning {start}-{end} for {dtype} {dnum}...", file=sys.stderr)
        for law_id in range(start, end, 50):
            title = get_title(law_id)
            if title:
                if 'Постановление' in title and dtype == 'Постановление':
                    if dnum in title and (not kw or kw in title.lower()):
                        print(f"  >>> FOUND: LAW_{law_id}: {title[:100]}", file=sys.stderr)
                        found.append((law_id, title))
                elif 'Приказ' in title and dtype == 'Приказ':
                    if dnum in title and (not kw or kw in title.lower()):
                        print(f"  >>> FOUND: LAW_{law_id}: {title[:100]}", file=sys.stderr)
                        found.append((law_id, title))
                elif 'Распоряжение' in title and dtype == 'Распоряжение':
                    if dnum in title and (not kw or kw in title.lower()):
                        print(f"  >>> FOUND: LAW_{law_id}: {title[:100]}", file=sys.stderr)
                        found.append((law_id, title))
            time.sleep(0.15)
    
    return found

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == '--discover':
        found = find_missing_docs()
        print(f"\nFound {len(found)} documents:", file=sys.stderr)
        for law_id, title in found:
            print(f"  LAW_{law_id}: {title[:100]}", file=sys.stderr)
        sys.exit(0)
    
    all_lines = []
    for i, (title, doc_id) in enumerate(DOCUMENTS):
        doc_lines = fetch_document(title, doc_id)
        if doc_lines:
            if all_lines:
                all_lines.append(""); all_lines.append("---"); all_lines.append("")
            all_lines.extend(doc_lines)
    
    if all_lines:
        append_to_output(all_lines)
    else:
        print("No documents to write!", file=sys.stderr)
