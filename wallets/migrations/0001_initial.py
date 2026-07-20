from django.db import migrations, models
import django.db.models.deletion
import django.contrib.postgres.fields


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Wallet",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("address", models.CharField(db_index=True, max_length=42, unique=True)),
                ("first_seen", models.DateTimeField(auto_now_add=True)),
                ("last_seen", models.DateTimeField(auto_now=True)),
                ("discovery_source", models.CharField(
                    choices=[("leaderboard", "Leaderboard"), ("trade_stream", "Trade Stream"), ("manual", "Manual")],
                    default="manual",
                    max_length=20,
                )),
                ("is_target", models.BooleanField(db_index=True, default=False)),
                ("promoted_reason", models.TextField(blank=True)),
                ("promoted_at", models.DateTimeField(blank=True, null=True)),
                ("score_at_promotion", models.IntegerField(blank=True, null=True)),
                ("is_active", models.BooleanField(default=True)),
                ("tags", django.contrib.postgres.fields.ArrayField(
                    base_field=models.CharField(max_length=100), blank=True, default=list, size=None
                )),
            ],
            options={"db_table": "wallets_wallet"},
        ),
        migrations.CreateModel(
            name="Fill",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("wallet", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="fills", to="wallets.wallet")),
                ("asset", models.CharField(db_index=True, max_length=20)),
                ("side", models.CharField(choices=[("buy", "Buy"), ("sell", "Sell")], max_length=4)),
                ("price", models.DecimalField(decimal_places=10, max_digits=30)),
                ("size", models.DecimalField(decimal_places=10, max_digits=30)),
                ("fee", models.DecimalField(decimal_places=10, max_digits=30)),
                ("closed_pnl", models.DecimalField(decimal_places=10, max_digits=30)),
                ("timestamp", models.DateTimeField(db_index=True)),
                ("is_liquidation", models.BooleanField(default=False)),
                ("oid", models.BigIntegerField(unique=True)),
                ("direction", models.CharField(
                    blank=True, choices=[("open", "Open"), ("close", "Close")], max_length=5
                )),
                ("start_position", models.DecimalField(blank=True, decimal_places=10, max_digits=30, null=True)),
                ("hash", models.CharField(blank=True, max_length=100)),
                ("tid", models.BigIntegerField(blank=True, null=True)),
            ],
            options={"db_table": "wallets_fill"},
        ),
        migrations.CreateModel(
            name="Position",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("wallet", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="positions", to="wallets.wallet")),
                ("asset", models.CharField(db_index=True, max_length=20)),
                ("side", models.CharField(choices=[("long", "Long"), ("short", "Short")], max_length=5)),
                ("size", models.DecimalField(decimal_places=10, max_digits=30)),
                ("entry_price", models.DecimalField(decimal_places=10, max_digits=30)),
                ("leverage", models.DecimalField(blank=True, decimal_places=4, max_digits=10, null=True)),
                ("liquidation_price", models.DecimalField(blank=True, decimal_places=10, max_digits=30, null=True)),
                ("unrealized_pnl", models.DecimalField(blank=True, decimal_places=10, max_digits=30, null=True)),
                ("status", models.CharField(choices=[("open", "Open"), ("closed", "Closed")], default="open", max_length=6)),
                ("opened_at", models.DateTimeField(blank=True, null=True)),
                ("closed_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={"db_table": "wallets_position"},
        ),
        migrations.AddIndex(
            model_name="wallet",
            index=models.Index(fields=["is_target", "is_active"], name="wallets_wallet_target_active_idx"),
        ),
        migrations.AddIndex(
            model_name="fill",
            index=models.Index(fields=["wallet", "timestamp"], name="wallets_fill_wallet_ts_idx"),
        ),
        migrations.AddIndex(
            model_name="fill",
            index=models.Index(fields=["wallet", "asset", "timestamp"], name="wallets_fill_wallet_asset_ts_idx"),
        ),
        migrations.AddIndex(
            model_name="position",
            index=models.Index(fields=["wallet", "status"], name="wallets_position_wallet_status_idx"),
        ),
        migrations.AddIndex(
            model_name="position",
            index=models.Index(fields=["wallet", "asset", "status"], name="wallets_position_wallet_asset_status_idx"),
        ),
    ]
