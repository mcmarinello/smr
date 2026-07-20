from django.contrib import admin

from .models import DiscoveryStatus


@admin.register(DiscoveryStatus)
class DiscoveryStatusAdmin(admin.ModelAdmin):
    list_display = ("source", "discovered_count", "is_running", "last_scan_at", "last_error_truncated")
    list_filter = ("source", "is_running")
    readonly_fields = ("source", "discovered_count", "last_scan_at", "last_error", "is_running", "created_at", "updated_at")

    @admin.display(description="Last error")
    def last_error_truncated(self, obj: DiscoveryStatus) -> str:
        return (obj.last_error[:80] + "…") if len(obj.last_error) > 80 else obj.last_error
