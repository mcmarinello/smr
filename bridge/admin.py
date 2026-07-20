from django.contrib import admin

from .models import BridgeAccessLog


@admin.register(BridgeAccessLog)
class BridgeAccessLogAdmin(admin.ModelAdmin):
    list_display = (
        "timestamp",
        "endpoint",
        "response_code",
        "requester_ip",
    )
    list_filter = ("response_code", "endpoint")
    search_fields = ("endpoint", "requester_ip")
    readonly_fields = (
        "timestamp",
        "requester_ip",
        "endpoint",
        "response_code",
        "data_snapshot",
    )
    date_hierarchy = "timestamp"