from django.test import TestCase

from accounts.models import User


class UserRoleTest(TestCase):
    def test_customer_role_exists(self):
        user = User.objects.create_user(username="cliente1", password="x", role=User.Role.CUSTOMER)
        self.assertTrue(user.is_customer())
        self.assertFalse(user.is_admin())
        self.assertFalse(user.is_operator())

    def test_staff_roles_are_not_customer(self):
        user = User.objects.create_user(username="staff1", password="x", role=User.Role.VIEWER)
        self.assertFalse(user.is_customer())
