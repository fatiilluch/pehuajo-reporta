import logging
import smtplib

from django.conf import settings
from django.contrib import admin
from django.contrib import messages
from django.core.mail import send_mail
from django.db import models
from django.urls import reverse

from .models import Report

logger = logging.getLogger(__name__)


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "category",
        "status",
        "review_status",
        "email_verified",
        "contact_name",
        "contact_email",
        "created_at",
    )
    list_filter = ("review_status", "status", "category", "created_at")
    search_fields = ("description", "contact_name", "contact_email")
    readonly_fields = ("email_verified", "created_at", "updated_at")
    actions = ("approve_reports", "reject_reports")

    def save_model(self, request, obj, form, change):
        previous_status = None
        if change:
            previous_status = Report.objects.values_list("status", flat=True).get(
                pk=obj.pk
            )

        super().save_model(request, obj, form, change)

        if previous_status is not None and previous_status != obj.status:
            self._notify_status_change(request, obj, previous_status)

    def _notify_status_change(self, request, report, previous_status):
        if not report.contact_email or not report.email_verified:
            return

        map_url = request.build_absolute_uri(reverse("reports:map"))
        subject = "Actualización de tu reporte - Pehuajó Reporta"
        previous_status_label = dict(Report.Status.choices).get(
            previous_status, previous_status
        )
        message = (
            f"El estado de tu reporte de {report.get_category_display().lower()} "
            f"cambió de «{previous_status_label}» "
            f"a «{report.get_status_display()}».\n\n"
            f"Podés consultar el mapa público en: {map_url}\n"
        )
        try:
            sent = send_mail(
                subject,
                message,
                settings.DEFAULT_FROM_EMAIL,
                [report.contact_email],
                fail_silently=False,
            )
            if sent != 1:
                raise smtplib.SMTPException("El backend de correo no aceptó el mensaje.")
        except (smtplib.SMTPException, OSError):
            logger.exception(
                "No se pudo enviar el aviso de cambio de estado del reporte %s",
                report.pk,
            )
            self.message_user(
                request,
                "El estado se guardó, pero no pudimos enviar el aviso por email.",
                level=messages.ERROR,
            )
            return

        self.message_user(request, "Se envió el aviso de cambio de estado por email.")

    @admin.action(description="Aprobar reportes seleccionados para publicar")
    def approve_reports(self, request, queryset):
        eligible = queryset.filter(
            models.Q(contact_email="") | models.Q(email_verified=True)
        )
        updated = eligible.update(review_status=Report.ReviewStatus.APPROVED)
        skipped = queryset.count() - updated
        self.message_user(request, f"{updated} reportes aprobados para publicar.")
        if skipped:
            self.message_user(
                request,
                f"{skipped} reportes no se aprobaron porque su email no está confirmado.",
                level="warning",
            )

    @admin.action(description="Rechazar reportes seleccionados")
    def reject_reports(self, request, queryset):
        updated = queryset.update(review_status=Report.ReviewStatus.REJECTED)
        self.message_user(request, f"{updated} reportes rechazados.")
