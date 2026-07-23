from django.test import TestCase

from accounts.models import User
from billing.models import CustomerProfile


class CustomerProfileTest(TestCase):
    def test_default_status_is_free(self):
        user = User.objects.create_user(username="cliente2", password="x", role=User.Role.CUSTOMER)
        profile = CustomerProfile.objects.create(user=user)
        self.assertEqual(profile.status, CustomerProfile.Status.FREE)
        self.assertFalse(profile.email_verified)
        self.assertIsNone(profile.current_period_end)
        self.assertEqual(user.customer_profile, profile)

    def test_one_profile_per_user(self):
        user = User.objects.create_user(username="cliente3", password="x", role=User.Role.CUSTOMER)
        CustomerProfile.objects.create(user=user)
        with self.assertRaises(Exception):
            CustomerProfile.objects.create(user=user)
