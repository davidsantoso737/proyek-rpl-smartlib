from django.contrib.auth.models import (
    AbstractBaseUser,
    BaseUserManager,
    PermissionsMixin,
)
from django.core.exceptions import ValidationError
from django.db import models


class UserManager(BaseUserManager):
    def create_user(self, username, password=None, **extra_fields):
        if not username:
            raise ValueError("Username wajib diisi.")
        user = self.model(username=username, **extra_fields)
        user.set_password(password)
        user.full_clean()
        user.save(using=self._db)
        return user

    def create_superuser(self, username, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("role", User.Role.PUSTAKAWAN)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser harus memiliki is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser harus memiliki is_superuser=True.")
        return self.create_user(username, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    class Role(models.TextChoices):
        SISWA = "SISWA", "Siswa"
        GURU = "GURU", "Guru"
        PUSTAKAWAN = "PUSTAKAWAN", "Pustakawan"

    username = models.CharField(max_length=150, unique=True)
    # Password & last_login diwarisi dari AbstractBaseUser (hashing Django,
    # tidak ada field password plaintext custom).
    role = models.CharField(max_length=20, choices=Role.choices)
    full_name = models.CharField(max_length=255)
    nis = models.CharField(max_length=30, unique=True, null=True, blank=True)
    nip = models.CharField(max_length=30, unique=True, null=True, blank=True)
    email = models.EmailField(null=True, blank=True)
    phone = models.CharField(max_length=30, null=True, blank=True)
    # "class" adalah keyword Python & nama kolom DB yang diminta,
    # sehingga dipetakan lewat db_column.
    student_class = models.CharField(
        max_length=50, null=True, blank=True, db_column="class"
    )
    department = models.CharField(max_length=100, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    # P1 — Account Provisioning foundation: akun hasil provisioning
    # (SISWA/GURU) wajib mengganti password saat login pertama.
    # Default False agar existing users tetap kompatibel.
    must_change_password = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "username"
    REQUIRED_FIELDS = ["full_name", "role"]

    class Meta:
        db_table = "users"

    def __str__(self):
        return f"{self.username} ({self.role})"

    def clean(self):
        super().clean()
        errors = {}
        if self.role == self.Role.SISWA:
            if not self.nis:
                errors["nis"] = "Siswa wajib memiliki NIS."
            if self.nip:
                errors["nip"] = "Siswa tidak boleh memiliki NIP."
        elif self.role == self.Role.GURU:
            if not self.nip:
                errors["nip"] = "Guru wajib memiliki NIP."
            if self.nis:
                errors["nis"] = "Guru tidak boleh memiliki NIS."
        # Pustakawan: boleh tanpa NIS/NIP, boleh memiliki salah satu.
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
