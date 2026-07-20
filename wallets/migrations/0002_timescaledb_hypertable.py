from django.db import migrations


class Migration(migrations.Migration):
    """
    Converts wallets_fill to a TimescaleDB hypertable partitioned by timestamp.
    Safe to run only when TimescaleDB extension is available (production).
    Silently skipped in non-TimescaleDB environments (e.g. local SQLite dev).
    """

    dependencies = [("wallets", "0001_initial")]

    operations = [
        migrations.RunSQL(
            sql="""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'
                ) THEN
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
