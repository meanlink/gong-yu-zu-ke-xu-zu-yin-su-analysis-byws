import os
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

from fastapi.testclient import TestClient

from src.db import execute, one, query
from src.main import app


DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://rental_app:rental_pass@db:5432/rental_ops")


def _db_ready():
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=2):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_ready(), reason="需要 PostgreSQL 才能运行导入测试")


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def auth(client):
    tokens = {}
    for email in ("analyst@harborstay.local", "manager@harborstay.local"):
        response = client.post("/auth/login", json={"email": email, "password": "rental123"})
        tokens[email] = {"Authorization": f"Bearer {response.json()['access_token']}"}
    return tokens


@pytest.fixture()
def tenant():
    suffix = uuid.uuid4().hex[:8]
    property_id = one(
        """INSERT INTO properties(code,name,neighborhood,monthly_rent,floor,property_age_years)
           VALUES(%s,%s,'测试片区',5000,5,2.0) RETURNING id""",
        (f"T-{suffix}", f"测试房源 {suffix}"))["id"]
    tenant_id = one(
        """INSERT INTO tenants(full_name,email,property_id,move_in_date,lease_end_date,status)
           VALUES('测试租客',%s,%s,CURRENT_DATE,CURRENT_DATE + INTERVAL '200 days','active') RETURNING id""",
        (f"t-{suffix}@example.local", property_id))["id"]
    yield tenant_id
    execute("DELETE FROM renewal_survey_revision WHERE tenant_id=%s", (tenant_id,))
    execute("DELETE FROM follow_up_tasks WHERE tenant_id=%s", (tenant_id,))
    execute("DELETE FROM survey_import_sheet WHERE tenant_id=%s", (tenant_id,))
    execute("DELETE FROM survey_conflict WHERE tenant_id=%s", (tenant_id,))
    execute("DELETE FROM renewal_surveys WHERE tenant_id=%s", (tenant_id,))
    execute("DELETE FROM tenants WHERE id=%s", (tenant_id,))
    execute("DELETE FROM properties WHERE id=%s", (property_id,))


def sheet(tenant_id, ref, intent, comments="", period="2026-2027"):
    return {
        "tenant_id": tenant_id, "lease_period": period, "sheet_ref": ref,
        "cleanliness_score": 3.5, "service_score": 3.5, "value_score": 3.5,
        "community_score": 3.5, "renewal_intent": intent, "comments": comments,
    }


def import_sheets(client, auth, key, sheets):
    return client.post("/survey-imports", headers=auth["analyst@harborstay.local"],
                       json={"idempotency_key": key, "source": "纸质问卷补录", "sheets": sheets})


def open_conflict(client, auth, tenant_id):
    rows = client.get("/survey-conflicts", headers=auth["analyst@harborstay.local"]).json()["open"]
    return next(row for row in rows if row["tenant_id"] == tenant_id)


def test_conflict_free_sheet_is_sampled(client, auth, tenant):
    response = import_sheets(client, auth, f"key-{uuid.uuid4().hex}", [sheet(tenant, "S-1", 6)])
    assert response.status_code == 200, response.text
    assert response.json()["batch"]["accepted_count"] == 1
    samples = [row for row in client.get("/surveys", headers=auth["analyst@harborstay.local"]).json()
               if row["tenant_id"] == tenant]
    assert len(samples) == 1 and samples[0]["origin"] == "paper_import"


def test_conflicting_group_is_staged_and_original_stays(client, auth, tenant):
    execute("""INSERT INTO renewal_surveys(tenant_id,lease_period,collected_at,cleanliness_score,service_score,
               value_score,community_score,renewal_intent,comments) VALUES(%s,'2026-2027',NOW(),4,4,4,4,8,'原件')""",
            (tenant,))
    response = import_sheets(client, auth, f"key-{uuid.uuid4().hex}",
                             [sheet(tenant, "S-2", 3), sheet(tenant, "S-3", 3)])
    assert response.status_code == 200, response.text
    assert response.json()["batch"]["staged_count"] == 2
    assert response.json()["batch"]["conflict_count"] == 1
    samples = [row for row in client.get("/surveys", headers=auth["analyst@harborstay.local"]).json()
               if row["tenant_id"] == tenant]
    assert len(samples) == 1 and float(samples[0]["renewal_intent"]) == 8.0


def test_retry_does_not_duplicate_samples_or_conflicts(client, auth, tenant):
    execute("""INSERT INTO renewal_surveys(tenant_id,lease_period,collected_at,cleanliness_score,service_score,
               value_score,community_score,renewal_intent,comments) VALUES(%s,'2026-2027',NOW(),4,4,4,4,8,'原件')""",
            (tenant,))
    key = f"key-{uuid.uuid4().hex}"
    sheets = [sheet(tenant, "S-4", 3)]
    first = import_sheets(client, auth, key, sheets).json()
    replay = import_sheets(client, auth, key, sheets).json()
    assert replay["replayed"] is True and replay["batch"]["id"] == first["batch"]["id"]
    # 换一个批次号重试同样的答卷：编号已登记，不再入样也不新开冲突。
    retry = import_sheets(client, auth, f"key-{uuid.uuid4().hex}", sheets).json()
    assert retry["batch"]["staged_count"] == 0 and retry["batch"]["duplicate_count"] == 1
    assert retry["created_conflicts"] == []
    rows = client.get("/survey-conflicts", headers=auth["analyst@harborstay.local"]).json()["open"]
    assert len([row for row in rows if row["tenant_id"] == tenant]) == 1
    assert len(query("SELECT id FROM survey_import_sheet WHERE tenant_id=%s", (tenant,))) == 1


def test_same_key_with_different_payload_is_rejected(client, auth, tenant):
    key = f"key-{uuid.uuid4().hex}"
    import_sheets(client, auth, key, [sheet(tenant, "S-5", 6)])
    response = import_sheets(client, auth, key, [sheet(tenant, "S-5", 2)])
    assert response.status_code == 409


def test_resolution_modes(client, auth, tenant):
    execute("""INSERT INTO renewal_surveys(tenant_id,lease_period,collected_at,cleanliness_score,service_score,
               value_score,community_score,renewal_intent,comments) VALUES(%s,'2026-2027',NOW(),4,4,4,4,3,'原件')""",
            (tenant,))
    import_sheets(client, auth, f"key-{uuid.uuid4().hex}", [sheet(tenant, "S-6", 9)])
    conflict = open_conflict(client, auth, tenant)
    response = client.post(f"/survey-conflicts/{conflict['id']}/resolve",
                           headers=auth["analyst@harborstay.local"],
                           json={"resolution": "adopted", "sheet_id": conflict["sheets"][0]["id"], "note": "核对纸质原件"})
    assert response.status_code == 200, response.text
    sample = next(row for row in client.get("/surveys", headers=auth["analyst@harborstay.local"]).json()
                  if row["tenant_id"] == tenant)
    assert float(sample["renewal_intent"]) == 9.0 and sample["sample_version"] == 2
    revision = one("SELECT * FROM renewal_survey_revision WHERE tenant_id=%s", (tenant,))
    assert revision and float(revision["renewal_intent"]) == 3.0

    # 再导入冲突，原问卷已派单时只能保留原答案并写明原因。
    manager = auth["manager@harborstay.local"]
    client.post("/reviews/approve", headers=manager, json={"note": "复核样本"})
    import_sheets(client, auth, f"key-{uuid.uuid4().hex}", [sheet(tenant, "S-7", 1)])
    conflict = open_conflict(client, auth, tenant)
    denied = client.post(f"/survey-conflicts/{conflict['id']}/resolve",
                         headers=auth["analyst@harborstay.local"],
                         json={"resolution": "adopted", "sheet_id": conflict["sheets"][0]["id"], "note": "换新答案"})
    assert denied.status_code == 409
    kept = client.post(f"/survey-conflicts/{conflict['id']}/resolve",
                       headers=auth["analyst@harborstay.local"],
                       json={"resolution": "kept_original", "note": "原问卷已派单，保留原答案"})
    assert kept.status_code == 200, kept.text
    sample = next(row for row in client.get("/surveys", headers=auth["analyst@harborstay.local"]).json()
                  if row["tenant_id"] == tenant)
    assert float(sample["renewal_intent"]) == 9.0
    task = one("SELECT * FROM follow_up_tasks WHERE tenant_id=%s", (tenant,))
    assert task and task["survey_answer_key"] == "3.50|3.50|3.50|3.50|9.00"


def test_concurrent_swap_and_dispatch_keep_followup_on_sample_answer(client, auth, tenant):
    import threading

    from src import survey_imports
    from src.db import query
    from src.services import build_followups

    analyst = query("SELECT id,email,full_name,role FROM users WHERE email='analyst@harborstay.local'")[0]
    manager = query("SELECT id,email,full_name,role FROM users WHERE email='manager@harborstay.local'")[0]

    for round_no in range(6):
        execute("DELETE FROM renewal_survey_revision WHERE tenant_id=%s", (tenant,))
        execute("DELETE FROM follow_up_tasks WHERE tenant_id=%s", (tenant,))
        execute("DELETE FROM survey_import_sheet WHERE tenant_id=%s", (tenant,))
        execute("DELETE FROM survey_conflict WHERE tenant_id=%s", (tenant,))
        execute("DELETE FROM renewal_surveys WHERE tenant_id=%s", (tenant,))
        execute("""INSERT INTO renewal_surveys(tenant_id,lease_period,collected_at,cleanliness_score,service_score,
                   value_score,community_score,renewal_intent,comments)
                   VALUES(%s,'2026-2027',NOW(),3,3,3,3,3,'原件')""", (tenant,))
        import_sheets(client, auth, f"key-{uuid.uuid4().hex}", [sheet(tenant, f"R-{round_no}", 9)])
        conflict = open_conflict(client, auth, tenant)

        def swap():
            from src.survey_import_schemas import ConflictResolveIn
            try:
                survey_imports.resolve_conflict(
                    conflict["id"],
                    ConflictResolveIn(resolution="adopted", sheet_id=conflict["sheets"][0]["id"], note="并发换卷核对"),
                    analyst)
            except Exception:
                pass

        def dispatch():
            for _ in range(3):
                build_followups(manager)

        threads = [threading.Thread(target=swap), threading.Thread(target=dispatch)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        s = one("SELECT * FROM renewal_surveys WHERE tenant_id=%s", (tenant,))
        task = one("SELECT * FROM follow_up_tasks WHERE tenant_id=%s", (tenant,))
        if task:
            assert task["survey_answer_key"] == "|".join(
                f"{float(s[field]):.2f}" for field in
                ("cleanliness_score", "service_score", "value_score", "community_score", "renewal_intent"))
            assert task["survey_version"] == s["sample_version"]
        # 同一租期永远只有一条跟进任务
        assert len(query("SELECT id FROM follow_up_tasks WHERE tenant_id=%s", (tenant,))) <= 1


def test_new_sheet_joins_open_conflict_group_instead_of_new_group(client, auth, tenant):
    execute("""INSERT INTO renewal_surveys(tenant_id,lease_period,collected_at,cleanliness_score,service_score,
               value_score,community_score,renewal_intent,comments) VALUES(%s,'2026-2027',NOW(),3.5,3.5,3.5,3.5,8,'原件')""",
            (tenant,))
    import_sheets(client, auth, f"key-{uuid.uuid4().hex}", [sheet(tenant, "G-1", 2)])
    first = open_conflict(client, auth, tenant)
    result = import_sheets(client, auth, f"key-{uuid.uuid4().hex}", [sheet(tenant, "G-2", 5)]).json()
    assert result["batch"]["staged_count"] == 1 and result["created_conflicts"] == []
    second = open_conflict(client, auth, tenant)
    assert second["id"] == first["id"]
    assert {s["sheet_ref"] for s in second["sheets"]} == {"G-1", "G-2"}
    # 与原问卷一致的答卷不并入冲突组，只算重复件
    result = import_sheets(client, auth, f"key-{uuid.uuid4().hex}", [sheet(tenant, "G-3", 8)]).json()
    assert result["batch"]["duplicate_count"] == 1 and result["batch"]["staged_count"] == 0


def test_failed_import_is_atomic_and_retryable(client, auth, tenant):
    moved_out = one("SELECT id FROM tenants WHERE status='moved_out' LIMIT 1")
    if not moved_out:
        pytest.skip("演示数据里没有已退租租客")
    key = f"key-{uuid.uuid4().hex}"
    bad = [sheet(tenant, "A-1", 6), sheet(moved_out["id"], "A-2", 6)]
    response = import_sheets(client, auth, key, bad)
    assert response.status_code == 422
    # 中断的导入不留下批次、答卷或样本
    assert one("SELECT id FROM survey_import_batch WHERE idempotency_key=%s", (key,)) is None
    assert query("SELECT id FROM survey_import_sheet WHERE tenant_id=%s", (tenant,)) == []
    assert query("SELECT id FROM renewal_surveys WHERE tenant_id=%s", (tenant,)) == []

    # 同一批次号修正后重试可以正常入样
    ok = import_sheets(client, auth, key, [sheet(tenant, "A-1", 6)])
    assert ok.status_code == 200, ok.text
    assert ok.json()["batch"]["accepted_count"] == 1
