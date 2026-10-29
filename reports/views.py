import hashlib
import json
import logging
import math
import re
import smtplib
import time
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from PIL import Image
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
from django.contrib.admin.views.decorators import staff_member_required
from django.core import signing
from django.core.cache import cache
from django.core.mail import send_mail
from django.db.models import Q
from django.http import FileResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET

from .forms import (
    MyReportsFilterForm,
    RegistrationForm,
    ReportForm,
    ResendAccountVerificationForm,
)
from .models import PendingAccountVerification, Report

logger = logging.getLogger(__name__)
EMAIL_CONFIRMATION_MAX_AGE = 60 * 60 * 48


def _send_account_verification_email(request, user):
    token = signing.dumps(
        {"user_id": user.pk, "email": user.email},
        salt="reports.account-email-confirmation",
    )
    confirmation_url = request.build_absolute_uri(
        reverse("reports:verify_account_email", args=[token])
    )
    sent = send_mail(
        "Confirmá tu cuenta - Pehuajó Reporta",
        (
            "Para activar tu cuenta de Pehuajó Reporta, abrí este enlace:\n\n"
            f"{confirmation_url}\n\n"
            "El enlace vence en 48 horas."
        ),
        settings.DEFAULT_FROM_EMAIL,
        [user.email],
        fail_silently=False,
    )
    if sent != 1:
        raise smtplib.SMTPException("El backend de correo no aceptó el mensaje.")


def _notify_team_of_report(request, report):
    recipients = settings.REPORT_NOTIFICATION_EMAILS
    if not recipients:
        logger.warning(
            "No se notificó al equipo el reporte %s: "
            "DJANGO_REPORT_NOTIFICATION_EMAILS no está configurado.",
            report.pk,
        )
        return

    report_url = request.build_absolute_uri(
        reverse("admin:reports_report_change", args=[report.pk])
    )
    address = report.location_address or (
        f"{report.latitude}, {report.longitude}"
    )
    message = (
        f"Se recibió el reporte #{report.pk} de {report.contact_name or 'una persona'}.\n\n"
        f"Categoría: {report.get_category_display()}\n"
        f"Ubicación: {address}\n"
        f"Descripción: {report.description}\n\n"
        f"Revisar el reporte en el panel: {report_url}\n"
    )
    try:
        sent = send_mail(
            f"Nuevo reporte #{report.pk} - Pehuajó Reporta",
            message,
            settings.DEFAULT_FROM_EMAIL,
            list(recipients),
            fail_silently=False,
        )
        if sent != 1:
            raise smtplib.SMTPException("El backend de correo no aceptó el mensaje.")
    except (smtplib.SMTPException, OSError):
        logger.exception("No se pudo notificar al equipo sobre el reporte %s", report.pk)


class RoleBasedLoginView(LoginView):
    template_name = "reports/login.html"
    redirect_authenticated_user = True

    def get_redirect_url(self):
        return ""

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["google_oauth_enabled"] = settings.GOOGLE_OAUTH_ENABLED
        return context

    def get_success_url(self):
        if self.request.user.is_staff:
            return reverse("admin:index")
        return reverse("reports:home")


def register(request):
    if request.user.is_authenticated:
        return redirect("reports:home")

    if request.method == "POST":
        form = RegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            PendingAccountVerification.objects.create(user=user)
            try:
                _send_account_verification_email(request, user)
            except (smtplib.SMTPException, OSError):
                logger.exception("No se pudo enviar la verificación de la cuenta %s", user.pk)
                user.delete()
                form.add_error(
                    None,
                    "No pudimos enviar el email de confirmación. "
                    "Revisá los datos o intentá de nuevo más tarde.",
                )
            else:
                return render(
                    request,
                    "reports/email_confirmation.html",
                    {
                        "account_confirmation": True,
                        "account_email": user.email,
                        "sent": True,
                    },
                )
    else:
        form = RegistrationForm()

    return render(
        request,
        "reports/register.html",
        {
            "form": form,
            "google_oauth_enabled": settings.GOOGLE_OAUTH_ENABLED,
        },
    )


def resend_account_verification(request):
    if request.method == "POST":
        form = ResendAccountVerificationForm(request.POST)
        if form.is_valid():
            verification = PendingAccountVerification.objects.select_related("user").filter(
                user__email__iexact=form.cleaned_data["email"],
                user__is_active=False,
            ).first()
            if verification:
                try:
                    _send_account_verification_email(request, verification.user)
                except (smtplib.SMTPException, OSError):
                    logger.exception(
                        "No se pudo reenviar la verificación de la cuenta %s",
                        verification.user.pk,
                    )
            return render(
                request,
                "reports/email_confirmation.html",
                {"account_confirmation": True, "sent": True},
            )
    else:
        form = ResendAccountVerificationForm()

    return render(
        request,
        "reports/email_confirmation.html",
        {"account_confirmation": True, "resend_form": form},
    )


def verify_account_email(request, token):
    try:
        data = signing.loads(
            token,
            salt="reports.account-email-confirmation",
            max_age=EMAIL_CONFIRMATION_MAX_AGE,
        )
    except signing.SignatureExpired:
        return render(
            request,
            "reports/email_confirmation.html",
            {
                "account_confirmation": True,
                "error": "El enlace venció. Podés pedir uno nuevo.",
                "resend_url": reverse("reports:resend_account_verification"),
            },
            status=400,
        )
    except signing.BadSignature:
        return HttpResponseBadRequest("El enlace de confirmación no es válido.")

    verification = PendingAccountVerification.objects.select_related("user").filter(
        user_id=data.get("user_id"),
        user__email=data.get("email"),
        user__is_active=False,
    ).first()
    if verification is None:
        user = get_user_model().objects.filter(
            pk=data.get("user_id"),
            email=data.get("email"),
            is_active=True,
        ).first()
        if user:
            return render(
                request,
                "reports/email_confirmation.html",
                {"account_confirmation": True, "confirmed": True},
            )
        return HttpResponseBadRequest("El enlace de confirmación no es válido.")

    user = verification.user
    if request.method == "POST":
        user.is_active = True
        user.save(update_fields=("is_active",))
        verification.delete()
        return render(
            request,
            "reports/email_confirmation.html",
            {"account_confirmation": True, "confirmed": True},
        )

    return render(
        request,
        "reports/email_confirmation.html",
        {"account_confirmation": True},
    )


def create_report(request):
    account_user = request.user if request.user.is_authenticated else None
    if request.method == "POST":
        form = ReportForm(request.POST, request.FILES, account_user=account_user)
        if form.is_valid():
            report = form.save(commit=False)
            if account_user:
                report.contact_name = (
                    account_user.get_full_name().strip() or account_user.get_username()
                )
                report.contact_email = account_user.email
                report.email_verified = bool(account_user.email)
                report.review_status = Report.ReviewStatus.PENDING
                report.submitted_by = account_user
                report.save()
                _notify_team_of_report(request, report)
                return redirect("reports:thank_you")

            if report.contact_email:
                report.email_verified = False
                report.review_status = Report.ReviewStatus.PENDING_EMAIL
                report.save()
                token = signing.dumps(
                    {"report_id": report.pk, "email": report.contact_email},
                    salt="reports.email-confirmation",
                )
                confirmation_url = request.build_absolute_uri(
                    reverse("reports:verify_email", args=[token])
                )
                try:
                    sent = send_mail(
                        "Confirmá tu email - Pehuajó Reporta",
                        (
                            "Para confirmar el email de tu reporte, abrí este enlace "
                            "y confirmá el envío:\n\n"
                            f"{confirmation_url}\n\n"
                            "El enlace vence en 48 horas."
                        ),
                        settings.DEFAULT_FROM_EMAIL,
                        [report.contact_email],
                        fail_silently=False,
                    )
                    if sent != 1:
                        raise smtplib.SMTPException(
                            "El backend de correo no aceptó el mensaje."
                        )
                except (smtplib.SMTPException, OSError):
                    logger.exception(
                        "No se pudo enviar la confirmación del reporte %s",
                        report.pk,
                    )
                    if report.photo:
                        report.photo.delete(save=False)
                    report.delete()
                    form.add_error(
                        None,
                        "No pudimos enviar el email de confirmación. "
                        "Revisá la dirección o intentá de nuevo más tarde.",
                    )
                    return render(request, "reports/report_form.html", {"form": form})
                return render(request, "reports/thank_you.html", {"email_sent": True})

            report.email_verified = False
            report.save()
            _notify_team_of_report(request, report)
            return redirect("reports:thank_you")
    else:
        form = ReportForm(account_user=account_user)

    return render(request, "reports/report_form.html", {"form": form})


def home(request):
    return render(request, "reports/home.html")


def thank_you(request):
    return render(request, "reports/thank_you.html")


@login_required
def my_reports(request):
    report_list = Report.objects.filter(submitted_by=request.user)
    filter_form = MyReportsFilterForm(request.GET or None)
    if request.GET:
        if filter_form.is_valid():
            filters = filter_form.cleaned_data
            if filters["search"]:
                report_list = report_list.filter(
                    Q(description__icontains=filters["search"])
                    | Q(location_address__icontains=filters["search"])
                )
            if filters["category"]:
                report_list = report_list.filter(category=filters["category"])
            if filters["review_status"]:
                report_list = report_list.filter(
                    review_status=filters["review_status"]
                )
            if filters["status"]:
                report_list = report_list.filter(status=filters["status"])
        else:
            report_list = report_list.none()

    return render(
        request,
        "reports/my_reports.html",
        {"reports": report_list, "filter_form": filter_form},
    )


@login_required
def my_report_photo(request, report_id):
    report = get_object_or_404(
        Report.objects.filter(submitted_by=request.user).exclude(photo=""),
        pk=report_id,
    )
    with report.photo.open("rb") as photo_file:
        with Image.open(photo_file) as image:
            image_format = image.format

    content_types = {
        "BMP": "image/bmp",
        "GIF": "image/gif",
        "JPEG": "image/jpeg",
        "PNG": "image/png",
        "WEBP": "image/webp",
    }
    content_type = content_types.get(image_format)
    if content_type is None:
        return HttpResponseBadRequest("El formato de la foto no está soportado.")

    response = FileResponse(report.photo.open("rb"), content_type=content_type)
    response["Content-Disposition"] = "inline"
    response["X-Content-Type-Options"] = "nosniff"
    return response


def public_map(request):
    reports = list(
        Report.objects.filter(review_status=Report.ReviewStatus.APPROVED)
        .filter(Q(contact_email="") | Q(email_verified=True))
        .values(
            "latitude",
            "longitude",
            "location_address",
            "category",
            "status",
            "description",
        )
    )
    return render(request, "reports/map.html", {"reports": reports})


@staff_member_required
def management_map(request):
    reports = list(
        Report.objects.values(
            "latitude",
            "longitude",
            "location_address",
            "category",
            "status",
            "review_status",
            "contact_name",
            "description",
            "email_verified",
        )
    )
    return render(request, "reports/management_map.html", {"reports": reports})


def verify_report_email(request, token):
    try:
        data = signing.loads(
            token,
            salt="reports.email-confirmation",
            max_age=EMAIL_CONFIRMATION_MAX_AGE,
        )
    except signing.SignatureExpired:
        return render(
            request,
            "reports/email_confirmation.html",
            {"error": "El enlace venció. Volvé a cargar el reporte para recibir uno nuevo."},
            status=400,
        )
    except signing.BadSignature:
        return HttpResponseBadRequest("El enlace de confirmación no es válido.")

    report = get_object_or_404(
        Report,
        pk=data.get("report_id"),
        contact_email=data.get("email"),
    )
    if request.method == "POST":
        if not report.email_verified:
            report.email_verified = True
            if report.review_status == Report.ReviewStatus.PENDING_EMAIL:
                report.review_status = Report.ReviewStatus.PENDING
            report.save(update_fields=("email_verified", "review_status", "updated_at"))
            _notify_team_of_report(request, report)
        return render(
            request,
            "reports/email_confirmation.html",
            {"confirmed": True},
        )

    return render(
        request,
        "reports/email_confirmation.html",
        {"confirmed": report.email_verified},
    )


def _intersection_streets(query):
    parts = re.split(
        r"\s+(?:y|and|esq\.?|esquina(?:\s+con)?|con)\s+|[&/]",
        query,
        maxsplit=1,
        flags=re.IGNORECASE,
    )
    if len(parts) != 2:
        return None
    streets = tuple(part.strip(" ,.") for part in parts)
    if any(len(street) < 2 for street in streets):
        return None
    return streets


def _nominatim_search(query, include_geometry=False):
    params = urlencode(
        {
            "q": f"{query}, Pehuajó, Buenos Aires, Argentina",
            "format": "jsonv2",
            "limit": 5,
            "countrycodes": "ar",
            **({"polygon_geojson": 1} if include_geometry else {}),
        }
    )
    request_url = f"https://nominatim.openstreetmap.org/search?{params}"
    http_request = Request(
        request_url,
        headers={"User-Agent": "PehuajoReporta/0.1 (address search)"},
    )
    with urlopen(http_request, timeout=8) as response:
        return json.loads(response.read())


def _nominatim_reverse(latitude, longitude):
    params = urlencode(
        {
            "lat": latitude,
            "lon": longitude,
            "format": "jsonv2",
            "zoom": 18,
            "addressdetails": 1,
        }
    )
    request_url = f"https://nominatim.openstreetmap.org/reverse?{params}"
    http_request = Request(
        request_url,
        headers={"User-Agent": "PehuajoReporta/0.1 (reverse address lookup)"},
    )
    with urlopen(http_request, timeout=8) as response:
        return json.loads(response.read())


def _reverse_address_label(payload):
    address = payload.get("address")
    if not isinstance(address, dict):
        return payload["display_name"]

    road = (
        address.get("road")
        or address.get("pedestrian")
        or address.get("footway")
        or address.get("residential")
    )
    house_number = address.get("house_number")
    components = []
    if road:
        components.append(f"{road} {house_number}" if house_number else road)
    seen = {component.casefold() for component in components}
    for component in (
        address.get("neighbourhood"),
        address.get("suburb"),
        address.get("city_district"),
        address.get("city"),
        address.get("town"),
        address.get("village"),
        address.get("municipality"),
        address.get("county"),
        address.get("state_district"),
        address.get("state"),
        address.get("postcode"),
        address.get("country"),
    ):
        if component and component.casefold() not in seen:
            components.append(component)
            seen.add(component.casefold())
    return ", ".join(components) if components else payload["display_name"]


def _segment_intersection(first_start, first_end, second_start, second_end):
    lat0 = math.radians(
        (first_start[0] + first_end[0] + second_start[0] + second_end[0]) / 4
    )

    def project(point):
        return (point[1] * math.cos(lat0), point[0])

    p, p_end, q, q_end = map(
        project,
        (first_start, first_end, second_start, second_end),
    )
    r = (p_end[0] - p[0], p_end[1] - p[1])
    s = (q_end[0] - q[0], q_end[1] - q[1])
    denominator = r[0] * s[1] - r[1] * s[0]
    if abs(denominator) < 1e-12:
        return None

    q_minus_p = (q[0] - p[0], q[1] - p[1])
    t = (q_minus_p[0] * s[1] - q_minus_p[1] * s[0]) / denominator
    u = (q_minus_p[0] * r[1] - q_minus_p[1] * r[0]) / denominator
    if 0 <= t <= 1 and 0 <= u <= 1:
        x = p[0] + t * r[0]
        y = p[1] + t * r[1]
        return (y, x / math.cos(lat0))
    return None


def _find_street_intersections(query):
    streets = _intersection_streets(query)
    if not streets:
        return None

    street_a, street_b = streets
    first_payload = _nominatim_search(street_a, include_geometry=True)
    time.sleep(1.05)
    second_payload = _nominatim_search(street_b, include_geometry=True)

    def normalize_name(name):
        decomposed = unicodedata.normalize("NFKD", name.casefold())
        ascii_name = "".join(char for char in decomposed if not unicodedata.combining(char))
        return " ".join(re.findall(r"[a-z0-9]+", ascii_name))

    def street_lines(payload, street_name):
        needle_tokens = set(normalize_name(street_name).split())
        minimum_matches = min(2, len(needle_tokens))
        lines = []
        if not isinstance(payload, list):
            raise ValueError("Nominatim devolvió una respuesta con formato inesperado")
        for result in payload:
            if result.get("category") != "highway":
                continue
            name_tokens = set(normalize_name(result.get("name", "")).split())
            if len(needle_tokens & name_tokens) < minimum_matches:
                continue
            geometry = result.get("geojson", {})
            coordinates = geometry.get("coordinates", [])
            if geometry.get("type") == "LineString":
                coordinate_lines = [coordinates]
            elif geometry.get("type") == "MultiLineString":
                coordinate_lines = coordinates
            else:
                continue
            for coordinate_line in coordinate_lines:
                points = [
                    (float(latitude), float(longitude))
                    for longitude, latitude in coordinate_line
                ]
                if len(points) > 1:
                    lines.append(points)
        return lines

    first_ways = street_lines(first_payload, street_a)
    second_ways = street_lines(second_payload, street_b)
    if not first_ways or not second_ways:
        return []

    intersections = {}
    for first_way in first_ways:
        for second_way in second_ways:
            for first_start, first_end in zip(first_way, first_way[1:]):
                for second_start, second_end in zip(second_way, second_way[1:]):
                    point = _segment_intersection(
                        first_start,
                        first_end,
                        second_start,
                        second_end,
                    )
                    if point:
                        key = (round(point[0], 6), round(point[1], 6))
                        intersections[key] = {
                            "label": f"Esquina de {street_a} y {street_b}, Pehuajó",
                            "latitude": f"{key[0]:.6f}",
                            "longitude": f"{key[1]:.6f}",
                        }
    return list(intersections.values())[:5]


@require_GET
def search_address(request):
    query = request.GET.get("q", "").strip()
    if len(query) < 3 or len(query) > 200:
        return JsonResponse(
            {"error": "Ingresá una dirección de entre 3 y 200 caracteres."},
            status=400,
        )

    query_hash = hashlib.sha256(query.casefold().encode("utf-8")).hexdigest()
    cache_key = f"address-search:{query_hash}"
    results = cache.get(cache_key)
    if results is not None:
        return JsonResponse({"results": results})

    streets = _intersection_streets(query)
    if streets:
        if not cache.add("nominatim-intersection-last-request", True, timeout=3):
            return JsonResponse(
                {"error": "Esperá unos segundos antes de buscar otra intersección."},
                status=429,
                headers={"Retry-After": "3"},
            )
        try:
            results = _find_street_intersections(query)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
            logger.exception("No se pudo completar la búsqueda de la intersección")
            return JsonResponse(
                {"error": "No pudimos buscar esa intersección. Intentá de nuevo."},
                status=502,
            )
        cache.set(cache_key, results, timeout=60 * 60 * 24)
        return JsonResponse({"results": results})

    if not cache.add("nominatim-last-request", True, timeout=1):
        return JsonResponse(
            {"error": "Esperá un segundo antes de hacer otra búsqueda."},
            status=429,
            headers={"Retry-After": "1"},
        )

    try:
        payload = _nominatim_search(query)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError):
        logger.exception("Falló la búsqueda de direcciones en Nominatim")
        return JsonResponse(
            {"error": "No pudimos buscar esa dirección. Intentá de nuevo."},
            status=502,
        )
    if not isinstance(payload, list):
        logger.error("Nominatim devolvió una respuesta con formato inesperado")
        return JsonResponse(
            {"error": "El servicio de direcciones devolvió una respuesta inesperada."},
            status=502,
        )

    results = [
        {
            "label": result["display_name"],
            "latitude": result["lat"],
            "longitude": result["lon"],
        }
        for result in payload
        if "display_name" in result and "lat" in result and "lon" in result
    ]
    cache.set(cache_key, results, timeout=60 * 60 * 24)
    return JsonResponse({"results": results})


@require_GET
def reverse_address(request):
    try:
        latitude = float(request.GET.get("lat", ""))
        longitude = float(request.GET.get("lon", ""))
    except ValueError:
        return JsonResponse({"error": "La ubicación no es válida."}, status=400)
    if (
        not math.isfinite(latitude)
        or not math.isfinite(longitude)
        or not -90 <= latitude <= 90
        or not -180 <= longitude <= 180
    ):
        return JsonResponse({"error": "La ubicación no es válida."}, status=400)

    cache_key = f"reverse-address:v2:{latitude:.6f}:{longitude:.6f}"
    cached_result = cache.get(cache_key)
    if cached_result is not None:
        return JsonResponse(cached_result)
    if not cache.add("nominatim-last-request", True, timeout=1):
        return JsonResponse(
            {"error": "Esperá un segundo antes de buscar otra dirección."},
            status=429,
            headers={"Retry-After": "1"},
        )

    try:
        payload = _nominatim_reverse(latitude, longitude)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError):
        logger.exception(
            "Falló la búsqueda de dirección para la ubicación seleccionada"
        )
        return JsonResponse(
            {"error": "No pudimos obtener la dirección de ese punto."},
            status=502,
        )
    if not isinstance(payload, dict):
        logger.error("Nominatim devolvió una respuesta inversa con formato inesperado")
        return JsonResponse(
            {"error": "El servicio de direcciones devolvió una respuesta inesperada."},
            status=502,
        )
    if not payload.get("display_name"):
        return JsonResponse(
            {"error": "No encontramos una dirección para ese punto."},
            status=404,
        )

    result = {"address": _reverse_address_label(payload)}
    cache.set(cache_key, result, timeout=60 * 60 * 24)
    return JsonResponse(result)
