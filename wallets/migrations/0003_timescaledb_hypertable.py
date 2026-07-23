from django.db import migrations


class Migration(migrations.Migration):
    """
    Converts wallets_fill to a TimescaleDB hypertable partitioned by timestamp.
    Safe to run only when TimescaleDB extension is available (production).
    Silently skipped in non-TimescaleDB environments (e.g. local SQLite dev).

    TimescaleDB requires every unique index on a hypertable — including the
    primary key — to include the partitioning column. wallets_fill's original
    primary key was `id` alone, so it is replaced here with a composite
    (id, timestamp) primary key before create_hypertable() runs. Django's ORM
    doesn't require the DB-level primary key to be single-column — it only
    relies on the `id` field's Python-side `primary_key=True` — so `.get(pk=...)`
    and all existing lookups are unaffected. The one other place that depended
    on `id` alone being DB-unique was the FK from
    copytrading.SimulatedTrade.fill_source, which is declared with
    db_constraint=False for exactly this reason.
    """

    dependencies = [
        ("wallets", "0002_rename_wallets_fill_wallet_ts_idx_wallets_fil_wallet__e920e1_idx_and_more"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'
                ) THEN
                    ALTER TABLE wallets_fill DROP CONSTRAINT wallets_fill_pkey;
                    ALTER TABLE wallets_fill ADD PRIMARY KEY (id, timestamp);
                    PERFORM create_hypertable(
                        'wallets_fill',
                        'timestamp',
                        if_not_exists => TRUE,
                        migrate_data => TRUE
                    );
                END IF;
            END;
            $$;
            """,
            reverse_sql=migrations.RunSQL.noop,
        )
    ]
