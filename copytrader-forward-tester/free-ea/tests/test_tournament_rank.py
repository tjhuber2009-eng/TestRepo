import importlib.util
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("tournament_rank",ROOT/"tournament_rank.py")
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class TournamentRankTests(unittest.TestCase):
    def base(self,ret=10,pf=1.5,dd=5,trades=20):
        return {
          "candidate":"X","login":"1","server":"Demo",
          "first_utc":"2026-09-01 00:00:00","last_utc":"2026-09-04 00:00:00",
          "equity_return_pct":ret,"profit_factor":pf,"max_observed_equity_dd_pct":dd,
          "closed_positions":trades,"wins":10,"losses":10,
          "valid_for_clean_comparison":True,"cashflow_event_count":0
        }

    def test_small_sample_is_not_excluded(self):
        r=m.score_result(self.base(trades=1))
        self.assertIsNotNone(r["prospective_score"])
        self.assertIn(r["tournament_status"],{"LOW_EVIDENCE","ACTIVE"})

    def test_better_return_improves_score_all_else_equal(self):
        a=m.score_result(self.base(ret=5))
        b=m.score_result(self.base(ret=20))
        self.assertGreater(b["prospective_score"],a["prospective_score"])

    def test_better_pf_improves_score_all_else_equal(self):
        a=m.score_result(self.base(pf=1.1))
        b=m.score_result(self.base(pf=2.0))
        self.assertGreater(b["prospective_score"],a["prospective_score"])

    def test_cashflow_contamination_fails_closed(self):
        x=self.base()
        x["valid_for_clean_comparison"]=False
        r=m.score_result(x)
        self.assertEqual(r["tournament_status"],"CONTAMINATED")
        self.assertIsNone(r["prospective_score"])

if __name__=="__main__":
    unittest.main()
