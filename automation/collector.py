"""External scheduled collector; Python 3.12 standard library only.
Never runs inside the static website. Official structured dates are not human verification.
"""
from __future__ import annotations
import concurrent.futures
import datetime as dt
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import threading
import time
from html import unescape
from html.parser import HTMLParser
from urllib import error, parse, request, robotparser
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
UTC = dt.timezone.utc

def now():
    return dt.datetime.now(UTC).isoformat()

def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()[:24]

def normalized(text):
    return re.sub(r'\s+', ' ', unescape(text)).strip()

def public_url(url):
    p = parse.urlsplit(url)
    if p.scheme not in ('https', 'http') or not p.hostname or p.username or p.password:
        raise ValueError('Only public HTTP(S) URLs are supported')
    if p.port not in (None, 80, 443):
        raise ValueError('Nonstandard port denied')
    for result in socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == 'https' else 80)):
        if not ipaddress.ip_address(result[4][0]).is_global:
            raise ValueError('Non-public network denied')
    return url

class SafeRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)

class Fetcher:
    def __init__(self, config):
        self.config = config
        self.robots = {}
        self.lock = threading.Lock()
        self.opener = request.build_opener(SafeRedirect())
        self.last = {}

    def raw(self, url):
        public_url(url)
        req = request.Request(url, headers={'User-Agent': self.config['user_agent'], 'Accept': 'text/html,application/rss+xml,application/xml;q=0.9,*/*;q=0.5'})
        with self.opener.open(req, timeout=self.config['timeout_seconds']) as response:
            content = response.read(self.config['max_bytes'] + 1)
            if len(content) > self.config['max_bytes']:
                raise ValueError('Response exceeded safety limit')
            charset = response.headers.get_content_charset() or 'utf-8'
            return content.decode(charset, errors='replace'), response.headers.get_content_type(), response.url

    def get(self, url):
        p = parse.urlsplit(url)
        origin = f'{p.scheme}://{p.netloc}'
        # Serialize per-host robot discovery and spacing; never bypass robots or a 401/403.
        with self.lock:
            if origin not in self.robots:
                rp = robotparser.RobotFileParser()
                try:
                    text, _, _ = self.raw(origin + '/robots.txt')
                    rp.parse(text.splitlines())
                    self.robots[origin] = rp
                except error.HTTPError as exc:
                    if exc.code == 404:
                        rp.parse([])
                        self.robots[origin] = rp
                    else:
                        raise ValueError(f'robots unavailable: HTTP {exc.code}') from exc
            rp = self.robots[origin]
            if not rp.can_fetch(self.config['user_agent'], url):
                raise ValueError('robots.txt disallows this URL')
            delay = max(1, rp.crawl_delay(self.config['user_agent']) or 0)
            remaining = delay - (time.monotonic() - self.last.get(origin, 0))
            if remaining > 0:
                time.sleep(min(remaining, 60))
            if delay > 60:
                raise ValueError('crawl-delay too long for this scheduled job')
            self.last[origin] = time.monotonic()
        return self.raw(url)

class EventHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.json_scripts = []
        self.capture = None
        self.hidden = 0
        self.text = []
        self.links = []
        self.anchor = None
        self.title = ''
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'script':
            self.hidden += 1
            if attrs.get('type', '').split(';')[0].strip().lower() == 'application/ld+json':
                self.capture = []
        elif tag in ('style', 'noscript'):
            self.hidden += 1
        if tag == 'title':
            self.in_title = True
        if tag == 'a':
            self.anchor = [attrs.get('href', ''), []]

    def handle_endtag(self, tag):
        if tag == 'script':
            if self.capture is not None:
                try:
                    self.json_scripts.append(json.loads(''.join(self.capture)))
                except (ValueError, TypeError):
                    pass
                self.capture = None
            self.hidden = max(0, self.hidden - 1)
        elif tag in ('style', 'noscript'):
            self.hidden = max(0, self.hidden - 1)
        if tag == 'title':
            self.in_title = False
        if tag == 'a' and self.anchor:
            self.links.append((self.anchor[0], normalized(' '.join(self.anchor[1]))))
            self.anchor = None

    def handle_data(self, text):
        if self.capture is not None:
            self.capture.append(text)
        if self.in_title:
            self.title += text
        if not self.hidden:
            self.text.append(text)
            if self.anchor is not None:
                self.anchor[1].append(text)


def walk_events(node):
    if isinstance(node, list):
        for child in node:
            yield from walk_events(child)
    elif isinstance(node, dict):
        types = node.get('@type', [])
        if isinstance(types, str):
            types = [types]
        if any(str(t).split('/')[-1] in ('Event', 'BusinessEvent', 'EducationEvent', 'ConferenceEvent') for t in types):
            yield node
        for value in node.values():
            if isinstance(value, (dict, list)):
                yield from walk_events(value)


def extract_day(value):
    if isinstance(value, str) and re.match(r'^\d{4}-\d{2}-\d{2}(?:T|$)', value):
        try:
            return dt.date.fromisoformat(value[:10]).isoformat()
        except ValueError:
            pass
    return ''


def excluded(text, plan):
    lower = text.casefold()
    if not any(t.casefold() in lower for t in plan['exclude_terms']):
        return False
    return not any(t in lower for t in ('tokenization', 'tokenisation', '代币化', '代幣化', '토큰화', 'real world asset'))


def structured_item(event, source, page_url, plan, timestamp):
    title = normalized(str(event.get('name', '')))
    if not title or excluded(title, plan):
        return None
    start = extract_day(event.get('startDate'))
    end = extract_day(event.get('endDate')) or start
    status = str(event.get('eventStatus', '')).split('/')[-1]
    location = event.get('location', {})
    if isinstance(location, list):
        location = location[0] if location else {}
    address = location.get('address', {}) if isinstance(location, dict) else {}
    country = address.get('addressCountry', '') if isinstance(address, dict) else ''
    if isinstance(country, dict):
        country = country.get('name', '')
    address_text = json.dumps(location, ensure_ascii=False).casefold()
    alias_match = any(a.casefold() in title.casefold() for a in source['aliases'])
    reasons = []
    if not start:
        reasons.append('구조화된 시작일 없음')
    elif not (plan['scope']['start'] <= start <= plan['scope']['end']):
        reasons.append('대상 기간 밖의 시작일')
    if end < start:
        reasons.append('종료일이 시작일보다 빠름')
    if status in ('EventCancelled', 'EventPostponed', 'EventRescheduled'):
        reasons.append('취소·연기·일정 변경 공지')
    if not alias_match:
        reasons.append('감시 행사와 이름 일치 검토 필요')
    content = (title + ' ' + normalized(str(event.get('description', '')))).casefold()
    financial_terms = ('tokenization', 'tokenisation', 'tokenized', 'tokenised', 'real world asset', 'digital assets', 'onchain finance', '토큰증권', '토큰화', '代幣化', '代币化', 'デジタル証券', 'トークン')
    if not (any(t.casefold() in content for t in plan['institution_terms']) and any(t in content for t in financial_terms)):
        reasons.append('현행 프로그램의 전통 금융·토큰화 연관성 확인 필요')
    # Location is not inferred from a historical event. Require explicit Asian geography.
    asia_markers = ('hong kong', 'hk', 'japan', 'jp', 'tokyo', 'korea', 'kr', 'seoul', 'singapore', 'sg', 'taiwan', 'tw', 'china', 'cn', 'thailand', 'th', 'bangkok', 'vietnam', 'vn', 'malaysia', 'my', 'philippines', 'ph', 'indonesia', 'id', 'macao', 'macau')
    location_verified = bool(country) and str(country).casefold() in asia_markers
    if not location_verified:
        reasons.append('현행 개최국 확인 필요')
    item = {
        'id': 'event-' + digest(page_url + '|' + title),
        'title': title, 'url': page_url, 'source_url': page_url,
        'watch_id': source['id'], 'watch_name': source['name'], 'priority': source['priority'],
        'region': str(country) if country else '현행 개최지 미확인',
        'historical_region': source['region'], 'event_date': start, 'end_date': end,
        'source_kind': 'official_structured', 'kind': '개최 공지',
        'review_required': bool(reasons), 'review_reasons': reasons,
        'scope': 'in_scope' if start and plan['scope']['start'] <= start <= plan['scope']['end'] else 'outside' if start else 'unknown',
        'summary': normalized(str(event.get('description', '')))[:600],
        'event_status': status, 'speakers': [], 'observed_at': timestamp,
        'evidence': '공식 후보 사이트의 schema.org Event. 자동 추출이며 사람의 사실 확인을 뜻하지 않음.'
    }
    for person in event.get('performer', []) if isinstance(event.get('performer'), list) else [event.get('performer')]:
        person_types = person.get('@type', []) if isinstance(person, dict) else []
        if isinstance(person_types, str):
            person_types = [person_types]
        if isinstance(person, dict) and isinstance(person.get('name'), str) and any(str(t).split('/')[-1] == 'Person' for t in person_types):
            aff = person.get('affiliation', {})
            item['speakers'].append({'name': str(person['name'])[:120], 'company': str(aff.get('name', ''))[:160] if isinstance(aff, dict) else '', 'role': str(person.get('jobTitle', ''))[:120], 'url': page_url})
    if item['speakers']:
        item['kind'] = '스피커 공개'
        if any(not s['company'] or not s['role'] for s in item['speakers']):
            item['review_reasons'].append('구조화된 발표자의 소속·직함 확인 필요')
            item['review_required'] = True
    return item


def meaningful_hash(item):
    return digest(json.dumps({k: v for k, v in item.items() if k not in ('observed_at', 'last_seen_at', 'first_seen_at', 'revision', 'change', 'changed_at')}, sort_keys=True, ensure_ascii=False))


def merge_items(previous, incoming, timestamp):
    result = {x['id']: dict(x) for x in previous}
    changes = []
    for item in incoming:
        revision = meaningful_hash(item)
        old = result.get(item['id'])
        changed = old is None or old.get('revision') != revision
        value = {**item, 'revision': revision, 'first_seen_at': old.get('first_seen_at', timestamp) if old else timestamp, 'last_seen_at': timestamp,
                 'change': 'new' if old is None else 'updated' if changed else old.get('change', 'new'),
                 'changed_at': timestamp if changed else old.get('changed_at', timestamp)}
        result[item['id']] = value
        if changed:
            changes.append({'id': item['id'], 'title': item['title'], 'change': value['change'], 'at': timestamp})
    # Missing or failed sources never delete older items.
    return sorted(result.values(), key=lambda x: x.get('changed_at', ''), reverse=True), changes


def rss_query(watch, language='en'):
    aliases = watch['aliases'][:2]
    return '2027 (' + ' OR '.join('"' + a + '"' for a in aliases) + ')'


def collect_rss(query, language, label, watch, fetcher, plan, timestamp):
    locales = {'ko': ('ko', 'KR', 'KR:ko'), 'ja': ('ja', 'JP', 'JP:ja'), 'zh-CN': ('zh-CN', 'CN', 'CN:zh-Hans'), 'zh-TW': ('zh-TW', 'TW', 'TW:zh-Hant')}
    hl, gl, ceid = locales.get(language, ('en', 'SG', 'SG:en'))
    url = 'https://news.google.com/rss/search?' + parse.urlencode({'q': query, 'hl': hl, 'gl': gl, 'ceid': ceid})
    text, _, _ = fetcher.get(url)
    root = ET.fromstring(text)
    items = []
    for node in root.findall('.//item')[:plan['collection']['rss_max_per_query']]:
        title = normalized(node.findtext('title', ''))
        if excluded(title, plan):
            continue
        href = node.findtext('link', '')
        if parse.urlsplit(href).scheme not in ('https', 'http'):
            continue
        publisher = node.find('source')
        source_url = publisher.get('url', '') if publisher is not None else ''
        source_name = publisher.text if publisher is not None else ''
        items.append({'id': 'news-' + digest(href), 'title': title, 'url': href, 'source_url': source_url,
                      'watch_id': watch['id'] if watch else 'discovery', 'watch_name': watch['name'] if watch else label,
                      'priority': watch['priority'] if watch else 'B', 'region': '현행 개최지 미확인',
                      'historical_region': watch['region'] if watch else '아시아 탐색',
                      'event_date': '', 'end_date': '', 'source_kind': 'news_discovery',
                      'kind': '검색 발견', 'review_required': True,
                      'review_reasons': ['검색 기사 후보: 공식 원문·개최연도·날짜·지역·금융 연관성 확인 필요'],
                      'scope': 'unknown', 'summary': f'검색식: {query} · 발행처: {source_name or "미상"}',
                      'published_at': node.findtext('pubDate', ''), 'speakers': [], 'observed_at': timestamp,
                      'evidence': '뉴스 RSS 검색 결과. 게시일은 행사 개최일이 아니며 공식 확인으로 간주하지 않음.'})
    return items, {'id': 'rss-' + digest(query), 'name': label, 'url': url, 'status': 'ok', 'method': 'rss', 'checked_at': timestamp, 'count': len(items)}


def collect_direct(source, fetcher, plan, timestamp, previous_hashes):
    items, checks, hashes = [], [], {}
    queue = [source['url']]
    for index, url in enumerate(queue):
        try:
            html, mime, final_url = fetcher.get(url)
            if mime not in ('text/html', 'application/xhtml+xml'):
                raise ValueError('HTML only; PDF/other content needs manual review')
            page = EventHTML()
            page.feed(html)
            for obj in page.json_scripts:
                for event in walk_events(obj):
                    item = structured_item(event, source, final_url, plan, timestamp)
                    if item:
                        items.append(item)
            text = normalized(' '.join(page.text))
            # Compare only if page plausibly concerns 2027. Never label a generic homepage change as a new event.
            if '2027' in text:
                h = digest(text)
                hashes[url] = h
                if previous_hashes.get(url) != h:
                    items.append({'id': 'page-' + digest(url), 'title': normalized(page.title)[:200] or source['name'],
                                  'url': final_url, 'source_url': final_url, 'watch_id': source['id'], 'watch_name': source['name'],
                                  'priority': source['priority'], 'region': '현행 개최지 미확인', 'historical_region': source['region'],
                                  'event_date': '', 'end_date': '', 'source_kind': 'official_page_change',
                                  'kind': '페이지 변경' if previous_hashes.get(url) else '2027 언급 발견',
                                  'review_required': True, 'review_reasons': ['본문에 2027 언급: 행사 개최일·금융 연관성·변경 내용 검수 필요'],
                                  'scope': 'unknown', 'summary': text[max(0, text.find('2027')-120):text.find('2027')+480],
                                  'page_fingerprint': h, 'speakers': [], 'observed_at': timestamp,
                                  'evidence': '공식 후보 페이지 본문 변화. 시계·배너 등 비의미적 변경 가능.'})
            else:
                hashes[url] = previous_hashes.get(url, '')
            checks.append({'id': 'direct-' + digest(url), 'name': source['name'], 'url': url, 'status': 'ok', 'method': 'html', 'checked_at': timestamp, 'count': len(items)})
            if index == 0:
                for href, label in page.links:
                    dest = parse.urljoin(final_url, href).split('#')[0]
                    if parse.urlsplit(dest).netloc != parse.urlsplit(final_url).netloc:
                        continue
                    if not re.search(r'2027|speaker|agenda|program|event|登壇|講演|嘉賓|嘉宾', href+' '+label, re.I):
                        continue
                    if dest not in queue and not dest.lower().endswith(('.pdf', '.zip', '.jpg', '.png')):
                        queue.append(dest)
                    if len(queue) >= plan['collection']['max_direct_links'] + 1:
                        break
        except Exception as exc:
            checks.append({'id': 'direct-' + digest(url), 'name': source['name'], 'url': url, 'status': 'failed', 'method': 'html', 'checked_at': timestamp, 'error': str(exc)[:240]})
    return items, checks, hashes


def atomic_write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def run():
    plan = json.loads((ROOT/'data/monitor-plan.json').read_text(encoding='utf-8'))
    private = ROOT/'.monitor-state/state.json'
    if private.exists():
        previous = json.loads(private.read_text(encoding='utf-8'))
        if not isinstance(previous.get('items'), list):
            raise ValueError('Existing state invalid: refusing to replace it')
    else:
        previous = {'items': [], 'runs': [], 'page_hashes': {}}
    timestamp = now()
    fetcher = Fetcher(plan['collection'])
    incoming, checks = [], []
    tasks = [(g['query'], g['language'], g['name'], None) for g in plan['keyword_groups']]
    for source in plan['watchlist']:
        lang = 'ko' if source['region'] == '한국' else 'ja' if source['region'] == '일본' else 'zh-TW' if source['region'] == '대만' else 'zh-CN' if source['region'] == '중국' else 'en'
        tasks.append((rss_query(source), lang, source['name'], source))
    with concurrent.futures.ThreadPoolExecutor(max_workers=plan['collection']['max_workers']) as executor:
        futures = {executor.submit(collect_rss, q, lang, label, source, fetcher, plan, timestamp): (q, label) for q, lang, label, source in tasks}
        for future in concurrent.futures.as_completed(futures):
            query, label = futures[future]
            try:
                found, check = future.result()
                incoming.extend(found)
                checks.append(check)
            except Exception as exc:
                checks.append({'id': 'rss-'+digest(query), 'name': label, 'url': '', 'status': 'failed', 'method': 'rss', 'checked_at': timestamp, 'error': str(exc)[:240]})
    hashes = dict(previous.get('page_hashes', {}))
    for source in plan['watchlist']:
        if source.get('direct'):
            found, rows, updated = collect_direct(source, fetcher, plan, timestamp, hashes)
            incoming.extend(found)
            checks.extend(rows)
            hashes.update(updated)
    # A URL found by several searches is not several events; prefer the first exact item ID.
    unique = {}
    for item in sorted(incoming, key=lambda x: (x['id'], x['priority'], x['watch_id'])):
        unique.setdefault(item['id'], item)
    incoming = list(unique.values())
    items, changes = merge_items(previous['items'], incoming, timestamp)
    ok = sum(c['status'] == 'ok' for c in checks)
    status = 'success' if ok == len(checks) and checks else 'partial' if ok else 'failed'
    run_record = {'id': digest(timestamp), 'started_at': timestamp, 'finished_at': now(), 'status': status,
                  'checked': len(checks), 'succeeded': ok, 'failed': len(checks)-ok,
                  'new': sum(c['change']=='new' for c in changes), 'updated': sum(c['change']=='updated' for c in changes), 'checks': checks}
    runs = [run_record] + previous.get('runs', [])[:27]
    last_success = run_record['finished_at'] if status == 'success' else previous.get('last_success_at')
    feed = {'version': 1, 'generated_at': run_record['finished_at'], 'last_success_at': last_success,
            'timezone': plan['timezone'], 'schedule': plan['schedule'], 'scope': plan['scope'],
            'publisher': 'RWA Radar scheduled collector', 'run': run_record, 'runs': runs,
            'items': items[:plan['collection']['max_items']],
            'retention': f"Public feed shows newest {plan['collection']['max_items']} findings; prior state retains all items.",
            'limitations': ['RSS는 후보 탐색이며 공식 원문 검수가 필요합니다.', 'HTML/JSON-LD만 수집합니다. JS 렌더링·이미지·PDF 연사 명단은 사람이 확인해야 합니다.', '예약 실행은 GitHub 서비스 상황에 따라 지연·누락될 수 있습니다.']}
    atomic_write(ROOT/'public-feed/monitor-feed.json', feed)
    atomic_write(ROOT/'public-feed/monitor-plan.json', plan)
    atomic_write(private, {'items': items, 'runs': runs, 'page_hashes': hashes, 'last_success_at': last_success})
    (ROOT/'public-feed/index.html').write_text('<!doctype html><meta charset="utf-8"><title>RWA Radar public research feed</title><h1>RWA Radar 공개 리서치 피드</h1><p>개인 섭외 메모는 포함하지 않습니다. 자동 발견은 행사 확정을 뜻하지 않습니다.</p><a href="monitor-feed.json">monitor-feed.json</a>', encoding='utf-8')
    print(json.dumps({'status': status, 'checks': len(checks), 'failed': len(checks)-ok, 'new': run_record['new'], 'updated': run_record['updated']}, ensure_ascii=False))

if __name__ == '__main__':
    run()
