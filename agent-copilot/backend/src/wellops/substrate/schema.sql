-- Raw tables mirror the source files 1:1 plus dataset_id. Never mutated after ingest.
CREATE TABLE IF NOT EXISTS areas (dataset_id VARCHAR, area_id VARCHAR, name VARCHAR, team_id VARCHAR,
    center_lat DOUBLE, center_lon DOUBLE);
CREATE TABLE IF NOT EXISTS teams (dataset_id VARCHAR, team_id VARCHAR, name VARCHAR, lead_user_id VARCHAR);
CREATE TABLE IF NOT EXISTS users (dataset_id VARCHAR, user_id VARCHAR, name VARCHAR, role VARCHAR, team_id VARCHAR);
CREATE TABLE IF NOT EXISTS wells (dataset_id VARCHAR, asset_id VARCHAR, name VARCHAR, well_type VARCHAR,
    area_id VARCHAR, team_id VARCHAR, in_charge_user_id VARCHAR, lat DOUBLE, lon DOUBLE, installed_on DATE,
    recorded_status VARCHAR, last_inspection_id VARCHAR, last_inspected_at TIMESTAMPTZ, area_listed VARCHAR);
CREATE TABLE IF NOT EXISTS equipment (dataset_id VARCHAR, asset_id VARCHAR, equipment_id VARCHAR, kind VARCHAR, model VARCHAR);
CREATE TABLE IF NOT EXISTS inspections (dataset_id VARCHAR, inspection_id VARCHAR, asset_id VARCHAR,
    inspected_at TIMESTAMPTZ, inspector_user_id VARCHAR, method VARCHAR, ambient_temp_c DOUBLE,
    wellhead_temp_c DOUBLE, spill_detected BOOLEAN, spill_area_pct DOUBLE, spill_est_volume_bbl DOUBLE,
    evidence_images VARCHAR[], notes VARCHAR);
CREATE TABLE IF NOT EXISTS maintenance (dataset_id VARCHAR, maintenance_id VARCHAR, asset_id VARCHAR,
    started_at TIMESTAMPTZ, completed_at TIMESTAMPTZ, maintenance_type VARCHAR, equipment_kind VARCHAR,
    issue VARCHAR, action_taken VARCHAR, technician_user_id VARCHAR, contractor VARCHAR, downtime_hours INTEGER,
    cost_labor DOUBLE, cost_parts DOUBLE, cost_total DOUBLE, triggered_by_inspection_id VARCHAR, report_file VARCHAR);
CREATE TABLE IF NOT EXISTS reg_events (dataset_id VARCHAR, event_id VARCHAR, asset_id VARCHAR, date DATE,
    agency VARCHAR, event_type VARCHAR, protocol_id VARCHAR, related_inspection_id VARCHAR, detail VARCHAR,
    hours_to_report INTEGER, fine_usd DOUBLE, status VARCHAR);
CREATE TABLE IF NOT EXISTS notes (dataset_id VARCHAR, note_id VARCHAR, asset_id VARCHAR, created_at TIMESTAMPTZ,
    author_user_id VARCHAR, note_type VARCHAR, inspection_id VARCHAR, inspection_id_in_text VARCHAR,
    text VARCHAR, valid_from DATE, valid_to DATE);
CREATE TABLE IF NOT EXISTS reports (dataset_id VARCHAR, maintenance_id VARCHAR, asset_id VARCHAR, path VARCHAR, text VARCHAR);
CREATE TABLE IF NOT EXISTS report_facts (dataset_id VARCHAR, maintenance_id VARCHAR, field VARCHAR,
    value VARCHAR, num DOUBLE, span_start INTEGER, span_end INTEGER, extractor VARCHAR);
CREATE TABLE IF NOT EXISTS docs (dataset_id VARCHAR, doc_id VARCHAR, path VARCHAR, section VARCHAR,
    title VARCHAR, text VARCHAR);

-- Rules extracted from manuals/SOPs (rules.yaml), each with a verbatim quote that must exist in its doc.
CREATE TABLE IF NOT EXISTS rules (param VARCHAR, scope_level VARCHAR, scope_key VARCHAR, value DOUBLE,
    unit VARCHAR, doc_id VARCHAR, section VARCHAR, quote VARCHAR);
CREATE TABLE IF NOT EXISTS type_inherits (dataset_id VARCHAR, well_type VARCHAR, parent VARCHAR);

-- Governed knowledge: every human statement (dataset notes that correct, and runtime feedback) as an overlay.
CREATE TABLE IF NOT EXISTS assertions (assertion_id VARCHAR PRIMARY KEY, dataset_id VARCHAR, kind VARCHAR,
    scope_level VARCHAR, scope_key VARCHAR, target_attr VARCHAR, value_num DOUBLE, value_text VARCHAR,
    original_num DOUBLE, author_user_id VARCHAR, reason VARCHAR, source VARCHAR, source_ref VARCHAR,
    valid_from DATE, valid_to DATE, tx_time TIMESTAMPTZ, status VARCHAR, visibility VARCHAR,
    superseded_by VARCHAR);
CREATE TABLE IF NOT EXISTS episodes (episode_id VARCHAR PRIMARY KEY, principal VARCHAR, chat_id VARCHAR,
    ts TIMESTAMPTZ, utterance VARCHAR, entities VARCHAR[], result_refs VARCHAR[], summary VARCHAR,
    forgotten BOOLEAN DEFAULT FALSE);
CREATE TABLE IF NOT EXISTS dq_flags (dataset_id VARCHAR, entity_id VARCHAR, kind VARCHAR, detail VARCHAR);
