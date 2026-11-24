-- 纸质问卷补录：批次、答卷暂存、冲突裁定与换卷留痕。
-- 原问卷（renewal_surveys）始终是分析样本；导入答卷只有入样或裁定采用后才影响样本。

ALTER TABLE renewal_surveys
    ADD COLUMN IF NOT EXISTS sample_version integer NOT NULL DEFAULT 1;
ALTER TABLE renewal_surveys
    ADD COLUMN IF NOT EXISTS origin text NOT NULL DEFAULT 'manual'
        CHECK (origin IN ('manual','paper_import'));
ALTER TABLE renewal_surveys
    ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();

ALTER TABLE follow_up_tasks
    ADD COLUMN IF NOT EXISTS survey_version integer;
ALTER TABLE follow_up_tasks
    ADD COLUMN IF NOT EXISTS survey_answer_key text;
-- 历史跟进任务补填当时的入样版本与答案，便于核对跟进依据和新样本是否一致。
UPDATE follow_up_tasks f SET survey_version=s.sample_version,
    survey_answer_key=concat_ws('|',to_char(s.cleanliness_score,'FM999990.00'),to_char(s.service_score,'FM999990.00'),
        to_char(s.value_score,'FM999990.00'),to_char(s.community_score,'FM999990.00'),
        to_char(s.renewal_intent,'FM999990.00'))
    FROM renewal_surveys s WHERE f.survey_id=s.id
    AND (f.survey_version IS NULL OR f.survey_answer_key IS NULL);

CREATE TABLE IF NOT EXISTS survey_import_batch (
    id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    idempotency_key text NOT NULL UNIQUE,
    payload_hash text NOT NULL,
    source text NOT NULL,
    note text,
    imported_by integer NOT NULL REFERENCES users(id),
    imported_at timestamptz NOT NULL DEFAULT now(),
    sheet_count integer NOT NULL DEFAULT 0 CHECK (sheet_count >= 0),
    accepted_count integer NOT NULL DEFAULT 0 CHECK (accepted_count >= 0),
    staged_count integer NOT NULL DEFAULT 0 CHECK (staged_count >= 0),
    duplicate_count integer NOT NULL DEFAULT 0 CHECK (duplicate_count >= 0),
    conflict_count integer NOT NULL DEFAULT 0 CHECK (conflict_count >= 0)
);

CREATE TABLE IF NOT EXISTS survey_conflict (
    id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id integer NOT NULL REFERENCES tenants(id),
    lease_period text NOT NULL,
    original_survey_id integer REFERENCES renewal_surveys(id),
    status text NOT NULL DEFAULT 'staged' CHECK (status IN ('staged','resolved')),
    reason_code text NOT NULL CHECK (reason_code IN ('answers_disagree','differs_from_original')),
    resolution text CHECK (resolution IN ('adopted','kept_original')),
    resolved_sheet_id integer,
    note text,
    resolved_by integer REFERENCES users(id),
    resolved_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (status = 'staged' OR (resolution IS NOT NULL AND note IS NOT NULL
        AND resolved_by IS NOT NULL AND resolved_at IS NOT NULL))
);
-- 同一租客同一租期同时只能有一组待裁定冲突，后续导入并入该组而不是重复登记。
CREATE UNIQUE INDEX IF NOT EXISTS survey_conflict_open_group
    ON survey_conflict (tenant_id, lease_period) WHERE status = 'staged';

CREATE TABLE IF NOT EXISTS survey_import_sheet (
    id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_id integer NOT NULL REFERENCES survey_import_batch(id),
    sheet_ref text,
    tenant_id integer NOT NULL REFERENCES tenants(id),
    lease_period text NOT NULL,
    collected_at timestamptz NOT NULL DEFAULT now(),
    cleanliness_score numeric(5,2) NOT NULL CHECK (cleanliness_score BETWEEN 1 AND 5),
    service_score numeric(5,2) NOT NULL CHECK (service_score BETWEEN 1 AND 5),
    value_score numeric(5,2) NOT NULL CHECK (value_score BETWEEN 1 AND 5),
    community_score numeric(5,2) NOT NULL CHECK (community_score BETWEEN 1 AND 5),
    renewal_intent numeric(5,2) NOT NULL CHECK (renewal_intent BETWEEN 1 AND 10),
    comments text NOT NULL DEFAULT '',
    answer_key text NOT NULL,
    state text NOT NULL DEFAULT 'staged'
        CHECK (state IN ('accepted','staged','duplicate','discarded')),
    conflict_id integer REFERENCES survey_conflict(id),
    survey_id integer REFERENCES renewal_surveys(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (state <> 'staged' OR conflict_id IS NOT NULL),
    UNIQUE (batch_id, sheet_ref)
);
-- 同一租客下纸质答卷编号唯一，换批次重试时相同编号不会重复登记。
CREATE UNIQUE INDEX IF NOT EXISTS survey_import_sheet_tenant_ref
    ON survey_import_sheet (tenant_id, sheet_ref) WHERE sheet_ref IS NOT NULL;
CREATE INDEX IF NOT EXISTS survey_import_sheet_group
    ON survey_import_sheet (tenant_id, lease_period);
CREATE INDEX IF NOT EXISTS survey_import_sheet_conflict
    ON survey_import_sheet (conflict_id);

-- 每次换卷前把被替换的那一版答案留在此表，当前答案仍在 renewal_surveys。
CREATE TABLE IF NOT EXISTS renewal_survey_revision (
    id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    survey_id integer NOT NULL REFERENCES renewal_surveys(id),
    tenant_id integer NOT NULL REFERENCES tenants(id),
    lease_period text NOT NULL,
    survey_version integer NOT NULL CHECK (survey_version > 0),
    reason text NOT NULL CHECK (reason IN ('paper_swap','conflict_adopted')),
    cleanliness_score numeric(5,2) NOT NULL,
    service_score numeric(5,2) NOT NULL,
    value_score numeric(5,2) NOT NULL,
    community_score numeric(5,2) NOT NULL,
    renewal_intent numeric(5,2) NOT NULL,
    comments text,
    follow_up_task_id integer REFERENCES follow_up_tasks(id),
    source_sheet_id integer REFERENCES survey_import_sheet(id),
    recorded_by integer REFERENCES users(id),
    recorded_at timestamptz NOT NULL DEFAULT now()
);
