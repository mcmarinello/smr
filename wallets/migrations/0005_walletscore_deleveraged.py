# Generated manually for Sprint 4 — Deleveraging Recalculator (PRD §15.4).
# Adds `score_deleveraged`, `leverage_dependency_index` and
# `component_breakdown_deleveraged` to WalletScore, plus a supporting
# index on (window, -score_deleveraged) for deleveraged leaderboards.

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("wallets", "0004_wallet_metrics_and_score"),
    ]

    operations = [
        migrations.AddField(
            model_name="walletscore",
            name="score_deleveraged",
            field=models.DecimalField(
                decimal_places=3,
                default=0,
                max_digits=6,
                validators=[
                    django.core.validators.MinValueValidator(0),
                    django.core.validators.MaxValueValidator(100),
                ],
            ),
        ),
        migrations.AddField(
            model_name="walletscore",
            name="leverage_dependency_index",
            field=models.DecimalField(decimal_places=6, default=0, max_digits=8),
        ),
        migrations.AddField(
            model_name="walletscore",
            name="component_breakdown_deleveraged",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddIndex(
            model_name="walletscore",
            index=models.Index(
                fields=["window", "-score_deleveraged"],
                name="wallets_sco_window_02f500_idx",
            ),
        ),
    ]