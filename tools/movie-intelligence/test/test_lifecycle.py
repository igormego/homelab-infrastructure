import unittest
from lifecycle import plan_lifecycle

IDENTITY = {'tmdb': 123, 'imdb': 'tt1234567', 'watched': True}
RADARR = {'tmdbId': 123, 'imdbId': 'tt1234567', 'monitored': False, 'hasFile': False, 'movieFileId': 0, 'sizeOnDisk': 0}
def movie(key, exists=True, accessible=True):
    return {'ratingKey':str(key), 'Guid':[{'id':'tmdb://123'}, {'id':'imdb://tt1234567'}],
            'Media':[{'Part':[{'exists':exists,'accessible':accessible}]}]}
def receipt():
    return {'stage':'verified','identity':{'tmdb':123},'verified':{'rating_key':'1244'}}
def decide(radarr=None, movies=None, archive=None, receipt_data=None, identity=None):
    return plan_lifecycle(identity if identity is not None else IDENTITY,
        radarr if radarr is not None else [RADARR], movies or [], archive or [], receipt_data)

class LifecycleTest(unittest.TestCase):
    def test_archive_candidate(self):
        self.assertEqual(decide()['action'],'plan_archive')
    def test_archive_already_present(self):
        self.assertEqual(decide(archive=[movie(1244)],receipt_data=receipt())['decision'],'archived')
    def test_archive_missing_ownership(self):
        self.assertEqual(decide(archive=[movie(1244)])['decision'],'needs_review')
    def test_monitored_movie_pending(self):
        self.assertEqual(decide(radarr=[{**RADARR,'monitored':True}])['decision'],'transition_pending')
    def test_active_movie(self):
        self.assertEqual(decide(radarr=[{**RADARR,'hasFile':True,'movieFileId':2}],movies=[movie(11)])['decision'],'active')
    def test_verified_real_copy_and_archive_requires_review(self):
        p=decide(radarr=[{**RADARR,'hasFile':True,'movieFileId':2}],movies=[movie(11)],archive=[movie(1244)],receipt_data=receipt())
        self.assertEqual(p['action'],'retire_archive_review')
    def test_missing_plex_canonical_id_blocks(self):
        rows=[{'ratingKey':'11','Guid':[]}]
        self.assertEqual(decide(movies=rows)['decision'],'needs_review')
    def test_unavailable_old_plex_item(self):
        self.assertEqual(decide(movies=[movie(11,False,False)])['action'],'plan_archive')
    def test_unknown_file_availability_blocks(self):
        self.assertEqual(decide(movies=[movie(11,True,False)])['decision'],'needs_review')
    def test_radarr_plex_conflict(self):
        self.assertEqual(decide(movies=[movie(11)])['decision'],'needs_review')
    def test_not_watched(self):
        self.assertEqual(decide(identity={**IDENTITY,'watched':False})['decision'],'not_eligible')
    def test_duplicate_plex_block(self):
        self.assertEqual(decide(movies=[movie(11),movie(12)])['decision'],'needs_review')
    def test_receipt_without_item_block(self):
        self.assertEqual(decide(receipt_data=receipt())['decision'],'needs_review')

if __name__=='__main__':
    unittest.main()
