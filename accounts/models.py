from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        OPERATOR = "operator", "Operator"
        VIEWER = "viewer", "Viewer"

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.VIEWER)

    class Meta:
        db_table = "accounts_user"

    def is_admin(self) -> bool:
        return self.role == self.Role.ADMIN

    def is_operator(self) -> bool:
        return self.role in (self.Role.ADMIN, self.Role.OPERATOR)
