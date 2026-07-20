from django.contrib import admin
from .models import Wallet, Fill, Position


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ("address", "discovery_source", "is_target", "is_active", "first_seen", "last_seen")
    list_filter = ("is_target", "is_active", "discovery_source")
    search_fields = ("address",)
    readonly_fields = ("first_seen", "last_seen", "created_at", "updated_at")


@admin.register(Fill)
class FillAdmin(admin.ModelAdmin):
    list_display = ("wallet", "asset", "side", "size", "price", "closed_pnl", "timestamp", "is_liquidation")
    list_filter = ("side", "is_liquidation", "asset")
    search_fields = ("wallet__address", "asset", "oid")
    readonly_fields = ("created_at", "updated_at")
    raw_id_fields = ("wallet",)


@admin.register(Position)
class PositionAdmin(admin.ModelAdmin):
    list_display = ("wallet", "asset", "side", "size", "entry_price", "status", "opened_at")
    list_filter = ("status", "side", "asset")
    search_fields = ("wallet__address", "asset")
    raw_id_fields = ("wallet",)
