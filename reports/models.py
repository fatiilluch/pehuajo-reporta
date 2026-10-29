from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q


class Report(models.Model):
    class Category(models.TextChoices):
        POTHOLE = "pothole", "Bache"
        LIGHTING = "lighting", "Alumbrado"
        WASTE = "waste", "Residuos"
        SIDEWALK = "sidewalk", "Vereda"
        OTHER = "other", "Otro"

    class Status(models.TextChoices):
        RECEIVED = "received", "Recibido"
        IN_PROGRESS = "in_progress", "En curso"
        RESOLVED = "resolved", "Resuelto"

    class ReviewStatus(models.TextChoices):
        PENDING = "pending", "Pendiente de revisión"
        PENDING_EMAIL = "pending_email", "Pendiente de confirmar email"
        APPROVED = "approved", "Aprobado para publicar"
        REJECTED = "rejected", "Rechazado"

    contact_name = models.CharField("nombre de contacto", max_length=120, blank=True)
    contact_email = models.EmailField("email de contacto", blank=True)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="enviado por",
        related_name="submitted_reports",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    category = models.CharField("categoría", max_length=20, choices=Category.choices)
    description = models.TextField("descripción")
    latitude = models.DecimalField(
        "latitud",
        max_digits=8,
        decimal_places=6,
        validators=[MinValueValidator(-90), MaxValueValidator(90)],
    )
    longitude = models.DecimalField(
        "longitud",
        max_digits=9,
        decimal_places=6,
        validators=[MinValueValidator(-180), MaxValueValidator(180)],
    )
    location_address = models.CharField("dirección aproximada", max_length=300, blank=True)
    photo = models.ImageField("foto", upload_to="reports/%Y/%m/", blank=True)
    email_verified = models.BooleanField("email confirmado", default=False)
    status = models.CharField(
        "estado",
        max_length=20,
        choices=Status.choices,
        default=Status.RECEIVED,
    )
    review_status = models.CharField(
        "revisión",
        max_length=20,
        choices=ReviewStatus.choices,
        default=ReviewStatus.PENDING,
    )
    created_at = models.DateTimeField("creado", auto_now_add=True)
    updated_at = models.DateTimeField("actualizado", auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(contact_name__gt="") | Q(contact_email__gt=""),
                name="reports_report_has_contact",
            )
        ]
        verbose_name = "reporte"
        verbose_name_plural = "reportes"

    def clean(self):
        super().clean()
        if not (self.contact_name or "").strip() and not (self.contact_email or "").strip():
            raise ValidationError("Ingresá al menos un nombre o un email de contacto.")
        if (
            self.review_status == self.ReviewStatus.APPROVED
            and self.contact_email
            and not self.email_verified
        ):
            raise ValidationError("No se puede publicar un reporte con email sin confirmar.")

    def __str__(self):
        return f"{self.get_category_display()} - {self.get_status_display()}"


class PendingAccountVerification(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="pending_email_verification",
    )
    created_at = models.DateTimeField(auto_now_add=True)
