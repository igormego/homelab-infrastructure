import unittest
from datetime import datetime, timezone

from maintainerr_audit import EvidenceUnavailable, summarize_membership, inspect


class Fake:
    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []
    def get(self, path):
        self.calls.append(path)
        if path not in self.mapping:
            raise EvidenceUnavailable('no_fixture')
        return self.mapping[path]


def plex_mapping():
    return {'/library/sections/2/all?type=2&includeGuids=1&X-Plex-Container-Start=0&X-Plex-Container-Size=100':
        {'MediaContainer': {'Metadata': [{'ratingKey':'555','title':'Lanterns','type':'show'}],
                            'totalSize':1,'size':1,'offset':0}}}


def member(**kwargs):
    base = {'mediaServerId':'555','addDate':'2026-11-15T12:00:00Z',
            'includedByRule':True,'ruleEvaluationFailed':False,
            'manualMembershipSource':None}
    return {**base,**kwargs}


def group(**kwargs):
    base = {'id':9,'name':'Old series cleanup','ruleGroupId':4,
            'deleteAfterDays':10,'media':[member()]}
    return {**base,**kwargs}


class MaintainerrAuditTests(unittest.TestCase):
    def test_countdown_is_membership_age_not_watch_age(self):
        r = summarize_membership({'ratingKey':'555'},[group()])
        self.assertEqual(len(r),1)
        self.assertEqual(r[0]['delete_after_days'],10)
        self.assertEqual(r[0]['handler_due_utc'],'2026-11-25T12:00:00+00:00')
        self.assertTrue(r[0]['included_by_rule'])

    def test_null_delete_window_is_due_immediately(self):
        r = summarize_membership({'ratingKey':'555'},[group(deleteAfterDays=None)])
        self.assertIn('due immediately',r[0]['timer_note'])

    def test_not_in_collection_is_not_false_positive(self):
        r = summarize_membership({'ratingKey':'555'},[group(media=[member(mediaServerId='999')])])
        self.assertEqual(r,[])

    def test_multiple_collection_memberships_remain_distinct(self):
        r = summarize_membership({'ratingKey':'555'},[group(),group(id=10,name='Second')])
        self.assertEqual({x['collection_id'] for x in r},{9,10})

    def test_unknown_countdown_fails_closed(self):
        with self.assertRaises(EvidenceUnavailable):
            summarize_membership({'ratingKey':'555'},[group(deleteAfterDays=-3)])

    def test_missing_membership_data_fails_closed(self):
        with self.assertRaises(EvidenceUnavailable):
            summarize_membership({'ratingKey':'555'},[{'id':9,'name':'Incomplete'}])

    def test_existing_rule_identity_reads_only_nonsecret_subroutes(self):
        maintainerr=Fake({
            '/api/collections/overlay-data':[group()],
            '/api/collections/collection/9':
                {'id':9,'arrAction':5,'isActive':True,'typeId':'show'},
            '/api/rules/4/rules':[
                {'section':1,'isActive':True,
                 'ruleJson':'{"firstVal":"Plex.lastViewedAt","operator":0,"action":"NOT_EXISTS","customVal":"secret"}'}
            ]
        })
        plex=Fake(plex_mapping())
        report=inspect(maintainerr,plex)
        self.assertEqual(report['show']['plex_rating_key'],'555')
        self.assertEqual(report['matching_collections'][0]['action_setting'],5)
        self.assertEqual(report['matching_collections'][0]['rules'][0]['first_value'],
                         'Plex.lastViewedAt')
        self.assertNotIn('secret',str(report))
        self.assertFalse(report['write_performed'])
        self.assertFalse(any(x.startswith('/api/rules?') or x=='/api/rules' or
                             'deactivate' in x or 'handle' in x for x in maintainerr.calls))

    def test_unknown_rule_identity_does_not_query_secrets(self):
        maintainerr=Fake({
            '/api/collections/overlay-data':[group(ruleGroupId=None)],
            '/api/collections/collection/9':{'id':9,'arrAction':0}
        })
        report=inspect(maintainerr,Fake(plex_mapping()))
        self.assertEqual(report['matching_collections'][0]['rules'],
                         'rule_group_identity_unavailable')
        self.assertEqual(len(maintainerr.calls),2)

    def test_ambiguous_plex_title_stops(self):
        mapping=plex_mapping()
        key=next(iter(mapping))
        mapping[key]['MediaContainer']['Metadata'].append({'ratingKey':'777','title':'Lanterns'})
        mapping[key]['MediaContainer']['size']=2
        mapping[key]['MediaContainer']['totalSize']=2
        with self.assertRaises(EvidenceUnavailable):
            inspect(Fake({}),Fake(mapping))


if __name__ == '__main__':
    unittest.main()
