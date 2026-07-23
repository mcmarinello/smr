from django.contrib import admin

from billing.models import CryptoPayment, CustomerProfile, ExchangeCredential, Favorite, PromoCode


@admin.register(CustomerProfile)
class CustomerProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "status", "plan_interval", "current_period_end", "email_verified")
    list_filter = ("status", "plan_interval", "email_verified")
    search_fields = ("user__username", "user__email")
    raw_id_fields = ("user",)


@admin.register(ExchangeCredential)
class ExchangeCredentialAdmin(admin.ModelAdmin):
    list_display = ("user", "exchange", "is_active", "created_at")
    list_filter = ("exchange", "is_active")
    search_fields = ("user__username",)
    raw_id_fields = ("user",)
    readonly_fields = ("api_key_encrypted", "api_secret_encrypted")


@admin.register(Favorite)
class FavoriteAdmin(admin.ModelAdmin):
    list_display = ("user", "wallet", "created_at")
    search_fields = ("user__username", "wallet__address")
    raw_id_fields = ("user", "wallet")


@admin.register(PromoCode)
class PromoCodeAdmin(admin.ModelAdmin):
    list_display = ("code", "discount_percent", "uses_count", "max_uses", "valid_until", "is_active")
    list_filter = ("is_active",)
    search_fields = ("code",)


@admin.register(CryptoPayment)
class CryptoPaymentAdmin(admin.ModelAdmin):
    list_display = ("user", "plan_interval", "expected_amount_usdt", "status", "expires_at", "confirmed_at")
    list_filter = ("status", "plan_interval")
    search_fields = ("user__username", "tx_hash")
    raw_id_fields = ("user", "promo_code")
