import hashlib
import json
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException

from .db import connect, query

SCORE_FIELDS = ("cleanliness_score", "service_score", "value_score", "community_score", "renewal_intent")
TWO_PLACES = Decimal("0.01")


def _number(value):
    return Decimal(str(value)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def answer_key(source):
    """冲突只看五道可计分题的答案；原话差异不构成答案冲突。"""
    return "|".join(format(_number(source[field]), "f") for field in SCORE_FIELDS)


def _clean(value):
    return (value or "").strip()


def _fingerprint(sheet):
    content = "|".join([str(sheet["tenant_id"]), sheet["lease_period"], answer_key(sheet), sheet["comments"]])
    return "auto:" + hashlib.sha256(content.encode()).hexdigest()[:40]


def _normalize(payload):
    sheets = []
    for item in payload.sheets:
        sheet = item.model_dump()
        sheet["lease_period"] = _clean(sheet["lease_period"])
        sheet["comments"] = _clean(sheet["comments"])
        sheet["sheet_ref"] = _clean(sheet["sheet_ref"]) or None
        sheet["collected_at"] = sheet["collected_at"].isoformat() if sheet["collected_at"] else None
        sheets.append(sheet)
    return sheets


def _payload_hash(sheets):
    canon = sorted(json.dumps(sheet, sort_keys=True, ensure_ascii=False) for sheet in sheets)
    return hashlib.sha256("\n".join(canon).encode()).hexdigest()


def _batch_summary(batch):
    return {key: batch[key] for key in (
        "id", "idempotency_key", "source", "note", "imported_by", "imported_at",
        "sheet_count", "accepted_count", "staged_count", "duplicate_count", "conflict_count")}


def import_sheets(payload, user):
    sheets = _normalize(payload)
    payload_hash = _payload_hash(sheets)
    with connect() as conn:
        # 导入事务串行：重试落到同一批次，答卷编号也只在锁内判重。
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('survey-paper-import'))")
        prior = conn.execute(
            "SELECT * FROM survey_import_batch WHERE idempotency_key=%s", (payload.idempotency_key,)).fetchone()
        if prior:
            if prior["payload_hash"] != payload_hash:
                raise HTTPException(409, "该批次号已用于不同内容的答卷，请换一个批次号后重试")
            return {"batch": _batch_summary(prior), "replayed": True, "created_conflicts": []}

        tenant_ids = sorted({sheet["tenant_id"] for sheet in sheets})
        tenants = {row["id"]: row for row in conn.execute(
            "SELECT id,status FROM tenants WHERE id = ANY(%s)", (tenant_ids,)).fetchall()}
        for index, sheet in enumerate(sheets, start=1):
            tenant = tenants.get(sheet["tenant_id"])
            if not tenant:
                raise HTTPException(422, f"第 {index} 行租客不存在")
            if tenant["status"] != "active":
                raise HTTPException(422, f"第 {index} 行租客已退租，纸质问卷不进入续租样本")

        batch = conn.execute(
            """INSERT INTO survey_import_batch(idempotency_key,payload_hash,source,note,imported_by,sheet_count)
               VALUES(%s,%s,%s,%s,%s,%s) RETURNING *""",
            (payload.idempotency_key, payload_hash, payload.source, payload.note, user["id"], len(sheets))).fetchone()

        prepared, repeated = _collect_new_sheets(conn, sheets)
        counts = {"accepted": 0, "staged": 0, "duplicate": len(repeated), "conflict": 0}
        grouped = {}
        for sheet, ref in prepared:
            grouped.setdefault((sheet["tenant_id"], sheet["lease_period"]), []).append((sheet, ref))

        created_conflicts = []
        for (tenant_id, period), group in grouped.items():
            created_conflicts.extend(_process_group(conn, batch["id"], tenant_id, period, group, counts))

        batch = conn.execute(
            """UPDATE survey_import_batch SET accepted_count=%s,staged_count=%s,duplicate_count=%s,conflict_count=%s
               WHERE id=%s RETURNING *""",
            (counts["accepted"], counts["staged"], counts["duplicate"], counts["conflict"], batch["id"])).fetchone()
        conn.execute(
            'INSERT INTO audit_logs(actor_email,action,entity_type,entity_id,detail) VALUES(%s,%s,%s,%s,%s)',
            (user["email"], "survey_paper_import", "survey_import_batch", batch["id"],
             f"{len(sheets)} 份答卷，入样 {counts['accepted']}，暂存 {counts['staged']}，重复 {counts['duplicate']}"))
        return {"batch": _batch_summary(batch), "replayed": False, "created_conflicts": created_conflicts}


def _collect_new_sheets(conn, sheets):
    """给每份答卷落到唯一编号上：重试时同编号的答卷直接视为重复，不再入样。"""
    prepared, repeated, seen = [], [], {}
    for sheet in sheets:
        ref = sheet["sheet_ref"] or _fingerprint(sheet)
        key = answer_key(sheet)
        scope = (sheet["tenant_id"], ref)
        existing = conn.execute(
            "SELECT answer_key FROM survey_import_sheet WHERE tenant_id=%s AND sheet_ref=%s",
            scope).fetchone()
        if existing:
            if existing["answer_key"] != key:
                raise HTTPException(409, f"答卷编号 {ref} 已登记过不同答案，请核对后重新编号")
            repeated.append(ref)
            continue
        if scope in seen:
            if seen[scope] != key:
                raise HTTPException(409, f"答卷编号 {ref} 在本批次内对应不同答案")
            repeated.append(ref)
            continue
        seen[scope] = key
        prepared.append((sheet, ref))
    return prepared, repeated


def _process_group(conn, batch_id, tenant_id, period, group, counts):
    # 同一租客同一租期的导入与裁定串行，避免并发下重复入样或重复登记冲突。
    conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"survey-group:{tenant_id}:{period}",))
    original = conn.execute(
        "SELECT * FROM renewal_surveys WHERE tenant_id=%s AND lease_period=%s FOR UPDATE",
        (tenant_id, period)).fetchone()
    open_conflict = conn.execute(
        "SELECT * FROM survey_conflict WHERE tenant_id=%s AND lease_period=%s AND status='staged' FOR UPDATE",
        (tenant_id, period)).fetchone()
    keys = {answer_key(sheet) for sheet, _ in group}

    if open_conflict and original and all(key == answer_key(original) for key in keys):
        # 已有冲突组在等裁定，但这批答卷与原问卷一致：样本没有变化，只留重复件。
        for sheet, ref in group:
            _insert_sheet(conn, batch_id, sheet, ref, "duplicate", None, original["id"])
            counts["duplicate"] += 1
        return []

    reason = None
    if not open_conflict:
        if original and any(key != answer_key(original) for key in keys):
            reason = "differs_from_original"
        elif not original and len(keys) > 1:
            reason = "answers_disagree"

    if not open_conflict and not reason:
        if original:
            # 与原件答案一致：样本不变，答卷留存为重复件。
            for sheet, ref in group:
                _insert_sheet(conn, batch_id, sheet, ref, "duplicate", None, original["id"])
                counts["duplicate"] += 1
            return []
        first, first_ref = group[0]
        survey = conn.execute(
            """INSERT INTO renewal_surveys(tenant_id,lease_period,collected_at,cleanliness_score,service_score,
                   value_score,community_score,renewal_intent,comments,origin)
               VALUES(%s,%s,COALESCE(%s,NOW()),%s,%s,%s,%s,%s,%s,'paper_import')
               ON CONFLICT (tenant_id,lease_period) DO NOTHING RETURNING id""",
            (tenant_id, period, _collected(first, "collected_at"), first["cleanliness_score"],
             first["service_score"], first["value_score"], first["community_score"],
             first["renewal_intent"], first["comments"])).fetchone()
        if survey:
            _insert_sheet(conn, batch_id, first, first_ref, "accepted", None, survey["id"])
            counts["accepted"] += 1
            for sheet, ref in group[1:]:
                _insert_sheet(conn, batch_id, sheet, ref, "duplicate", None, survey["id"])
                counts["duplicate"] += 1
            return []
        # 并发的非导入问卷抢先落库：回到冲突判断。
        original = conn.execute(
            "SELECT * FROM renewal_surveys WHERE tenant_id=%s AND lease_period=%s FOR UPDATE",
            (tenant_id, period)).fetchone()
        if original and answer_key(original) in keys:
            for sheet, ref in group:
                _insert_sheet(conn, batch_id, sheet, ref, "duplicate", None, original["id"])
                counts["duplicate"] += 1
            return []
        reason = "differs_from_original" if original else "answers_disagree"

    conflict = open_conflict
    if not conflict:
        conflict = conn.execute(
            """INSERT INTO survey_conflict(tenant_id,lease_period,original_survey_id,reason_code)
               VALUES(%s,%s,%s,%s) RETURNING *""",
            (tenant_id, period, original["id"] if original else None, reason)).fetchone()
        counts["conflict"] += 1
    for sheet, ref in group:
        _insert_sheet(conn, batch_id, sheet, ref, "staged", conflict["id"], None)
        counts["staged"] += 1
    return [conflict["id"]] if not open_conflict else []


def _collected(sheet, key):
    value = sheet.get(key)
    if not value:
        return None
    return datetime.fromisoformat(value) if isinstance(value, str) else value

def _insert_sheet(conn, batch_id, sheet, ref, state, conflict_id, survey_id):
    return conn.execute(
        """INSERT INTO survey_import_sheet(batch_id,sheet_ref,tenant_id,lease_period,collected_at,
               cleanliness_score,service_score,value_score,community_score,renewal_intent,comments,
               answer_key,state,conflict_id,survey_id)
           VALUES(%s,%s,%s,%s,COALESCE(%s,NOW()),%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
        (batch_id, ref, sheet["tenant_id"], sheet["lease_period"], _collected(sheet, "collected_at"),
         sheet["cleanliness_score"], sheet["service_score"], sheet["value_score"], sheet["community_score"],
         sheet["renewal_intent"], sheet["comments"], answer_key(sheet), state, conflict_id, survey_id)).fetchone()


def _survey_payload(survey):
    if not survey:
        return None
    values = {field: float(_number(survey[field])) for field in SCORE_FIELDS}
    values.update({
        "id": survey["id"], "tenant_id": survey["tenant_id"], "lease_period": survey["lease_period"],
        "comments": survey["comments"], "sample_version": survey["sample_version"],
        "status": survey["status"], "origin": survey["origin"],
        "answer_key": answer_key(survey), "collected_at": survey["collected_at"]})
    return values


def _sheet_payload(sheet):
    values = {field: float(_number(sheet[field])) for field in SCORE_FIELDS}
    values.update({
        "id": sheet["id"], "sheet_ref": sheet["sheet_ref"], "state": sheet["state"],
        "comments": sheet["comments"], "answer_key": sheet["answer_key"],
        "collected_at": sheet["collected_at"], "survey_id": sheet["survey_id"]})
    return values


def conflict_detail(conn, conflict):
    tenant = conn.execute(
        """SELECT t.full_name AS tenant_name,t.lease_end_date,p.code AS property_code,p.name AS property_name
           FROM tenants t JOIN properties p ON p.id=t.property_id WHERE t.id=%s""",
        (conflict["tenant_id"],)).fetchone()
    original = conn.execute(
        "SELECT * FROM renewal_surveys WHERE tenant_id=%s AND lease_period=%s",
        (conflict["tenant_id"], conflict["lease_period"])).fetchone()
    task = None
    if original:
        task = conn.execute(
            """SELECT id,status,priority,reason,survey_version,survey_answer_key
               FROM follow_up_tasks WHERE survey_id=%s ORDER BY id LIMIT 1""", (original["id"],)).fetchone()
    sheets = conn.execute(
        "SELECT * FROM survey_import_sheet WHERE conflict_id=%s ORDER BY id", (conflict["id"],)).fetchall()
    detail = dict(conflict)
    detail.update({
        "tenant_name": tenant["tenant_name"] if tenant else None,
        "lease_end_date": tenant["lease_end_date"] if tenant else None,
        "property_code": tenant["property_code"] if tenant else None,
        "property_name": tenant["property_name"] if tenant else None,
        "original": _survey_payload(original),
        "followup": dict(task) if task else None,
        "sheets": [_sheet_payload(sheet) for sheet in sheets],
    })
    return detail


def list_conflicts():
    with connect() as conn:
        open_rows = conn.execute(
            "SELECT * FROM survey_conflict WHERE status='staged' ORDER BY created_at,id").fetchall()
        resolved_rows = conn.execute(
            "SELECT * FROM survey_conflict WHERE status='resolved' ORDER BY resolved_at DESC,id DESC LIMIT 30").fetchall()
        return {"open": [conflict_detail(conn, row) for row in open_rows],
                "resolved": [conflict_detail(conn, row) for row in resolved_rows]}


def list_batches():
    return [_batch_summary(row) for row in
            query("SELECT * FROM survey_import_batch ORDER BY id DESC LIMIT 30")]


def resolve_conflict(conflict_id, payload, user):
    with connect() as conn:
        head = conn.execute(
            "SELECT tenant_id,lease_period FROM survey_conflict WHERE id=%s", (conflict_id,)).fetchone()
        if not head:
            raise HTTPException(404, "冲突组不存在")
        # 先取组级锁再碰行锁，与导入保持同一加锁顺序，避免并发换卷和派单互相等待。
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))",
                     (f"survey-group:{head['tenant_id']}:{head['lease_period']}",))
        conflict = conn.execute(
            "SELECT * FROM survey_conflict WHERE id=%s FOR UPDATE", (conflict_id,)).fetchone()
        if conflict["status"] != "staged":
            raise HTTPException(409, "该冲突已经裁定")
        # 与派单同一把行锁：裁定时看到的就是派单任务提交后的状态。
        original = conn.execute(
            "SELECT * FROM renewal_surveys WHERE tenant_id=%s AND lease_period=%s FOR UPDATE",
            (conflict["tenant_id"], conflict["lease_period"])).fetchone()
        sheets = conn.execute(
            """SELECT * FROM survey_import_sheet WHERE conflict_id=%s AND state='staged'
               ORDER BY id FOR UPDATE""", (conflict_id,)).fetchall()
        if not sheets:
            raise HTTPException(409, "该冲突组没有待裁定答卷")
        task = None
        if original:
            task = conn.execute(
                "SELECT id,status FROM follow_up_tasks WHERE survey_id=%s ORDER BY id LIMIT 1",
                (original["id"],)).fetchone()

        if payload.resolution == "adopted":
            if task:
                raise HTTPException(409, f"原问卷已派出跟进（任务 #{task['id']}），裁定时只能保留原答案并说明原因")
            chosen = _choose_sheet(sheets, payload.sheet_id)
            survey_id = _adopt(conn, conflict, original, chosen, user)
            action = "survey_conflict_adopted"
            detail = f"采用答卷 {chosen['sheet_ref']} · {payload.note}"
        else:
            if not original:
                raise HTTPException(422, "该租客和租期没有原问卷，无法保留原答案，请选择采用某份答卷")
            conn.execute(
                "UPDATE survey_import_sheet SET state='discarded' WHERE conflict_id=%s AND state='staged'",
                (conflict_id,))
            survey_id = original["id"]
            action = "survey_conflict_kept_original"
            detail = f"{payload.note} · 已派出跟进 #{task['id']}" if task else payload.note

        updated = conn.execute(
            """UPDATE survey_conflict SET status='resolved',resolution=%s,resolved_sheet_id=%s,
                   note=%s,resolved_by=%s,resolved_at=now()
               WHERE id=%s RETURNING *""",
            (payload.resolution, chosen["id"] if payload.resolution == "adopted" else None,
             payload.note, user["id"], conflict_id)).fetchone()
        conn.execute(
            'INSERT INTO audit_logs(actor_email,action,entity_type,entity_id,detail) VALUES(%s,%s,%s,%s,%s)',
            (user["email"], action, "survey_conflict", conflict_id, detail))
        result = conflict_detail(conn, updated)
        result["survey_id"] = survey_id
        return result


def _choose_sheet(sheets, sheet_id):
    if sheet_id is not None:
        chosen = next((sheet for sheet in sheets if sheet["id"] == sheet_id), None)
        if not chosen:
            raise HTTPException(422, "所选答卷不在该冲突组的待裁定答卷内")
        return chosen
    if len({sheet["answer_key"] for sheet in sheets}) > 1:
        raise HTTPException(422, "该冲突组有多种答案，请指定要采用的答卷编号")
    return sheets[0]


def _adopt(conn, conflict, original, chosen, user):
    if not original:
        survey_id = conn.execute(
            """INSERT INTO renewal_surveys(tenant_id,lease_period,collected_at,cleanliness_score,service_score,
                   value_score,community_score,renewal_intent,comments,origin)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,'paper_import')
               ON CONFLICT (tenant_id,lease_period) DO NOTHING RETURNING id""",
            (conflict["tenant_id"], conflict["lease_period"], chosen["collected_at"],
             chosen["cleanliness_score"], chosen["service_score"], chosen["value_score"],
             chosen["community_score"], chosen["renewal_intent"], chosen["comments"])).fetchone()
        if survey_id:
            survey_id = survey_id["id"]
        else:
            # 并发的手工问卷在锁外抢先落库：按换卷处理，旧答案同样留痕。
            original = conn.execute(
                "SELECT * FROM renewal_surveys WHERE tenant_id=%s AND lease_period=%s FOR UPDATE",
                (conflict["tenant_id"], conflict["lease_period"])).fetchone()
    if original:
        conn.execute(
            """INSERT INTO renewal_survey_revision(survey_id,tenant_id,lease_period,survey_version,reason,
                   cleanliness_score,service_score,value_score,community_score,renewal_intent,comments,
                   source_sheet_id,recorded_by)
               SELECT id,tenant_id,lease_period,sample_version,'conflict_adopted',
                   cleanliness_score,service_score,value_score,community_score,renewal_intent,comments,
                   %s,%s
               FROM renewal_surveys WHERE id=%s""",
            (chosen["id"], user["id"], original["id"]))
        # 换卷后旧复核结论失效，等待按新答案重新复核。
        conn.execute(
            """UPDATE renewal_surveys SET cleanliness_score=%s,service_score=%s,value_score=%s,
                   community_score=%s,renewal_intent=%s,comments=%s,collected_at=%s,
                   sample_version=sample_version+1,updated_at=now(),
                   status='submitted',reviewed_at=NULL,analyzed_at=NULL
               WHERE id=%s""",
            (chosen["cleanliness_score"], chosen["service_score"], chosen["value_score"],
             chosen["community_score"], chosen["renewal_intent"], chosen["comments"],
             chosen["collected_at"], original["id"]))
        survey_id = original["id"]
    conn.execute("UPDATE survey_import_sheet SET state='accepted',survey_id=%s WHERE id=%s", (survey_id, chosen["id"]))
    conn.execute(
        "UPDATE survey_import_sheet SET state='discarded' WHERE conflict_id=%s AND state='staged' AND id<>%s",
        (conflict["id"], chosen["id"]))
    return survey_id
