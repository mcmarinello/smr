from django.contrib import admin

from .models import AlertHistory, AlertRule, Notification


@admin.register(AlertRule)
class AlertRuleAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "condition_type",
        "wallet",
        "asset_filter",
        "threshold",
        "channel",
        "is_active",
        "created_at",
    )
    list_filter = ("condition_type", "channel", "is_active")
    search_fields = ("user__username", "wallet__address", "asset_filter")
    raw_id_fields = ("user", "wallet")
    readonly_fields = ("created_at", "updated_at")


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "level",
        "event_type",
        "wallet",
        "title",
        "read",
        "sent_at",
        "created_at",
    )
    list_filter = ("level", "read", "event_type", "sent_at")
    search_fields = ("user__username", "wallet__address", "title", "body")
    raw_id_fields = ("user", "alert_rule", "wallet")
    readonly_fields = ("created_at", "updated_at", "sent_at")
    date_hierarchy = "created_at"


@admin.register(AlertHistory)
class AlertHistoryAdmin(admin.ModelAdmin):
    list_display = (
        "alert_rule",
        "wallet",
        "event_type",
        "asset",
        "notification",
        "fired_at",
    )
    list_filter = ("event_type", "asset")
    search_fields = (
        "alert_rule__user__username",
        "wallet__address",
        "asset",
    )
    raw_id_fields = ("alert_rule", "wallet", "notification")
    readonly_fields = ("fired_at", "created_at", "updated_at")
    date_hierarchy = "fired_at"