from django.contrib import admin

from billing.models import CustomerProfile


@admin.register(CustomerProfile)
class CustomerProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "status", "plan_interval", "current_period_end", "email_verified")
    list_filter = ("status", "plan_interval", "email_verified")
    search_fields = ("user__username", "user__email")
    raw_id_fields = ("user",)
