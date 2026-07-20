from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("wallets", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="DiscoveryStatus",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "source",
                    models.CharField(
                        choices=[
                            ("leaderboard", "Leaderboard"),
                            ("trade_stream", "Trade Stream"),
                        ],
                        max_length=20,
                        unique=True,
                    ),
                ),
                ("discovered_count", models.IntegerField(default=0)),
                ("last_scan_at", models.DateTimeField(blank=True, null=True)),
                ("last_error", models.TextField(blank=True)),
                ("is_running", models.BooleanField(default=False)),
            ],
            options={
                "verbose_name": "Discovery Status",
                "verbose_name_plural": "Discovery Statuses",
                "db_table": "discovery_status",
            },
        ),
    ]
