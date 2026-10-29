import logging
import smtplib

from django.conf import settings
from django.contrib import admin
from django.contrib import messages
from django.core.mail import send_mail
from django.db import models
from django.utils import timezone
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
        previous_state = None
        if change:
            previous_state = Report.objects.values(
                "status", "review_status"
            ).get(pk=obj.pk)

        super().save_model(request, obj, form, change)

        if previous_state is not None:
            self._notify_report_update(
                request,
                obj,
                previous_state["status"],
                previous_state["review_status"],
            )

    def _notify_report_update(
        self, request, report, previous_status, previous_review_status
    ):
        if not report.contact_email or not report.email_verified:
            return

        changes = []
        if previous_review_status != report.review_status:
            previous_review_label = dict(Report.ReviewStatus.choices).get(
                previous_review_status, previous_review_status
            )
            changes.append(
                f"La revisión cambió de «{previous_review_label}» "
                f"a «{report.get_review_status_display()}»."
            )
        if previous_status != report.status:
            previous_status_label = dict(Report.Status.choices).get(
                previous_status, previous_status
            )
            changes.append(
                f"El estado cambió de «{previous_status_label}» "
                f"a «{report.get_status_display()}»."
            )
        if not changes:
            return

        changes_text = "\n".join(changes)
        destination = (
            "reports:my_reports" if report.submitted_by_id else "reports:map"
        )
        status_url = request.build_absolute_uri(reverse(destination))
        subject = "Actualización de tu reporte - Pehuajó Reporta"
        message = (
            f"Hubo una actualización en tu reporte de "
            f"{report.get_category_display().lower()} (#{report.pk}):\n\n"
            f"{changes_text}\n\n"
            f"Podés consultar el seguimiento en: {status_url}\n"
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
        to_approve = eligible.exclude(review_status=Report.ReviewStatus.APPROVED)
        previous_states = {
            report.pk: (report.status, report.review_status)
            for report in to_approve
        }
        updated = to_approve.update(
            review_status=Report.ReviewStatus.APPROVED,
            updated_at=timezone.now(),
        )
        self._notify_bulk_review_updates(request, eligible.model, previous_states)
        skipped = queryset.count() - eligible.count()
        self.message_user(request, f"{updated} reportes aprobados para publicar.")
        if skipped:
            self.message_user(
                request,
                f"{skipped} reportes no se aprobaron porque su email no está confirmado.",
                level="warning",
            )

    @admin.action(description="Rechazar reportes seleccionados")
    def reject_reports(self, request, queryset):
        to_reject = queryset.exclude(review_status=Report.ReviewStatus.REJECTED)
        previous_states = {
            report.pk: (report.status, report.review_status)
            for report in to_reject
        }
        updated = to_reject.update(
            review_status=Report.ReviewStatus.REJECTED,
            updated_at=timezone.now(),
        )
        self._notify_bulk_review_updates(request, queryset.model, previous_states)
        self.message_user(request, f"{updated} reportes rechazados.")

    def _notify_bulk_review_updates(self, request, report_model, previous_states):
        updated_reports = report_model.objects.filter(pk__in=previous_states).select_related(
            "submitted_by"
        )
        for report in updated_reports:
            previous_status, previous_review_status = previous_states[report.pk]
            self._notify_report_update(
                request, report, previous_status, previous_review_status
            )
