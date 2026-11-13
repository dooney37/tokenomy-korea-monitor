"""External scheduled collector; Python 3.12 standard library only.
Never runs inside the static website. Official structured dates are not human verification.
"""
from __future__ import annotations
import concurrent.futures
import datetime as dt
import difflib
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

class TargetRedirect(SafeRedirect):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # The redirected path may have different robots rules. Ask for the final URL
        # instead of fetching it before checking those rules.
        raise ValueError('Target redirected: submit the final public browser URL directly')


class Fetcher:
    def __init__(self, config):
        self.config = config
        self.robots = {}
        self.robot_errors = {}
        self.responses = {}
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
            if url in self.responses:
                return self.responses[url]
            if origin in self.robot_errors:
                raise ValueError(self.robot_errors[origin])
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
                        self.robot_errors[origin] = f'robots unavailable: HTTP {exc.code}'
                        raise ValueError(self.robot_errors[origin]) from exc
                except Exception as exc:
                    self.robot_errors[origin] = 'robots unavailable: '+str(exc)[:160]
                    raise ValueError(self.robot_errors[origin]) from exc
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
        result = self.raw(url)
        with self.lock:
            self.responses[url] = result
        return result

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
        self.feeds = []

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
        if tag == 'link' and 'alternate' in attrs.get('rel', '').lower() and attrs.get('type', '').lower() in ('application/rss+xml', 'application/atom+xml'):
            self.feeds.append(attrs.get('href', ''))
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
            aff = person.get('affiliation') or person.get('worksFor', {})
            if isinstance(aff, list):
                aff = aff[0] if aff else {}
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


def recover_title_links(title, original_url, fetcher, timestamp):
    """One bounded public-API title requery; actual mirror access/title must match."""
    phrase = re.sub(r'[\"()\\\\]', ' ', title)[:140]
    query_url = 'https://api.gdeltproject.org/api/v2/doc/doc?' + parse.urlencode({'query':'"'+normalized(phrase)+'"','mode':'ArtList','format':'json','maxrecords':5,'timespan':'3months'})
    links, checks = [], []
    try:
        time.sleep(5)
        text, _, _ = fetcher.get(query_url)
        value = json.loads(text)
        if not isinstance(value,dict) or ('articles' not in value and value) or not isinstance(value.get('articles',[]),list):
            raise ValueError('Unexpected title search response')
        checks.append({'id':'title-query-'+digest(title),'name':title[:100],'url':query_url,'status':'ok','method':'title_requery','checked_at':timestamp})
        tested = 0
        for article in value.get('articles',[]):
            href = str(article.get('url',''))
            headline = normalized(str(article.get('title','')))
            if href==original_url or parse.urlsplit(href).scheme not in ('http','https') or difflib.SequenceMatcher(None,title.casefold(),headline.casefold()).ratio()<0.75:
                continue
            if tested >= 2:
                break
            tested += 1
            try:
                body,mime,final_url=fetcher.get(href)
                if mime not in ('text/html','application/xhtml+xml'):
                    raise ValueError('Mirror is not supported HTML')
                page=EventHTML();page.feed(body)
                if difflib.SequenceMatcher(None,title.casefold(),normalized(page.title).casefold()).ratio()<0.65:
                    raise ValueError('Accessible page title did not match the original headline')
                links.append({'url':final_url,'title':normalized(page.title)[:200],'checked_at':timestamp,'method':'gdelt_title_requery','status':'title_match_accessible'})
                checks.append({'id':'title-mirror-'+digest(href),'name':title[:100],'url':href,'status':'ok','method':'title_mirror','checked_at':timestamp})
            except Exception as exc:
                checks.append({'id':'title-mirror-'+digest(href),'name':title[:100],'url':href,'status':'failed','method':'title_mirror','checked_at':timestamp,'error':str(exc)[:200]})
    except Exception as exc:
        checks.append({'id':'title-query-'+digest(title),'name':title[:100],'url':query_url,'status':'failed','method':'title_requery','checked_at':timestamp,'error':str(exc)[:200]})
    return links,checks


def collect_public_news(query, fetcher, plan, timestamp):
    """Optional public, keyless GDELT Doc API. Separate coverage, not Google RSS bypass.
    API titles remain unverified; original article access is checked independently.
    """
    config = plan.get('public_news_api', {})
    url = 'https://api.gdeltproject.org/api/v2/doc/doc?' + parse.urlencode({'query':query,'mode':'ArtList','format':'json','maxrecords':min(20,int(config.get('max_records',10))),'timespan':'3months'})
    text, _, _ = fetcher.get(url)
    result = json.loads(text)
    if not isinstance(result, dict) or ('articles' not in result and result):
        raise ValueError('Unexpected public news API response')
    articles = result.get('articles', [])
    if not isinstance(articles, list):
        raise ValueError('Public API articles must be a list')
    items, checks = [], []
    max_origins = int(config.get('max_article_checks', 3))
    title_retries = 0
    for index, article in enumerate(articles[:min(10, int(config.get('max_records',10)))]):
        title = normalized(str(article.get('title','')))
        href = str(article.get('url',''))
        p = parse.urlsplit(href)
        if not title or excluded(title,plan) or p.scheme not in ('http','https') or p.username or p.password:
            continue
        accessed, access_error = False, '이번 회차 원문 접근 확인 예산 밖'
        snippet = ''
        if index < max_origins:
            try:
                body, mime, final_url = fetcher.get(href)
                if mime not in ('text/html','application/xhtml+xml'):
                    raise ValueError('Original article is not supported HTML')
                page = EventHTML();page.feed(body)
                snippet = normalized(' '.join(page.text))[:350]
                accessed, access_error = True, ''
                checks.append({'id':'news-origin-'+digest(href),'name':title[:100],'url':href,'status':'ok','method':'news_origin','checked_at':timestamp})
            except Exception as exc:
                access_error = str(exc)[:220]
                checks.append({'id':'news-origin-'+digest(href),'name':title[:100],'url':href,'status':'failed','method':'news_origin','checked_at':timestamp,'error':access_error})
        alternatives = []
        if not accessed and index < max_origins and title_retries < int(config.get('title_retry_limit',1)):
            title_retries += 1
            alternatives, rows = recover_title_links(title, href, fetcher, timestamp)
            checks.extend(rows)
        watch = next((s for s in plan['watchlist'] if any(len(a)>3 and a.casefold() in title.casefold() for a in s['aliases'])), None)
        reasons=['공개 뉴스 API 제목 후보: 공식 근거·개최연도·지역·금융 연관성 검수 필요']
        if not accessed:
            reasons.append('원문 접근 미확인: '+access_error)
        items.append({'id':'news-'+digest(href),'title':title,'url':href,'source_url':href,
                      'watch_id':watch['id'] if watch else 'public-news','watch_name':watch['name'] if watch else '공개 뉴스 API 탐색',
                      'priority':watch['priority'] if watch else 'B','region':'현행 개최지 미확인','historical_region':watch['region'] if watch else '미확인',
                      'event_date':'','end_date':'','source_kind':'news_discovery','kind':'대체 뉴스 발견',
                      'review_required':True,'review_reasons':reasons,'scope':'unknown','summary':('공개 API 제목 · '+str(article.get('domain',''))+' · '+snippet)[:600],
                      'published_at':str(article.get('seendate','')),'speakers':[],'observed_at':timestamp,'retrieval_method':'gdelt_doc_api',
                      'original_access':accessed,'alternate_links':alternatives,'evidence':'GDELT 공개 Doc API 검색 후보. 기사 원문 접근과 공식 행사 사실 확인은 별개. 게시 시각을 행사일로 사용하지 않음.'})
    checks.insert(0,{'id':'news-api-'+digest(query),'name':'GDELT 공개 뉴스 API','url':url,'status':'ok','method':'news_api','checked_at':timestamp,'count':len(items)})
    return items, checks, {'provider':'GDELT Doc API','query':query,'status':'ok','count':len(items),'originals_accessed':sum(x['original_access'] for x in items),'note':'별도 뉴스 색인 범위의 후보 탐색. 전체 뉴스·Google RSS 복구를 뜻하지 않음.'}


def candidate_link_score(url, label, source, titles=()):
    value = (parse.unquote(url) + ' ' + label).casefold()
    score = 5 if '2027' in value else 0
    if re.search(r'speaker|agenda|program|conference|summit|forum|登壇|講演|嘉賓|嘉宾|포럼|연사', value):
        score += 4
    if re.search(r'news|events|공지|소식', value):
        score += 2
    terms = source.get('aliases', []) + [source.get('name', '')] + list(titles)[:12]
    if any(len(t) > 3 and t.casefold() in value for t in terms):
        score += 8
    return score


def allowed_same_site(url, origin):
    try:
        p = parse.urlsplit(url)
        base = parse.urlsplit(origin)
        host = (p.hostname or '').lower().removeprefix('www.')
        base_host = (base.hostname or '').lower().removeprefix('www.')
        return p.scheme in ('http', 'https') and host == base_host and not p.username and not p.password and p.port in (None, 80, 443)
    except ValueError:
        return False


def xml_locations(text):
    root = ET.fromstring(text)
    is_index = root.tag.split('}')[-1] == 'sitemapindex'
    return is_index, [node.text.strip() for node in root.iter() if node.tag.split('}')[-1] == 'loc' and node.text][:2000]


def feed_links(text):
    root = ET.fromstring(text)
    links = []
    for node in root.iter():
        tag = node.tag.split('}')[-1]
        if tag not in ('item', 'entry'):
            continue
        title = next((c.text or '' for c in node if c.tag.split('}')[-1] == 'title'), '')
        href = next((c.get('href') or c.text or '' for c in node if c.tag.split('}')[-1] == 'link'), '')
        if href:
            links.append((href.strip(), normalized(title)))
    return links[:100]


def collect_recovery(source, fetcher, plan, timestamp, previous_items):
    """Bounded search WITHIN known source sites, not a search-engine or robots bypass.
    A failed RSS query contains no recovered article title. Only known brands and
    titles of previously fetched documents can guide this fallback.
    """
    config = plan.get('recovery', {})
    budget = min(12, int(config.get('max_requests_per_group', 7)))
    max_matches = min(8, int(config.get('max_matches_per_group', 3)))
    checks, items, checked, queue, maps, matches = [], [], set(), [], [], []
    source_url = source['url']
    parsed = parse.urlsplit(source_url)
    origin = f'{parsed.scheme}://{parsed.netloc}'
    titles = [x.get('title', '') for x in previous_items if x.get('watch_id') == source['id']]
    seeds = [source_url]
    seeds += [u for u in source.get('fallback_urls', []) if allowed_same_site(u, origin)]
    seeds += [x['url'] for x in previous_items if x.get('watch_id') == source['id'] and allowed_same_site(x.get('url', ''), origin)]
    seeds.append(origin + '/')
    # First two distinct known pages; never guess a year-specific URL.
    for url in dict.fromkeys(seeds):
        if allowed_same_site(url, origin):
            queue.append((url.split('#')[0], 'known_page'))
        if len(queue) >= 2:
            break
    requests_used = 0
    def read(url, method):
        nonlocal requests_used
        if requests_used >= budget or url in checked:
            return None
        checked.add(url)
        requests_used += 1
        try:
            body, mime, final_url = fetcher.get(url)
            # Redirects must not expand the approved source scope for content analysis.
            if not allowed_same_site(final_url, origin):
                raise ValueError('Redirect left the known source host; manual approval required')
            checks.append({'id':'recovery-'+digest(url), 'name':source['name'], 'url':url, 'status':'ok', 'method':method, 'checked_at':timestamp})
            return body, mime, final_url
        except Exception as exc:
            checks.append({'id':'recovery-'+digest(url), 'name':source['name'], 'url':url, 'status':'failed', 'method':method, 'checked_at':timestamp, 'error':str(exc)[:240]})
            return None
    def analyze_page(url, method):
        result = read(url, method)
        if result is None:
            return [], []
        body, mime, final_url = result
        if mime not in ('text/html','application/xhtml+xml'):
            checks[-1]['status'] = 'failed'
            checks[-1]['error'] = 'Unsupported content: HTML required; no automatic PDF/JS bypass'
            return [], []
        page = EventHTML()
        page.feed(body)
        text = normalized(' '.join(page.text))
        # Old year in a title or robots/HTTP errors never establishes a 2027 event.
        if '2027' in text and not excluded(normalized(page.title)+' '+text[:600], plan):
            matches.append({'url':final_url, 'title':normalized(page.title)[:200] or source['name'], 'checked_at':timestamp, 'method':method, 'status':'accessible_2027_mention'})
            for obj in page.json_scripts:
                for event in walk_events(obj):
                    found = structured_item(event, source, final_url, plan, timestamp)
                    if found:
                        found['retrieval_method'] = method
                        found['review_required'] = True
                        found['review_reasons'].append('대체 경로 출처·본행사/세션 구분 확인 필요')
                        items.append(found)
            position = text.find('2027')
            items.append({'id':'page-'+digest(final_url), 'title':normalized(page.title)[:200] or source['name'],
                          'url':final_url, 'source_url':final_url, 'watch_id':source['id'], 'watch_name':source['name'],
                          'priority':source['priority'], 'region':'현행 개최지 미확인', 'historical_region':source['region'],
                          'event_date':'', 'end_date':'', 'source_kind':'official_page_change', 'kind':'대체 링크 발견',
                          'review_required':True, 'review_reasons':['알려진 출처의 접근 가능한 링크에 2027 언급: 개최일·금융 연관성 검수 필요'],
                          'scope':'unknown', 'summary':text[max(0,position-120):position+480], 'page_fingerprint':digest(text),
                          'retrieval_method':method, 'speakers':[], 'observed_at':timestamp,
                          'evidence':'알려진 출처의 본문 접근 성공과 2027 언급만 확인. 행사 개최·공식성·스피커 확정을 의미하지 않음.'})
        ranked = []
        for href, label in page.links:
            dest = parse.urljoin(final_url, href).split('#')[0]
            if not allowed_same_site(dest, origin) or dest.lower().split('?')[0].endswith(('.pdf','.zip','.jpg','.png','.js','.css')):
                continue
            score = candidate_link_score(dest, label, source, titles)
            if score >= 4:
                ranked.append((score, dest, 'fallback_link'))
        public_feeds = [parse.urljoin(final_url, href) for href in page.feeds]
        return ranked, [u for u in public_feeds if allowed_same_site(u, origin)]
    ranked, public_feeds = [], []
    for url, method in queue:
        links, feeds = analyze_page(url, method)
        ranked.extend(links)
        public_feeds.extend(feeds)
    # The site's own advertised RSS/Atom is checked, not an unauthorised search API.
    for url in list(dict.fromkeys(public_feeds))[:1]:
        result = read(url, 'site_feed')
        if result:
            try:
                for href, label in feed_links(result[0]):
                    dest = parse.urljoin(url, href)
                    if allowed_same_site(dest, origin):
                        score = candidate_link_score(dest, label, source, titles)
                        if score >= 4:
                            ranked.append((score, dest, 'site_feed_link'))
            except Exception as exc:
                checks[-1].update(status='failed',error='Feed parse failed: '+str(exc)[:180])
    # Sitemap paths come from robots where available; /sitemap.xml is a checked probe,
    # never an asserted accessible or 2027 page until an actual successful read.
    rp = getattr(fetcher, 'robots', {}).get(origin)
    if rp is not None:
        maps.extend(rp.site_maps() or [])
    maps.append(origin+'/sitemap.xml')
    if requests_used < budget-1:
        for sitemap in dict.fromkeys(maps):
            if not allowed_same_site(sitemap, origin):
                continue
            result = read(sitemap, 'sitemap')
            if result:
                try:
                    index, locs = xml_locations(result[0])
                    if index:
                        children = sorted((u for u in locs if allowed_same_site(u, origin)), key=lambda u:candidate_link_score(u,'',source,titles),reverse=True)
                        if children and requests_used < budget-1:
                            nested = read(children[0], 'sitemap')
                            if nested:
                                _, locs = xml_locations(nested[0])
                    for dest in locs:
                        if allowed_same_site(dest, origin):
                            score = candidate_link_score(dest,'',source,titles)
                            if score >= 4:
                                ranked.append((score,dest,'sitemap_link'))
                except Exception as exc:
                    checks[-1].update(status='failed',error='Sitemap parse failed: '+str(exc)[:160])
            break
    for _, url, method in sorted(set(ranked), reverse=True):
        if requests_used >= budget or len(matches) >= max_matches:
            break
        if url not in checked:
            analyze_page(url, method)
    unique_matches = list({x['url']:x for x in matches}.values())
    state = 'found' if unique_matches else 'checked_no_match' if any(x['status']=='ok' for x in checks) else 'failed'
    summary = {'watch_id':source['id'], 'name':source['name'], 'status':state, 'checks':len(checks), 'accessible_links':unique_matches,
               'note':'제목·알려진 출처 범위의 대체 탐색. 광범위한 뉴스 검색 복구가 아니며 2027 언급만으로 행사 확정 금지.'}
    return items, checks, summary


def atomic_write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def manual_url(value):
    """Reject credentials, queries and private/opaque addresses before any target request."""
    if not isinstance(value, str) or len(value) > 2000:
        raise ValueError('Public URL must be at most 2000 characters')
    p = parse.urlsplit(value.strip())
    if p.query or p.fragment:
        raise ValueError('Remove query/fragment; never submit signed or private URLs')
    return public_url(value.strip())


def collect_target(url, fetcher, plan, timestamp):
    """Owner-dispatched single HTML page. No automatic enrollment or inferred people."""
    items = []
    safe_target = ''
    try:
        url = manual_url(url)
        safe_target = url
        html, mime, final_url = fetcher.get(url)
        manual_url(final_url)
        if mime not in ('text/html', 'application/xhtml+xml'):
            raise ValueError('HTML only; PDF/image/JavaScript speaker lists need manual review')
        page = EventHTML(); page.feed(html)
        source = {'id':'manual-'+digest(url), 'name':normalized(page.title)[:160] or '지정 URL 조사', 'aliases':[], 'priority':'A', 'region':'현행 개최지 미확인'}
        for event in list(walk_events(page.json_scripts))[:20]:
            item = structured_item(event, source, final_url, plan, timestamp)
            if item:
                item['id'] = 'manual-'+digest(final_url+'|'+item['title'])
                item['requested_url'] = url
                item['manual_request'] = True
                item['review_required'] = True
                item['review_reasons'].append('사용자 지정 URL: 공식성·현행 연도·연사 참석·소속을 직접 검수해야 함')
                item['evidence'] = '사용자 지정 공개 HTML의 Event/performer 구조화 정보. 출처 공식성·참석 확정 아님.'
                items.append(item)
        if not items:
            items.append({'id':'manual-page-'+digest(final_url), 'title':source['name'], 'url':final_url, 'source_url':final_url,
                          'requested_url':url, 'manual_request':True, 'watch_id':source['id'], 'watch_name':source['name'], 'priority':'A',
                          'region':'현행 개최지 미확인', 'event_date':'', 'end_date':'', 'source_kind':'official_page_change',
                          'kind':'지정 URL 조사', 'review_required':True, 'review_reasons':['구조화된 Event/performer 없음: 본문을 직접 검수하고 행사·연사를 수동 입력해야 함'],
                          'scope':'unknown', 'summary':normalized(' '.join(page.text))[:1800], 'speakers':[], 'observed_at':timestamp,
                          'evidence':'지정 URL의 HTML 본문 발췌. 날짜·연사·공식성을 추정하여 채우지 않음.'})
        check = {'id':'target-'+digest(url), 'name':'지정 URL 조사', 'url':url, 'status':'ok', 'method':'manual_url', 'checked_at':timestamp, 'count':len(items)}
    except Exception as exc:
        check = {'id':'target-'+digest(url), 'name':'지정 URL 조사', 'url':safe_target, 'status':'failed', 'method':'manual_url', 'checked_at':timestamp, 'error':str(exc)[:240]}
    return items, check


def run_target(url, previous, fetcher, plan, timestamp, private):
    found, check = collect_target(url, fetcher, plan, timestamp)
    items, changes = merge_items(previous['items'], found, timestamp)
    ok = check['status'] == 'ok'
    record = {'id':digest(timestamp), 'mode':'target_url', 'target_url':check['url'], 'started_at':timestamp, 'finished_at':now(),
              'status':'success' if ok else 'failed', 'checked':1, 'succeeded':int(ok), 'failed':int(not ok),
              'new':sum(c['change']=='new' for c in changes), 'updated':sum(c['change']=='updated' for c in changes), 'checks':[check]}
    runs = [record]+previous.get('runs', [])[:27]
    # Do not advance the last full-monitoring success timestamp for a one-page request.
    feed = {'version':1, 'collector_version':'3.0', 'generated_at':record['finished_at'], 'last_success_at':previous.get('last_success_at'),
            'timezone':plan['timezone'], 'schedule':plan['schedule'], 'scope':plan['scope'], 'publisher':'RWA Radar owner-requested URL research',
            'run':record, 'runs':runs, 'items':items[:plan['collection']['max_items']],
            'limitations':['지정 URL 한 페이지만 조사한 결과이며 전체 모니터링의 복구·성공을 의미하지 않습니다.', 'HTML/JSON-LD Event와 performer만 추출합니다. 검수 후 선택 등록해야 합니다.']}
    atomic_write(ROOT/'public-feed/monitor-feed.json', feed)
    atomic_write(ROOT/'public-feed/monitor-plan.json', plan)
    atomic_write(private, {**previous, 'items':items, 'runs':runs})
    (ROOT/'public-feed/index.html').write_text('<!doctype html><meta charset="utf-8"><title>RWA Radar public feed</title><a href="monitor-feed.json">Public research feed</a>', encoding='utf-8')
    print(json.dumps({'mode':'target_url', 'status':record['status'], 'new':record['new'], 'updated':record['updated']}))


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
    target_url = os.environ.get('RADAR_TARGET_URL', '').strip()
    if target_url:
        fetcher.opener = request.build_opener(TargetRedirect())
        return run_target(target_url, previous, fetcher, plan, timestamp, private)
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
    recovery_records = []
    news_records = []
    news_config = plan.get('public_news_api', {})
    if news_config.get('enabled',False):
        for index, query in enumerate(news_config.get('queries',[])[:2]):
            if index:
                time.sleep(max(5,int(news_config.get('min_interval_seconds',5))))
            try:
                found, rows, summary = collect_public_news(query, fetcher, plan, timestamp)
                incoming.extend(found);checks.extend(rows);news_records.append(summary)
            except Exception as exc:
                checks.append({'id':'news-api-'+digest(query),'name':'GDELT 공개 뉴스 API','url':'','status':'failed','method':'news_api','checked_at':timestamp,'error':str(exc)[:220]})
                news_records.append({'provider':'GDELT Doc API','query':query,'status':'failed','count':0,'originals_accessed':0,'note':str(exc)[:220]})
    for source in plan['watchlist']:
        if source.get('direct'):
            found, rows, updated = collect_direct(source, fetcher, plan, timestamp, hashes)
            incoming.extend(found)
            checks.extend(rows)
            hashes.update(updated)
    # RSS failure is retained. Recover ONLY inside known sites; never claim global
    # keyword/news search has recovered merely because a known site is accessible.
    recovery_config = plan.get('recovery', {})
    cursor = int(previous.get('recovery_cursor', 0))
    if recovery_config.get('enabled', True):
        failed_watch_ids = {source['id'] for query, language, label, source in tasks if source and any(c['id']=='rss-'+digest(query) and c['status']!='ok' for c in checks)}
        eligible = [s for s in plan['watchlist'] if s['id'] in failed_watch_ids]
        if eligible:
            start = cursor % len(eligible)
            ordered = eligible[start:] + eligible[:start]
            limit = min(len(ordered), int(recovery_config.get('max_groups_per_run', 8)))
            selected = ordered[:limit]
            for source in selected:
                found, rows, summary = collect_recovery(source, fetcher, plan, timestamp, previous['items']+incoming)
                incoming.extend(found)
                checks.extend(rows)
                recovery_records.append(summary)
            recovery_records.extend({'watch_id':s['id'],'name':s['name'],'status':'not_attempted','checks':0,'accessible_links':[],'note':'실행당 예산에 따른 순환 대기. 접근 성공으로 간주하지 않음.'} for s in ordered[limit:])
            cursor += limit
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
    feed = {'version': 1, 'collector_version':'3.0', 'recovery':recovery_records, 'public_news':news_records, 'global_news_search':'Google 뉴스 RSS 실패는 보존. 별도 공개 뉴스 색인 및 알려진 출처 내부의 대체 탐색만 수행', 'generated_at': run_record['finished_at'], 'last_success_at': last_success,
            'timezone': plan['timezone'], 'schedule': plan['schedule'], 'scope': plan['scope'],
            'publisher': 'RWA Radar scheduled collector', 'run': run_record, 'runs': runs,
            'items': items[:plan['collection']['max_items']],
            'retention': f"Public feed shows newest {plan['collection']['max_items']} findings; prior state retains all items.",
            'limitations': ['RSS는 후보 탐색이며 공식 원문 검수가 필요합니다.', 'HTML/JSON-LD만 수집합니다. JS 렌더링·이미지·PDF 연사 명단은 사람이 확인해야 합니다.', '예약 실행은 GitHub 서비스 상황에 따라 지연·누락될 수 있습니다.']}
    atomic_write(ROOT/'public-feed/monitor-feed.json', feed)
    atomic_write(ROOT/'public-feed/monitor-plan.json', plan)
    atomic_write(private, {**previous, 'items': items, 'runs': runs, 'page_hashes': hashes, 'last_success_at': last_success, 'recovery_cursor':cursor})
    (ROOT/'public-feed/index.html').write_text('<!doctype html><meta charset="utf-8"><title>RWA Radar public research feed</title><h1>RWA Radar 공개 리서치 피드</h1><p>개인 섭외 메모는 포함하지 않습니다. 자동 발견은 행사 확정을 뜻하지 않습니다.</p><a href="monitor-feed.json">monitor-feed.json</a>', encoding='utf-8')
    print(json.dumps({'status': status, 'checks': len(checks), 'failed': len(checks)-ok, 'new': run_record['new'], 'updated': run_record['updated']}, ensure_ascii=False))

if __name__ == '__main__':
    run()
