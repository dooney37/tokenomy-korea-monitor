import json
import unittest
from pathlib import Path
from unittest.mock import patch
import collector as c

PLAN = json.loads((Path(__file__).resolve().parents[1]/'data/monitor-plan.json').read_text(encoding='utf-8'))
SOURCE = PLAN['watchlist'][0]
TIME = '2026-11-05T00:00:00+00:00'

class CollectorTests(unittest.TestCase):
    def event(self, **kw):
        return {'@type': 'Event', 'name': 'Digital Assets Asia 2027', 'startDate': '2027-04-10', 'endDate': '2027-04-11', 'description': 'Bank and institutional tokenization conference', 'location': {'address': {'addressCountry': 'HK'}}, **kw}

    def item(self, **kw):
        return c.structured_item(self.event(**kw), SOURCE, 'https://example.com/event', PLAN, TIME)

    def test_valid_structured_event(self):
        item = self.item()
        self.assertFalse(item['review_required'])
        self.assertEqual(item['scope'], 'in_scope')
        self.assertEqual(item['event_date'], '2027-04-10')

    def test_past_year_not_2027(self):
        item = self.item(startDate='2026-04-10')
        self.assertTrue(item['review_required'])
        self.assertEqual(item['scope'], 'outside')

    def test_missing_dates_or_place_requires_review(self):
        self.assertTrue(self.item(startDate='', location={})['review_required'])

    def test_cancellation_requires_review(self):
        self.assertTrue(self.item(eventStatus='https://schema.org/EventCancelled')['review_required'])

    def test_event_month_not_inferred(self):
        self.assertEqual(self.item(startDate='TBA')['event_date'], '')

    def test_broad_web3_needs_financial_relevance(self):
        self.assertTrue(self.item(description='NFT art and gaming')['review_required'])

    def test_risk_weighted_assets_exclusion(self):
        self.assertTrue(c.excluded('RWA risk-weighted assets regulation', PLAN))
        self.assertFalse(c.excluded('RWA tokenization bank summit', PLAN))

    def test_nested_jsonld_and_script_excluded(self):
        p = c.EventHTML()
        p.feed('<title>Test</title><script>hidden</script><script type="application/ld+json">'+json.dumps({'@graph':[self.event()]})+'</script><p>2027 public</p>')
        self.assertEqual(len(list(c.walk_events(p.json_scripts))), 1)
        self.assertNotIn('hidden', ' '.join(p.text))

    def test_changed_timestamp_is_not_news(self):
        first, changes = c.merge_items([], [self.item()], TIME)
        self.assertEqual(len(changes), 1)
        again = dict(self.item(), observed_at='2026-11-05T08:00:00+00:00')
        second, changes = c.merge_items(first, [again], '2026-11-05T08:00:00+00:00')
        self.assertEqual(changes, [])
        self.assertEqual(second[0]['first_seen_at'], TIME)

    def test_failed_source_does_not_delete_old_records(self):
        first, _ = c.merge_items([], [self.item()], TIME)
        second, changes = c.merge_items(first, [], '2026-11-06T00:00:00+00:00')
        self.assertEqual(len(second), 1)
        self.assertEqual(changes, [])

    def test_speaker_requires_person_and_incomplete_affiliation_is_reviewed(self):
        item = self.item(performer=[{'@type':'Person','name':'Test Person'}])
        self.assertEqual(item['kind'], '스피커 공개')
        self.assertTrue(item['review_required'])
        self.assertEqual(item['speakers'][0]['company'], '')

    def test_organization_is_not_a_speaker(self):
        item = self.item(performer=[{'@type':'Organization','name':'Test Bank'}])
        self.assertEqual(item['speakers'], [])
        self.assertEqual(item['kind'], '개최 공지')

    def test_schedule_and_ids(self):
        self.assertEqual(PLAN['schedule'], ['08:30', '17:00'])
        self.assertEqual(len({x['id'] for x in PLAN['watchlist']}), len(PLAN['watchlist']))

    def test_rss_does_not_use_publication_as_event_date(self):
        class Fake:
            def get(self, url):
                return '<rss><channel><item><title>Bank RWA 2027 speakers</title><link>https://example.com/news</link><pubDate>2026-11-05</pubDate><source url="https://example.com">Publisher</source></item></channel></rss>', 'application/xml', url
        items, check = c.collect_rss('test', 'en', 'test', SOURCE, Fake(), PLAN, TIME)
        self.assertTrue(items[0]['review_required'])
        self.assertEqual(items[0]['event_date'], '')
        self.assertEqual(check['status'], 'ok')

class RecoveryTests(unittest.TestCase):
    def source(self):
        return {**SOURCE, 'url':'https://example.com/events'}

    def plan(self):
        value = json.loads(json.dumps(PLAN))
        value['recovery'] = {'enabled':True,'max_requests_per_group':7,'max_matches_per_group':3,'max_groups_per_run':1}
        return value

    def test_same_site_scope_does_not_expand(self):
        self.assertTrue(c.allowed_same_site('https://www.example.com/a','https://example.com'))
        self.assertFalse(c.allowed_same_site('https://example.com.evil.test/a','https://example.com'))
        self.assertFalse(c.allowed_same_site('https://other.example/a','https://example.com'))
        self.assertFalse(c.allowed_same_site('javascript:alert(1)','https://example.com'))

    def test_sitemap_namespaces_and_feed_links(self):
        index, urls = c.xml_locations('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://example.com/2027/forum</loc></url></urlset>')
        self.assertFalse(index)
        self.assertEqual(urls,['https://example.com/2027/forum'])
        self.assertEqual(c.feed_links('<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>2027 Forum</title><link href="https://example.com/2027/forum"/></entry></feed>')[0][1],'2027 Forum')

    def test_public_feed_discovery(self):
        page=c.EventHTML()
        page.feed('<link rel="alternate" type="application/rss+xml" href="/feed.xml">')
        self.assertEqual(page.feeds,['/feed.xml'])

    def test_title_is_only_a_search_lead(self):
        self.assertGreater(c.candidate_link_score('https://example.com/events/a','Digital Assets Asia 2027 speakers',SOURCE),0)
        self.assertEqual(c.candidate_link_score('https://example.com/contact','Contact',SOURCE),0)

    def test_recovery_checks_accessible_page_and_keeps_review_required(self):
        event={'@type':'Event','name':'Digital Assets Asia 2027','startDate':'2027-04-10','description':'Bank tokenization forum','location':{'address':{'addressCountry':'HK'}}}
        class Fake:
            robots={}
            def get(self,url):
                pages={
                  'https://example.com/events':'<title>Events</title><a href="/2027/forum">Digital Assets Asia 2027</a><link rel="alternate" type="application/rss+xml" href="/feed.xml">',
                  'https://example.com/':'<title>Home</title>',
                  'https://example.com/feed.xml':'<rss><channel><item><title>Digital Assets Asia 2027</title><link>https://example.com/2027/forum</link></item></channel></rss>',
                  'https://example.com/sitemap.xml':'<urlset><url><loc>https://example.com/2027/forum</loc></url></urlset>',
                  'https://example.com/2027/forum':'<title>Digital Assets Asia 2027</title><p>2027 Bank tokenization</p><script type="application/ld+json">'+json.dumps(event)+'</script>'}
                return pages[url], ('application/xml' if url.endswith('.xml') else 'text/html'), url
        items,checks,summary=c.collect_recovery(self.source(),Fake(),self.plan(),TIME,[])
        self.assertEqual(summary['status'],'found')
        self.assertTrue(any(x['url']=='https://example.com/2027/forum' for x in summary['accessible_links']))
        self.assertTrue(all(x['review_required'] for x in items))
        self.assertTrue(any(x['event_date']=='2027-04-10' for x in items))
        self.assertLessEqual(len(checks),7)

    def test_blocked_recovery_never_claims_success(self):
        class Blocked:
            robots={}
            def get(self,url):
                raise ValueError('robots.txt disallows this URL')
        items,checks,summary=c.collect_recovery(self.source(),Blocked(),self.plan(),TIME,[])
        self.assertEqual(items,[])
        self.assertEqual(summary['status'],'failed')
        self.assertEqual(summary['accessible_links'],[])
        self.assertTrue(all(x['status']=='failed' for x in checks))

    def test_recovery_rejects_redirect_outside_known_host(self):
        class Other:
            robots={}
            def get(self,url):
                return '<title>2027</title>','text/html','https://other.test/2027'
        items,checks,summary=c.collect_recovery(self.source(),Other(),self.plan(),TIME,[])
        self.assertEqual(items,[])
        self.assertEqual(summary['status'],'failed')

    def test_no_2027_match_not_reported_as_event(self):
        class Old:
            robots={}
            def get(self,url):
                return '<title>2024 Old event</title><p>2024</p>','text/html',url
        items,checks,summary=c.collect_recovery(self.source(),Old(),self.plan(),TIME,[])
        self.assertEqual(items,[])
        self.assertEqual(summary['status'],'checked_no_match')

    def test_robot_discovery_failure_cached_without_bypass(self):
        fetcher=c.Fetcher(PLAN['collection'])
        with patch.object(fetcher,'raw',side_effect=ValueError('robots unavailable')) as raw:
            for url in ['https://example.com/a','https://example.com/b']:
                with self.assertRaises(ValueError):
                    fetcher.get(url)
            self.assertEqual(raw.call_count,1)

    def test_run_preserves_old_state_and_advances_round_robin(self):
        import tempfile
        import concurrent.futures
        class Immediate:
            def __init__(self,**kw): pass
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def submit(self,fn,*args):
                result=concurrent.futures.Future()
                try: result.set_result(fn(*args))
                except Exception as exc: result.set_exception(exc)
                return result
        class PublicOnly:
            def __init__(self,config): self.robots={}
            def get(self,url):
                if 'news.google.com' in url: raise ValueError('robots.txt disallows this URL')
                if url.endswith('sitemap.xml'): return '<urlset/>','application/xml',url
                return '<title>Digital Assets Asia 2027</title><p>2027</p>','text/html',url
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'data').mkdir();(root/'.monitor-state').mkdir()
            plan=self.plan();plan['keyword_groups']=[];plan['public_news_api']={'enabled':False};plan['watchlist']=[self.source()];plan['watchlist'][0]['direct']=False
            (root/'data/monitor-plan.json').write_text(json.dumps(plan),encoding='utf-8')
            old={'id':'keep-id','revision':'old','title':'Keep original','url':'https://example.com/old','changed_at':TIME}
            (root/'.monitor-state/state.json').write_text(json.dumps({'items':[old],'runs':[],'page_hashes':{}}),encoding='utf-8')
            with patch.object(c,'ROOT',root),patch.object(c,'Fetcher',PublicOnly),patch.object(c.concurrent.futures,'ThreadPoolExecutor',Immediate):
                c.run()
            state=json.loads((root/'.monitor-state/state.json').read_text())
            feed=json.loads((root/'public-feed/monitor-feed.json').read_text())
            self.assertTrue(any(x['id']=='keep-id' for x in state['items']))
            self.assertEqual(state['recovery_cursor'],1)
            self.assertEqual(feed['collector_version'],'2.0')
            self.assertEqual(feed['recovery'][0]['status'],'found')
            self.assertEqual(feed['run']['status'],'partial')

class PublicNewsTests(unittest.TestCase):
    def test_empty_public_api_does_not_invent_articles(self):
        class Empty:
            def get(self,url): return '{}','application/json',url
        items,checks,summary=c.collect_public_news('2027 RWA',Empty(),PLAN,TIME)
        self.assertEqual(items,[])
        self.assertEqual(summary['count'],0)
        self.assertEqual(checks[0]['status'],'ok')

    def test_api_candidate_checks_original_but_keeps_event_date_unknown(self):
        class Accessible:
            def get(self,url):
                if 'api.gdeltproject.org' in url:
                    return json.dumps({'articles':[{'title':'Bank tokenization forum 2027','url':'https://example.com/article','domain':'example.com','seendate':'20261106T000000Z'}]}),'application/json',url
                return '<title>Bank tokenization forum 2027</title><p>2027 institutional forum</p>','text/html',url
        items,checks,summary=c.collect_public_news('2027 RWA',Accessible(),PLAN,TIME)
        self.assertEqual(len(items),1)
        self.assertTrue(items[0]['original_access'])
        self.assertTrue(items[0]['review_required'])
        self.assertEqual(items[0]['event_date'],'')

    def test_title_requery_requires_actual_matching_mirror(self):
        title='Bank tokenization forum 2027'
        class Mirror:
            def get(self,url):
                if 'api.gdeltproject.org' in url:
                    return json.dumps({'articles':[{'title':title,'url':'https://mirror.example/report'}]}),'application/json',url
                return '<title>'+title+'</title>','text/html',url
        with patch.object(c.time,'sleep'):
            links,checks=c.recover_title_links(title,'https://blocked.example/article',Mirror(),TIME)
        self.assertEqual(links[0]['url'],'https://mirror.example/report')
        self.assertEqual(links[0]['status'],'title_match_accessible')
        self.assertTrue(any(x['method']=='title_mirror' and x['status']=='ok' for x in checks))

    def test_accessible_wrong_title_is_not_accepted_as_mirror(self):
        class Wrong:
            def get(self,url):
                if 'api.gdeltproject.org' in url:
                    return json.dumps({'articles':[{'title':'Bank tokenization forum 2027','url':'https://mirror.example/report'}]}),'application/json',url
                return '<title>Unrelated login page</title>','text/html',url
        with patch.object(c.time,'sleep'):
            links,checks=c.recover_title_links('Bank tokenization forum 2027','https://blocked.example/article',Wrong(),TIME)
        self.assertEqual(links,[])
        self.assertTrue(any(x['status']=='failed' for x in checks))

    def test_api_error_is_not_a_successful_empty_search(self):
        class Error:
            def get(self,url): return '{"error":"rate limited"}','application/json',url
        with self.assertRaises(ValueError):
            c.collect_public_news('2027 RWA',Error(),PLAN,TIME)

    def test_original_block_remains_visible_even_when_other_title_url_found(self):
        title='Bank tokenization forum 2027'
        class Blocked:
            def get(self,url):
                if 'api.gdeltproject.org' in url:
                    if 'query=%22' in url:
                        return json.dumps({'articles':[{'title':title,'url':'https://mirror.example/report'}]}),'application/json',url
                    return json.dumps({'articles':[{'title':title,'url':'https://blocked.example/article'}]}),'application/json',url
                if 'blocked.example' in url: raise ValueError('robots.txt disallows this URL')
                return '<title>'+title+'</title>','text/html',url
        with patch.object(c.time,'sleep'):
            items,checks,summary=c.collect_public_news('2027 RWA',Blocked(),PLAN,TIME)
        self.assertFalse(items[0]['original_access'])
        self.assertEqual(len(items[0]['alternate_links']),1)
        self.assertTrue(items[0]['review_required'])
        self.assertTrue(any(x['method']=='news_origin' and x['status']=='failed' for x in checks))

if __name__ == '__main__':
    unittest.main()
