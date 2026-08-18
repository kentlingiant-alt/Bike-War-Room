import argparse
import concurrent.futures
import json
import os
import re
import time
import webbrowser
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests
import urllib3
from jinja2 import Environment, FileSystemLoader, select_autoescape

# 1. 關閉 SSL 不安全請求警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 2. 請求設定：偽裝 Chrome User-Agent
HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/122.0.0.0 Safari/537.36'
    )
}

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = BASE_DIR / "config.json"
DEFAULT_BRANDS_PATH = BASE_DIR / "brands.json"
TEMPLATE_DIR = BASE_DIR / "templates"


def load_config(config_path):
    """讀取 RSS 來源設定檔 (JSON)。"""
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_brands(brands_path):
    """讀取品牌清單設定檔 (JSON)，每個品牌包含 name 與 aliases（常見譯名）。"""
    with open(brands_path, "r", encoding="utf-8") as f:
        return json.load(f)


def decode_content(response):
    """靈活嘗試解碼文字，優先處理 UTF-8 與 Big5，避免出現亂碼。"""
    if response.encoding and response.encoding.lower() != 'iso-8859-1':
        try:
            return response.text
        except Exception:
            pass

    for encoding in ['utf-8', 'big5', 'gbk', 'cp950']:
        try:
            return response.content.decode(encoding)
        except (UnicodeDecodeError, TypeError):
            continue

    return response.content.decode('utf-8', errors='ignore')


def parse_item_datetime(date_str):
    """
    嘗試解析新聞的發布時間字串，支援：
    - RSS 2.0 的 RFC 822 格式，例如 'Tue, 18 Aug 2026 08:00:00 GMT'
    - Atom 的 ISO 8601 格式，例如 '2026-08-18T08:00:00Z'
    解析失敗回傳 None（畫面上就不會顯示時間，不會讓程式壞掉）。
    """
    if not date_str:
        return None
    date_str = date_str.strip()

    try:
        dt = parsedate_to_datetime(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        pass

    try:
        dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def format_relative_time(dt):
    """把 datetime 轉成『幾分鐘前』『幾小時前』『幾天前』這種相對時間字串。"""
    if dt is None:
        return None

    now = datetime.now(timezone.utc)
    seconds = (now - dt).total_seconds()

    if seconds < 60:
        return '剛剛'
    minutes = seconds / 60
    if minutes < 60:
        return f'{int(minutes)} 分鐘前'
    hours = minutes / 60
    if hours < 24:
        return f'{int(hours)} 小時前'
    days = hours / 24
    if days < 7:
        return f'{int(days)} 天前'
    weeks = days / 7
    if weeks < 5:
        return f'{int(weeks)} 週前'
    months = days / 30
    if months < 12:
        return f'{int(months)} 個月前'
    years = days / 365
    return f'{int(years)} 年前'


def parse_rss_xml(xml_string, max_items=8):
    """解析 RSS 2.0 或 Atom 格式的 XML 內容。"""
    items = []
    try:
        root = ET.fromstring(xml_string)

        for item in root.findall('.//item'):
            title_elem = item.find('title')
            link_elem = item.find('link')
            pub_date_elem = item.find('pubDate')

            title = title_elem.text if title_elem is not None and title_elem.text else '無標題'
            link = link_elem.text if link_elem is not None and link_elem.text else '#'
            pub_date_str = pub_date_elem.text if pub_date_elem is not None and pub_date_elem.text else None
            published_dt = parse_item_datetime(pub_date_str)

            items.append({
                'title': title.strip(),
                'link': link.strip(),
                'relative_time': format_relative_time(published_dt),
            })
            if len(items) >= max_items:
                break

        if not items:
            ns = {'atom': 'http://www.w3.org/2005/Atom'}
            for entry in root.findall('.//atom:entry', ns) or root.findall('.//{http://www.w3.org/2005/Atom}entry'):
                title_elem = entry.find('atom:title', ns) or entry.find('{http://www.w3.org/2005/Atom}title')
                link_elem = entry.find('atom:link', ns) or entry.find('{http://www.w3.org/2005/Atom}link')
                date_elem = (
                    entry.find('atom:published', ns) or entry.find('{http://www.w3.org/2005/Atom}published')
                    or entry.find('atom:updated', ns) or entry.find('{http://www.w3.org/2005/Atom}updated')
                )

                title = title_elem.text if title_elem is not None and title_elem.text else '無標題'
                link = link_elem.attrib.get('href', '#') if link_elem is not None else '#'
                date_str = date_elem.text if date_elem is not None and date_elem.text else None
                published_dt = parse_item_datetime(date_str)

                items.append({
                    'title': title.strip(),
                    'link': link.strip(),
                    'relative_time': format_relative_time(published_dt),
                })
                if len(items) >= max_items:
                    break

    except Exception as e:
        raise ValueError(f"XML 解析失敗: {e}")

    return items


def fetch_single_feed(category, source):
    """單一來源抓取任務 (用於 ThreadPool)"""
    name = source['name']
    url = source['url']
    site = source.get('site', '')

    try:
        response = requests.get(url, headers=HEADERS, verify=False, timeout=10)
        response.raise_for_status()

        xml_text = decode_content(response)
        items = parse_rss_xml(xml_text)

        print(f"✅ [{category}] {name} - 成功抓取 {len(items)} 則新聞")
        return (category, name, site, items, None)

    except Exception as e:
        error_msg = f"❌ [{category}] {name} - 失敗: {str(e)}"
        print(error_msg)
        return (category, name, site, [], error_msg)


TITLE_NORMALIZE_PATTERN = re.compile(
    r'[\s\-–—|:：·,，。.!！?？"\'"\u2018\u2019()（）\[\]【】/\\]+'
)
MIN_TITLE_LENGTH_FOR_DEDUP = 6  # 正規化後太短的標題（例如「無標題」）不參與去重比對


def normalize_title(title):
    """把標題轉成方便比對相似度的形式：轉小寫、去除標點符號與多餘空白。"""
    title = title.lower()
    title = TITLE_NORMALIZE_PATTERN.sub(' ', title)
    return title.strip()


def title_similarity(a, b):
    """回傳兩個正規化後標題的相似度分數 (0~1)。"""
    return SequenceMatcher(None, a, b).ratio()


def deduplicate_category(sources, threshold=0.72):
    """
    對同一分類底下、來自不同來源的新聞做去重。
    保留最先出現的版本，重複的新聞不會被丟棄資訊，而是記錄在 also_from 欄位，
    在頁面上顯示「同時也見於：X、Y」，方便看出哪些新聞被多家媒體報導。
    回傳 (去重後的 sources, 移除的重複則數)。
    """
    kept_items = []  # 保留下來的 item dict（會被加上 also_from 欄位）
    removed_count = 0

    for source in sources:
        filtered_items = []
        for item in source['items']:
            norm = normalize_title(item['title'])
            duplicate_of = None

            if len(norm) >= MIN_TITLE_LENGTH_FOR_DEDUP:
                for kept in kept_items:
                    if title_similarity(norm, kept['_norm']) >= threshold:
                        duplicate_of = kept
                        break

            if duplicate_of:
                if source['source_name'] not in duplicate_of['also_from']:
                    duplicate_of['also_from'].append(source['source_name'])
                removed_count += 1
                continue

            item['_norm'] = norm
            item['also_from'] = []
            kept_items.append(item)
            filtered_items.append(item)

        source['items'] = filtered_items

    # 清除比對用的暫存欄位，避免混進輸出的資料裡
    for item in kept_items:
        item.pop('_norm', None)

    return sources, removed_count


def deduplicate_news(data, threshold=0.72):
    """對整份新聞資料逐分類去重，並印出每個分類移除了幾則重複新聞。"""
    total_removed = 0
    for category, sources in data.items():
        deduped_sources, removed = deduplicate_category(sources, threshold=threshold)
        data[category] = deduped_sources
        total_removed += removed
        if removed:
            print(f"🧹 [{category}] 合併了 {removed} 則重複/相似新聞")

    if total_removed:
        print(f"🧹 總共合併 {total_removed} 則重複新聞\n")

    return data


def build_brand_patterns(brands):
    """
    為每個品牌建立比對用的正規表示式清單（品牌名稱 + 中文譯名）。
    英文名稱使用單字邊界 (\\b) 避免比對到單字的一部分（例如 giant 不會誤中 gigantic）；
    中文譯名因為沒有空白分隔詞彙，直接用子字串比對。
    回傳 {品牌名稱: [pattern, ...]}
    """
    patterns = {}
    for brand in brands:
        name = brand['name']
        terms = [name] + brand.get('aliases', [])
        compiled = []
        for term in terms:
            if term.isascii():
                pattern = re.compile(r'(?<![A-Za-z0-9])' + re.escape(term) + r'(?![A-Za-z0-9])', re.IGNORECASE)
            else:
                pattern = re.compile(re.escape(term))
            compiled.append(pattern)
        patterns[name] = compiled
    return patterns


def detect_brands_in_title(title, brand_patterns):
    """回傳標題裡比對到的品牌名稱清單（依 brands.json 裡的順序）。"""
    matched = []
    for brand_name, patterns in brand_patterns.items():
        if any(p.search(title) for p in patterns):
            matched.append(brand_name)
    return matched


def tag_news_with_brands(data, brands):
    """
    對整份新聞資料的每則新聞標題做品牌比對，並把結果存進 item['brands']。
    回傳 (打完標籤的 data, 命中品牌的新聞則數)。
    """
    brand_patterns = build_brand_patterns(brands)
    tagged_count = 0

    for sources in data.values():
        for source in sources:
            for item in source['items']:
                matched = detect_brands_in_title(item['title'], brand_patterns)
                item['brands'] = matched
                if matched:
                    tagged_count += 1

    return data, tagged_count


def fetch_all_news(rss_config, workers=12):
    """使用多執行緒（ThreadPoolExecutor）同時下載所有來源新聞"""
    results = {cat: [] for cat in rss_config.keys()}

    tasks = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        for category, sources in rss_config.items():
            for source in sources:
                tasks.append(executor.submit(fetch_single_feed, category, source))

        for future in concurrent.futures.as_completed(tasks):
            cat, name, site, items, err = future.result()
            results[cat].append({'source_name': name, 'site': site, 'items': items, 'error': err})

    return results


def generate_html_dashboard(data, output_file="news.html", brand_names=None):
    """使用 Jinja2 模板生成響應式卡片式 HTML 頁面，含左側收藏側邊欄與品牌篩選器"""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(['html', 'j2']),
    )
    template = env.get_template("dashboard.html.j2")
    html_content = template.render(data=data, now_str=now_str, brand_names=brand_names or [])

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"\n🎉 網頁產出成功：{os.path.abspath(output_file)}")


def parse_args():
    parser = argparse.ArgumentParser(description="個人新聞儀表板產生器")
    parser.add_argument(
        "-c", "--config", default=str(DEFAULT_CONFIG_PATH),
        help="RSS 來源設定檔路徑 (JSON)，預設為 config.json"
    )
    parser.add_argument(
        "-o", "--output", default="news.html",
        help="輸出 HTML 檔案路徑，預設為 news.html"
    )
    parser.add_argument(
        "-w", "--workers", type=int, default=12,
        help="同時抓取的執行緒數量，預設 12"
    )
    parser.add_argument(
        "--no-browser", action="store_true",
        help="產生後不要自動開啟瀏覽器"
    )
    parser.add_argument(
        "--no-dedup", action="store_true",
        help="不要合併相似標題的新聞"
    )
    parser.add_argument(
        "--similarity-threshold", type=float, default=0.72,
        help="標題相似度門檻 (0~1)，數字越高代表判定越嚴格，預設 0.72"
    )
    parser.add_argument(
        "-b", "--brands", default=str(DEFAULT_BRANDS_PATH),
        help="品牌清單設定檔路徑 (JSON)，預設為 brands.json"
    )
    parser.add_argument(
        "--no-brand-filter", action="store_true",
        help="不要產生品牌篩選器"
    )
    return parser.parse_args()


def main():
    args = parse_args()

    start_time = time.time()
    print("🚀 開始抓取新聞資料 (多執行緒加速中)...")

    rss_config = load_config(args.config)
    news_data = fetch_all_news(rss_config, workers=args.workers)

    if not args.no_dedup:
        news_data = deduplicate_news(news_data, threshold=args.similarity_threshold)

    brand_names = []
    if not args.no_brand_filter:
        brands = load_brands(args.brands)
        news_data, brand_hit_count = tag_news_with_brands(news_data, brands)
        brand_names = [b['name'] for b in brands]
        if brand_hit_count:
            print(f"🏷️  標記了 {brand_hit_count} 則含品牌關鍵字的新聞")

    generate_html_dashboard(news_data, args.output, brand_names=brand_names)

    print(f"⏱️ 總共耗時: {time.time() - start_time:.2f} 秒")

    if not args.no_browser:
        file_path = os.path.abspath(args.output)
        webbrowser.open(f"file://{file_path}")


if __name__ == "__main__":
    main()
