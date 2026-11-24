from datetime import date, timedelta
from .db import connect, query, one, execute
from .analysis import run_analysis
from .survey_imports import answer_key

def audit(user, action, entity_type, entity_id, detail):
    execute("INSERT INTO audit_logs(actor_email,action,entity_type,entity_id,detail) VALUES(%s,%s,%s,%s,%s)", (user["email"], action, entity_type, entity_id, detail))

def survey_rows():
    return query("""
      SELECT s.*, t.full_name AS tenant_name, t.status AS tenant_status, t.property_id, t.lease_end_date,
             p.code AS property_code, p.name AS property_name, p.monthly_rent,
             p.property_age_years,
             COALESCE(AVG(se.resolution_days) FILTER (WHERE se.status='resolved'),0) AS service_resolution_days,
             CASE WHEN p.monthly_rent > 0 THEN p.monthly_rent/5000.0 ELSE 0 END AS rent_burden
      FROM renewal_surveys s
      JOIN tenants t ON t.id=s.tenant_id
      JOIN properties p ON p.id=t.property_id
      LEFT JOIN service_events se ON se.tenant_id=t.id
      GROUP BY s.id,t.id,p.id
      ORDER BY s.collected_at DESC
    """)

def build_followups(user):
    rows = survey_rows()
    created = []
    for s in rows:
        if s["tenant_status"] != "active":
            continue
        with connect() as conn:
            # 与换卷裁定共用组级锁，再锁问卷行：跟进依据始终等于当前入样答案。
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))",
                         (f"survey-group:{s['tenant_id']}:{s['lease_period']}",))
            locked = conn.execute(
                "SELECT * FROM renewal_surveys WHERE id=%s FOR UPDATE", (s["id"],)).fetchone()
            if not locked:
                continue
            tenant = conn.execute("SELECT status FROM tenants WHERE id=%s", (locked["tenant_id"],)).fetchone()
            if not tenant or tenant["status"] != "active":
                continue
            if locked["renewal_intent"] >= 7 and locked["service_score"] >= 4:
                continue
            exists = conn.execute(
                "SELECT id,survey_version,survey_answer_key FROM follow_up_tasks WHERE survey_id=%s",
                (locked["id"],)).fetchone()
            if exists:
                continue
            priority = "high" if locked["renewal_intent"] <= 4 or locked["service_score"] <= 2 else "medium"
            reason = f"续租意愿 {locked['renewal_intent']}/10，服务评分 {locked['service_score']}/5"
            task = conn.execute("""INSERT INTO follow_up_tasks(tenant_id,survey_id,task_type,priority,owner,due_date,reason,lease_end_snapshot,survey_version,survey_answer_key)
              VALUES(%s,%s,'续租沟通',%s,'customer_manager',CURRENT_DATE + INTERVAL '5 days',%s,%s,%s,%s)
              ON CONFLICT (survey_id) DO NOTHING
              RETURNING id""", (s["tenant_id"], locked["id"], priority, reason, s["lease_end_date"],
              locked["sample_version"], answer_key(locked))).fetchone()
            if not task:
                continue
            conn.execute('INSERT INTO audit_logs(actor_email,action,entity_type,entity_id,detail) VALUES(%s,%s,%s,%s,%s)',
                (user["email"], "create_follow_up", "follow_up_task", task["id"], reason))
            created.append(task["id"])
    return created

def get_analysis():
    rows = survey_rows()
    return run_analysis(rows)
