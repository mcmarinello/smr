from django.urls import path

from .views import SmartMoneySignalView

urlpatterns = [
    path(
        "v1/smart-money-signal/",
        SmartMoneySignalView.as_view(),
        name="smart_money_signal",
    ),
]