import unittest
from archive_batch import plan_archive_batch, execute_archive_batch

A = "a" * 64

class FakeArchive:
    def __init__(self):
        self.calls=[]
        self.plans={}
        self.fail_id=None
    def plan_archive_movie(self, tmdb):
        self.calls.append(("plan",tmdb))
        return self.plans.get(tmdb,{"decision":"eligible","mutation_attempted":False,"plan_fingerprint":A})
    def archive_movie(self, tmdb, *, execute, plan_fingerprint):
        self.calls.append(("execute",tmdb,execute,plan_fingerprint))
        if self.fail_id==tmdb:
            return {"decision":"needs_review","reason":"unconfirmed"}
        return {"decision":"archived","mutation_attempted":True}

class ArchiveBatchTest(unittest.TestCase):
    def setUp(self):
        self.a=FakeArchive()
    def approve(self, ids):
        p=plan_archive_batch(self.a,ids)
        self.assertEqual(p["decision"],"ready")
        return p["batch_fingerprint"]
    def test_read_only_plan(self):
        p=execute_archive_batch(self.a,[1,2],reviewed_batch_fingerprint=None)
        self.assertEqual(p["decision"],"ready")
        self.assertFalse(any(c[0]=="execute" for c in self.a.calls))
    def test_bounded_approved_execution(self):
        fp=self.approve([1,2])
        p=execute_archive_batch(self.a,[1,2],reviewed_batch_fingerprint=fp,execute=True)
        self.assertEqual(p["decision"],"completed")
        self.assertEqual(sum(c[0]=="execute" for c in self.a.calls),2)
    def test_missing_and_stale_fingerprint_block(self):
        p=execute_archive_batch(self.a,[1],reviewed_batch_fingerprint="x"*64,execute=True)
        self.assertEqual(p["decision"],"blocked")
        self.assertFalse(any(c[0]=="execute" for c in self.a.calls))
    def test_unsorted_duplicate_and_over_limit_refuse(self):
        for ids in ([2,1],[1,1],[],list(range(1,12))):
            with self.assertRaises(ValueError):
                plan_archive_batch(self.a,ids,max_movies=10)
    def test_blocked_movie_stops_without_mutation(self):
        self.a.plans[2]={"decision":"blocked","mutation_attempted":False}
        p=plan_archive_batch(self.a,[1,2,3])
        self.assertEqual((p["decision"],p["blocked_tmdb"]),("blocked",2))
        self.assertFalse(any(c[0]=="execute" for c in self.a.calls))
    def test_single_movie_uncertainty_stops_later_movies(self):
        fp=self.approve([1,2,3]); self.a.fail_id=2
        p=execute_archive_batch(self.a,[1,2,3],reviewed_batch_fingerprint=fp,execute=True)
        self.assertEqual((p["decision"],p["blocked_tmdb"]),("stopped",2))
        self.assertFalse(any(c[0]=="execute" and c[1]==3 for c in self.a.calls))
    def test_inflight_freshness_change_blocks_next_action(self):
        fp=self.approve([1])
        self.a.plans[1]={"decision":"blocked","mutation_attempted":False}
        p=execute_archive_batch(self.a,[1],reviewed_batch_fingerprint=fp,execute=True)
        self.assertEqual(p["decision"],"blocked")
    def test_existing_verified_archive_no_new_file(self):
        self.a.plans[1]={"decision":"already_archived","mutation_attempted":False,"verified":{"poster":"verified"}}
        fp=self.approve([1])
        p=execute_archive_batch(self.a,[1],reviewed_batch_fingerprint=fp,execute=True)
        self.assertEqual(p["decision"],"completed")
        self.assertFalse(p["mutation_attempted"])
    def test_exception_after_mutation_is_uncertain(self):
        fp=self.approve([1])
        def raised(*args,**kwargs): raise OSError("connection lost after write")
        self.a.archive_movie=raised
        p=execute_archive_batch(self.a,[1],reviewed_batch_fingerprint=fp,execute=True)
        self.assertEqual(p["decision"],"needs_review")
        self.assertTrue(p["mutation_attempted"])

if __name__=="__main__":
    unittest.main()
