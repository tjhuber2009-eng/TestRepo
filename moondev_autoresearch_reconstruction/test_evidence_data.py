import csv
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from v4.prepare_evidence_data import (
    cboe_vix_rows_from_csv,
    tiingo_adjusted_open_diagnostics,
    tiingo_rows_from_prices,
)
from v4.reconcile_evidence_data import compare_return_files


class EvidenceDataTests(unittest.TestCase):
    def test_tiingo_raw_rows_use_future_splits_and_explicit_dividend(self):
        prices = [
            {"date":"2020-01-01T00:00:00.000Z","open":100,"high":101,"low":99,"close":100,"volume":1000,"divCash":0,"splitFactor":1,"adjOpen":50},
            {"date":"2020-01-02T00:00:00.000Z","open":102,"high":103,"low":101,"close":102,"volume":1200,"divCash":1,"splitFactor":1,"adjOpen":51.5},
            {"date":"2020-01-03T00:00:00.000Z","open":51,"high":52,"low":50,"close":51,"volume":2500,"divCash":0,"splitFactor":2,"adjOpen":51.5},
        ]
        rows = tiingo_rows_from_prices(prices)
        self.assertAlmostEqual(rows[0][1], 50.0)
        self.assertAlmostEqual(rows[1][4], 51.0)
        self.assertAlmostEqual(rows[2][1], 51.0)
        self.assertAlmostEqual(rows[0][5], 2000.0)
        self.assertAlmostEqual(rows[1][6], 0.5)

    def test_tiingo_adjusted_open_is_diagnostic_only_and_matches_total_return(self):
        prices = [
            {"date":"2020-01-01T00:00:00Z","open":100,"high":101,"low":99,"close":100,"volume":1000,"divCash":0,"splitFactor":1,"adjOpen":50},
            {"date":"2020-01-02T00:00:00Z","open":102,"high":103,"low":101,"close":102,"volume":1200,"divCash":1,"splitFactor":1,"adjOpen":51.5},
            {"date":"2020-01-03T00:00:00Z","open":51,"high":52,"low":50,"close":51,"volume":2500,"divCash":0,"splitFactor":2,"adjOpen":51.5},
        ]
        diag = tiingo_adjusted_open_diagnostics(prices)
        self.assertTrue(diag["parity_pass"])
        self.assertLess(diag["max_abs_diff_bp"], 1e-9)

    def test_cboe_parser_accepts_official_style_ohlc_csv(self):
        blob = b"DATE,OPEN,HIGH,LOW,CLOSE\n01/02/2020,13.0,14.0,12.0,13.5\n01/03/2020,14.0,15.0,13.0,14.5\n"
        rows = cboe_vix_rows_from_csv(
            blob,
            datetime(2020,1,1,tzinfo=timezone.utc),
            datetime(2020,1,31,tzinfo=timezone.utc),
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][1:5], [13.0,14.0,12.0,13.5])

    def test_return_reconciliation_ignores_nominal_share_basis(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            a = root / "a.csv"
            b = root / "b.csv"
            a.write_text(
                "Date,Open,High,Low,Close,Volume,Dividend\n"
                "2020-01-01,50,50,50,50,0,0\n"
                "2020-01-02,51,51,51,51,0,0.5\n"
                "2020-01-03,51.5,51.5,51.5,51.5,0,0\n"
                "2020-01-06,52,52,52,52,0,0\n"
                "2020-01-07,52.5,52.5,52.5,52.5,0,0\n"
                "2020-01-08,53,53,53,53,0,0\n"
                "2020-01-09,53.5,53.5,53.5,53.5,0,0\n"
                "2020-01-10,54,54,54,54,0,0\n"
                "2020-01-13,54.5,54.5,54.5,54.5,0,0\n"
                "2020-01-14,55,55,55,55,0,0\n"
                "2020-01-15,55.5,55.5,55.5,55.5,0,0\n"
                "2020-01-16,56,56,56,56,0,0\n"
                "2020-01-17,56.5,56.5,56.5,56.5,0,0\n"
                "2020-01-20,57,57,57,57,0,0\n"
                "2020-01-21,57.5,57.5,57.5,57.5,0,0\n"
                "2020-01-22,58,58,58,58,0,0\n"
                "2020-01-23,58.5,58.5,58.5,58.5,0,0\n"
                "2020-01-24,59,59,59,59,0,0\n"
                "2020-01-27,59.5,59.5,59.5,59.5,0,0\n"
                "2020-01-28,60,60,60,60,0,0\n"
                "2020-01-29,60.5,60.5,60.5,60.5,0,0\n",
                encoding="utf-8",
            )
            with a.open(encoding="utf-8") as source:
                rows = list(csv.DictReader(source))
            with b.open("w", newline="", encoding="utf-8") as handle:
                w = csv.DictWriter(handle, fieldnames=rows[0].keys())
                w.writeheader()
                for row in rows:
                    for key in ("Open","High","Low","Close","Dividend"):
                        row[key] = str(float(row[key]) * 2.0)
                    w.writerow(row)
            comp = compare_return_files(a,b)
            self.assertTrue(comp["cross_source_pass"])
            self.assertLess(comp["max_abs_diff_bp"], 1e-9)


    def test_cross_source_shadow_ignores_dividend_basis(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            a = root / "a.csv"
            b = root / "b.csv"
            header = "Date,Open,High,Low,Close,Volume,Dividend"
            rows_a = [header]
            rows_b = [header]
            for i in range(25):
                day = i + 1
                px = 100.0 + i
                div_a = 10.0 if i == 8 else 0.0
                div_b = 0.01 if i == 8 else 0.0
                rows_a.append(f"2020-02-{day:02d},{px},{px},{px},{px},0,{div_a}")
                rows_b.append(f"2020-02-{day:02d},{px},{px},{px},{px},0,{div_b}")
            a.write_text("\n".join(rows_a) + "\n", encoding="utf-8")
            b.write_text("\n".join(rows_b) + "\n", encoding="utf-8")
            comp = compare_return_files(a, b)
            self.assertTrue(comp["cross_source_pass"])
            self.assertLess(comp["max_abs_diff_bp"], 1e-9)



    def test_material_gate_rejects_double_split_history(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            primary = root / "primary.csv"
            reference = root / "reference.csv"
            header = "Date,Open,High,Low,Close,Volume,Dividend"
            a = [header]
            b = [header]
            for i in range(30):
                day = i + 1
                ref_px = 100.0 + i
                # Mimic a provider that applies an extra 2:1 adjustment to all
                # history before a split date, creating a false jump.
                pri_px = ref_px / 2.0 if i < 15 else ref_px
                a.append(f"2020-03-{day:02d},{pri_px},{pri_px},{pri_px},{pri_px},0,0")
                b.append(f"2020-03-{day:02d},{ref_px},{ref_px},{ref_px},{ref_px},0,0")
            primary.write_text("\n".join(a) + "\n", encoding="utf-8")
            reference.write_text("\n".join(b) + "\n", encoding="utf-8")
            comp = compare_return_files(primary, reference)
            self.assertFalse(comp["cross_source_pass"])
            self.assertGreater(comp["fraction_over_50bp"], 0.005)



if __name__ == "__main__":
    unittest.main()
