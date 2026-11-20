from utils.decisions_store import DecisionStore, effective_status


def test_each_email_decides_alone(tmp_path):
    d = DecisionStore(str(tmp_path / "m.db"))
    d.record("al1", "a@x.com", "APPROVED"); d.record("al1", "b@x.com", "REJECTED")
    assert (d.get("al1", "a@x.com"), d.get("al1", "b@x.com"), d.get("al1", "c@x.com")) == ("APPROVED", "REJECTED", None)

def test_effective_status():
    assert effective_status({"status": "EXPIRED"}, "APPROVED") == "EXPIRED"
    assert effective_status({"status": "INFO"}, None) == "INFO"
    assert effective_status({"status": "APPROVED"}, None) == "PENDING"   # the owner's decision is not yours
    assert effective_status({"status": "PENDING"}, "REJECTED") == "REJECTED"

def test_seed_owner_is_idempotent(tmp_path):
    d = DecisionStore(str(tmp_path / "m.db"))
    alerts = [{"alertId": "1", "status": "APPROVED"}, {"alertId": "2", "status": "PENDING"}]
    assert d.seed_owner(alerts, "o@x.com") == 1 and d.seed_owner(alerts, "o@x.com") == 0
