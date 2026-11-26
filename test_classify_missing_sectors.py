import unittest
from unittest.mock import Mock

from classify_missing_sectors import (
    SourceError,
    apply_classifications,
    build_audit,
    fetch_hose_classifications,
)


class SectorClassifierTests(unittest.TestCase):
    def test_fetches_sector_members_by_parent_id(self):
        session = Mock()
        session.get.side_effect = [
            Mock(status_code=200, json=lambda: {
                "success": True,
                "data": {"list": [{"id": 138, "name": "Financials"}]},
            }),
            Mock(status_code=200, json=lambda: {
                "success": True,
                "data": [
                    {"code": "VCB", "name": "Banks", "parentName": "Financials"},
                    {"code": "SSI", "name": "Financial Services", "parentName": "Financials"},
                ],
            }),
        ]
        result = fetch_hose_classifications(session, pause=0)
        self.assertEqual(result, {"VCB": "Financials", "SSI": "Financials"})
        self.assertEqual(session.get.call_count, 2)
        self.assertEqual(session.get.call_args_list[1].kwargs["params"]["parentId"], 138)

    def test_rejects_unsuccessful_source_response(self):
        session = Mock()
        session.get.return_value = Mock(json=lambda: {"success": False, "data": None})
        with self.assertRaises(SourceError):
            fetch_hose_classifications(session, pause=0)

    def test_audit_only_matches_hose_and_keeps_other_exchanges_unresolved(self):
        universe = {"stocks": [
            {"t": "VCB", "x": "HOSE", "i": ""},
            {"t": "AAA", "x": "HNX", "i": ""},
            {"t": "XYZ", "x": "UPCOM", "i": ""},
            {"t": "HNX1", "x": "HNX", "i": "Manufacture"},
        ]}
        audit, count = build_audit(universe, {"VCB": "Financials", "AAA": "wrong-market"})
        self.assertEqual(count, 1)
        self.assertEqual(audit["summary"]["unresolved_by_exchange"], {"HNX": 1, "UPCOM": 1})
        self.assertEqual(audit["matched"], [{"ticker": "VCB", "exchange": "HOSE", "industry": "Financials"}])

    def test_apply_fills_only_blank_hose_entries(self):
        universe = {"stocks": [
            {"t": "VCB", "x": "HOSE", "i": ""},
            {"t": "SSI", "x": "HOSE", "i": "existing"},
            {"t": "AAA", "x": "HNX", "i": ""},
        ]}
        count = apply_classifications(universe, {"VCB": "Financials", "SSI": "Financials", "AAA": "wrong-market"})
        self.assertEqual(count, 1)
        self.assertEqual(universe["stocks"][0]["i"], "Financials")
        self.assertEqual(universe["stocks"][1]["i"], "existing")
        self.assertEqual(universe["stocks"][2]["i"], "")


if __name__ == "__main__":
    unittest.main()
