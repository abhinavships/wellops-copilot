"""Data-quality flags: traps from DATA.md recomputed for any dataset, returned alongside tool results."""

FLAGS = {
    # Latest reading disagrees with the system-of-record status (refreshed monthly; may be stale).
    "recorded_status_stale": """
        SELECT asset_id, 'recorded=' || recorded_status || ', latest Δ=' || round(delta_c, 1) || '°C'
        FROM latest_status(?, ?::TIMESTAMPTZ) WHERE recorded_status_stale""",
    # Hot wellhead that is nevertheless not producing (absolute temperature misleads).
    "hot_idle": """
        SELECT inspection_id, wellhead_eff || '°C vs air ' || ambient_eff || '°C'
        FROM ins_eff(?, ?::TIMESTAMPTZ) WHERE NOT producing AND wellhead_eff >= 45""",
    # Within 1 °C of the threshold: the call is sensitive to sensor error.
    "borderline_delta": """
        SELECT inspection_id, 'Δ=' || round(delta_c, 2) || ' vs ' || delta_min_c
        FROM ins_eff(?, ?::TIMESTAMPTZ) WHERE abs(delta_c - delta_min_c) < 1""",
    # RC-01 gaps between consecutive inspections.
    "inspection_gap": """
        SELECT asset_id, 'gap ' || gap || ' d ending ' || inspected_at::DATE FROM (
          SELECT asset_id, inspected_at,
                 date_diff('day', lag(inspected_at) OVER (PARTITION BY asset_id ORDER BY inspected_at), inspected_at) AS gap
          FROM ins_eff(?, ?::TIMESTAMPTZ)) WHERE gap > 14""",
    # Repairs still in progress at the clock.
    "repair_in_progress": """
        SELECT maintenance_id, 'started ' || started_at::DATE || ', planned end ' || completed_at::DATE
        FROM repairs(?, ?::TIMESTAMPTZ) WHERE open_as_of""",
}


def compute(con, ds: str, asof: str | None = None) -> None:
    from wellops.settings import settings

    asof = asof or f"{settings.clock_today} 23:59:59+00"
    con.execute("DELETE FROM dq_flags WHERE dataset_id = ?", [ds])
    for kind, sql in FLAGS.items():
        rows = con.execute(sql, [ds, asof]).fetchall()
        if rows:
            con.executemany("INSERT INTO dq_flags VALUES (?,?,?,?)", [(ds, r[0], kind, r[1]) for r in rows])
    con.execute("""INSERT INTO dq_flags SELECT dataset_id, maintenance_id, 'nonstandard_report_filename', report_file
                   FROM maintenance WHERE dataset_id = ? AND report_file IS NOT NULL
                   AND report_file NOT LIKE '%' || maintenance_id || '.md'""", [ds])
    con.execute("""INSERT INTO dq_flags SELECT dataset_id, note_id, 'inspection_ref_only_in_text', inspection_id_in_text
                   FROM notes WHERE dataset_id = ? AND inspection_id IS NULL AND inspection_id_in_text IS NOT NULL""",
                [ds])
