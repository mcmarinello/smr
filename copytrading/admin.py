from django.contrib import admin

from .models import CopyTradingProfile, CopyTradingTarget, SimulatedTrade


@admin.register(CopyTradingProfile)
class CopyTradingProfileAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "user",
        "strategy",
        "initial_capital",
        "max_position_pct",
        "max_concurrent_positions",
        "max_hold_hours",
        "is_active",
        "created_at",
    )
    list_filter = ("strategy", "is_active")
    search_fields = ("name", "user__username")
    raw_id_fields = ("user",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(CopyTradingTarget)
class CopyTradingTargetAdmin(admin.ModelAdmin):
    list_display = (
        "profile",
        "wallet",
        "allocation_pct",
        "is_active",
        "created_at",
    )
    list_filter = ("is_active",)
    search_fields = (
        "profile__name",
        "profile__user__username",
        "wallet__address",
    )
    raw_id_fields = ("profile", "wallet")
    readonly_fields = ("created_at", "updated_at")


@admin.register(SimulatedTrade)
class SimulatedTradeAdmin(admin.ModelAdmin):
    list_display = (
        "profile",
        "wallet",
        "asset",
        "side",
        "entry_price",
        "size_usd",
        "exit_price",
        "pnl_usd",
        "status",
        "opened_at",
        "closed_at",
    )
    list_filter = ("status", "side")
    search_fields = (
        "profile__name",
        "wallet__address",
        "asset",
    )
    raw_id_fields = ("profile", "wallet", "fill_source")
    readonly_fields = ("created_at", "updated_at", "opened_at", "closed_at")
    date_hierarchy = "opened_at"