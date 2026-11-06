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

if __name__ == '__main__':
    unittest.main()
