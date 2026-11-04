import unittest

from probe import Reader, Unavailable, media_versions, episode_candidates, summarize


def media(v, file, exists, bitrate):
    return {"id":v, "bitrate":bitrate, "videoCodec":"hevc",
            "height":2160, "Part":[{"id":v, "file":file, "exists":exists,
                                     "accessible":exists, "size":100000}]}


class FakeReader:
    def __init__(self, data):
        self.data=data
        self.calls=[]
    def get(self, path):
        self.calls.append(path)
        if path not in self.data: raise Unavailable("not_available")
        return self.data[path]


class ProbeTests(unittest.TestCase):
    def setUp(self):
        series=[{"id":9,"title":"Dexter: Resurrection"}]
        eps=[{"id":10,"seriesId":9,"seasonNumber":1,"episodeNumber":3,
              "hasFile":True,"episodeFileId":30,"airDateUtc":"2026-11-01",
              "episodeFile":{"dateAdded":"2026-11-04T19:00:00Z"}}]
        self.sonarr=FakeReader({"/api/v3/series":series,
                                "/api/v3/episode?seriesId=9":eps})
        self.tv={"ratingKey":"88","grandparentTitle":"Dexter: Resurrection",
                 "parentIndex":1,"index":3}
        self.movie={"ratingKey":"99","title":"Carrie","year":1976}
        self.plex=FakeReader({
            "/library/sections/2/all?type=4&includeGuids=1&X-Plex-Container-Start=0&X-Plex-Container-Size=100":
                {"MediaContainer":{"Metadata":[self.tv],"size":1,"totalSize":1,"offset":0}},
            "/library/sections/1/all?type=1&includeGuids=1&X-Plex-Container-Start=0&X-Plex-Container-Size=100":
                {"MediaContainer":{"Metadata":[self.movie],"size":1,"totalSize":1,"offset":0}},
            "/library/metadata/88?checkFiles=1&includeGuids=1":
                {"MediaContainer":{"Metadata":[{"ratingKey":"88","Media":[
                    media(1,"/tv/Dexter.new.hevc.mkv",True,25000),
                    media(2,"/tv/Dexter.old-1080.mkv",False,15000),
                    media(3,"/tv/Dexter.old-720.mkv",False,7000)]}]}},
            "/library/metadata/99?checkFiles=1":
                {"MediaContainer":{"Metadata":[{"ratingKey":"99","Media":[
                    media(4,"/movies/Carrie.1976.mkv",True,14000)]}]}}
        })
        self.bazarr=FakeReader({
            "/api/episodes?episodeid%5B%5D=10": {"data":[{
                "sonarrEpisodeId":10,"sceneName":"Dexter.S01E03.2160p.WEB-DL",
                "missing_subtitles":[{"code2":"sk"},{"code2":"cs"}],
                "subtitles":[{"code2":"en","type":"embedded"}],
                "monitored":True}]}})
    def run_probe(self):
        return summarize(self.sonarr,self.plex,self.bazarr,
                         query="Dexter",movie_title="Carrie")
    def test_three_plex_versions_only_one_readily_available(self):
        p=self.run_probe()
        v=p["episode_candidates"][0]["plex"][0]["files"]
        self.assertEqual(v["reported_plex_parts"],3)
        self.assertEqual(v["distinct_available_file_refs"],1)
        self.assertEqual(v["unavailable_parts"],2)
        self.assertEqual(v["assessment"],"multiple_metadata_entries_one_distinct_available_file")
        self.assertEqual(v["parts"][0]["bitrate_kbps"],25000)
    def test_carrie_bitrate_is_file_metadata_not_a_session(self):
        p=self.run_probe()
        m=p["movie_candidates"][0]["files"]["parts"][0]
        self.assertEqual(m["bitrate_kbps"],14000)
        self.assertEqual(p["playback_bitrate_type"],"not_observed")
    def test_bazarr_language_and_no_provider_search_side_effects(self):
        p=self.run_probe()
        b=p["episode_candidates"][0]["bazarr"]
        self.assertEqual(b["status"],"indexed")
        self.assertEqual({x["code2"] for x in b["missing_subtitles"]},{"sk","cs"})
        self.assertFalse(any("providers/episodes" in x for x in self.bazarr.calls))
    def test_source_unavailable_bazarr_does_not_invent_subtitles(self):
        self.bazarr=FakeReader({})
        p=self.run_probe()
        self.assertEqual(p["episode_candidates"][0]["bazarr"]["status"],"unknown")
    def test_missing_plex_is_not_reported_as_one_physical(self):
        self.plex.data["/library/metadata/88?checkFiles=1&includeGuids=1"]={
            "MediaContainer":{"Metadata":[{"ratingKey":"88","Media":[]}]}}
        p=self.run_probe()
        self.assertEqual(p["episode_candidates"][0]["plex"][0]["files"]["assessment"],"no_media_parts")
    def test_missing_file_evidence_stays_unknown(self):
        v=media_versions({"Media":[{"id":1,"Part":[{"file":"/tv/movie.mkv","exists":None}]}]})
        self.assertEqual(v["assessment"],"file_presence_unverified")
    def test_movie_ambiguity_is_preserved(self):
        self.movie["year"]=2013
        p=self.run_probe()
        self.assertEqual(p["movie_candidates"][0]["year"],2013)
    def test_episode_candidates_with_no_series_match(self):
        self.assertEqual(episode_candidates(self.sonarr,"No Such Show"),[])
    def test_reject_invalid_api_base(self):
        for url in ["http://user:pass@sonarr:8989","file:///etc/passwd","https://example.com/?secret=1"]:
            with self.assertRaises(Unavailable): Reader(url,"token")
    def test_no_mutations_are_issued(self):
        self.run_probe()
        for c in self.sonarr.calls+self.plex.calls+self.bazarr.calls:
            self.assertTrue(c.startswith("/"))
            self.assertFalse(any(x in c.lower() for x in ("command","refresh","providers/episodes")))


if __name__ == "__main__":
    unittest.main()
