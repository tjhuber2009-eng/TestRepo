import csv, tempfile, unittest
from pathlib import Path
import importlib.util

HERE=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("demo_analyzer",HERE/"demo_analyzer.py")
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

class DemoAnalyzerTests(unittest.TestCase):
    def test_metrics_and_pf(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"COPYTRADER_DEMO_1_DNH_account.csv"
            with p.open("w",newline="") as h:
                w=csv.DictWriter(h,fieldnames=["utc","server_time","login","server","candidate","balance","equity","profit","margin","margin_free","margin_level","positions","orders"])
                w.writeheader()
                w.writerow(dict(utc="2026-09-04 00:00:00",server_time="",login="1",server="Demo",candidate="DNH",balance="1000",equity="1000",profit="0",margin="0",margin_free="1000",margin_level="0",positions="0",orders="0"))
                w.writerow(dict(utc="2026-09-05 00:00:00",server_time="",login="1",server="Demo",candidate="DNH",balance="1020",equity="1010",profit="-10",margin="0",margin_free="1010",margin_level="0",positions="1",orders="0"))
            d=p.with_name("COPYTRADER_DEMO_1_DNH_deals.csv")
            fields=["utc","login","candidate","ticket","order","position_id","symbol","type","type_name","entry","entry_name","volume","price","profit","commission","swap","fee","magic","comment"]
            with d.open("w",newline="") as h:
                w=csv.DictWriter(h,fieldnames=fields); w.writeheader()
                base=dict(utc="",login="1",candidate="DNH",order="",symbol="XAUUSD",type="0",type_name="BUY",entry="",volume=".01",price="",commission="0",swap="0",fee="0",magic="1",comment="")
                w.writerow({**base,"ticket":"1","position_id":"11","entry_name":"IN","profit":"0"})
                w.writerow({**base,"ticket":"2","position_id":"11","entry_name":"OUT","profit":"30"})
                w.writerow({**base,"ticket":"3","position_id":"12","entry_name":"IN","profit":"0"})
                w.writerow({**base,"ticket":"4","position_id":"12","entry_name":"OUT","profit":"-10"})
            r=m.analyze_account(p)
            self.assertEqual(r["closed_positions"],2)
            self.assertEqual(r["wins"],1); self.assertEqual(r["losses"],1)
            self.assertAlmostEqual(r["profit_factor"],3.0)
            self.assertTrue(r["valid_for_clean_comparison"])

    def test_cashflow_invalidates_clean_comparison(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"COPYTRADER_DEMO_1_MON_account.csv"
            fields=["utc","server_time","login","server","candidate","balance","equity","profit","margin","margin_free","margin_level","positions","orders"]
            with p.open("w",newline="") as h:
                w=csv.DictWriter(h,fieldnames=fields); w.writeheader()
                row=dict(utc="2026-09-04",server_time="",login="1",server="Demo",candidate="MON",balance="1000",equity="1000",profit="0",margin="0",margin_free="1000",margin_level="0",positions="0",orders="0")
                w.writerow(row); w.writerow({**row,"utc":"2026-09-05"})
            d=p.with_name("COPYTRADER_DEMO_1_MON_deals.csv")
            fields2=["utc","login","candidate","ticket","order","position_id","symbol","type","type_name","entry","entry_name","volume","price","profit","commission","swap","fee","magic","comment"]
            with d.open("w",newline="") as h:
                w=csv.DictWriter(h,fieldnames=fields2); w.writeheader()
                w.writerow(dict(utc="",login="1",candidate="MON",ticket="1",order="",position_id="",symbol="",type="2",type_name="BALANCE",entry="",entry_name="",volume="0",price="0",profit="100",commission="0",swap="0",fee="0",magic="0",comment="deposit"))
            r=m.analyze_account(p)
            self.assertFalse(r["valid_for_clean_comparison"])
            self.assertEqual(r["cashflow_event_count"],1)

    def test_mql5_is_demo_only_and_read_only(self):
        text=(HERE/"CopyTraderDemoReporter.mq5").read_text(encoding="utf-8")
        self.assertIn("ACCOUNT_TRADE_MODE_DEMO",text)
        for forbidden in ["OrderSend(","CTrade","trade.Buy(","trade.Sell("]:
            self.assertNotIn(forbidden,text)

if __name__=="__main__": unittest.main()
