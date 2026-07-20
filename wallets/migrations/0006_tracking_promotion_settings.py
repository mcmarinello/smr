from django.db import migrations, models
import django.db.models.deletion
import django.core.validators


class Migration(migrations.Migration):

    dependencies = [
        ("wallets", "0005_walletscore_deleveraged"),
    ]

    operations = [
        migrations.AddField(
            model_name="wallet",
            name="last_seen_fill_timestamp",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="wallet",
            name="demotion_consecutive_count",
            field=models.IntegerField(default=0),
        ),
        migrations.CreateModel(
            name="WalletSettings",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("wallet", models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="settings",
                    to="wallets.wallet",
                )),
                ("promotion_threshold", models.IntegerField(
                    default=70,
                    validators=[
                        django.core.validators.MinValueValidator(0),
                        django.core.validators.MaxValueValidator(100),
                    ],
                )),
                ("demotion_threshold", models.IntegerField(
                    default=55,
                    validators=[
                        django.core.validators.MinValueValidator(0),
                        django.core.validators.MaxValueValidator(100),
                    ],
                )),
                ("demotion_consecutive_required", models.IntegerField(
                    default=3,
                    validators=[
                        django.core.validators.MinValueValidator(1),
                        django.core.validators.MaxValueValidator(20),
                    ],
                )),
            ],
            options={"db_table": "wallets_walletsettings"},
        ),
    ]