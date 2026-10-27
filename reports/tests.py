import json
import re
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core import mail
from django.core.cache import cache
from django.test import TestCase
from django.test import override_settings
from django.urls import reverse

from config.settings import _get_or_create_development_secret_key
from .models import Report
from .views import _intersection_streets, _segment_intersection


class ReportModelTests(TestCase):
    def setUp(self):
        self.report = Report(
            contact_name="Ana",
            category=Report.Category.POTHOLE,
            description="Hay un bache en la esquina.",
            latitude="-35.836000",
            longitude="-61.870000",
        )

    def test_new_report_is_private_and_received_by_default(self):
        self.report.full_clean()
        self.report.save()

        self.assertEqual(self.report.status, Report.Status.RECEIVED)
        self.assertEqual(self.report.review_status, Report.ReviewStatus.PENDING)

    def test_report_requires_a_name_or_email(self):
        self.report.contact_name = ""

        with self.assertRaises(ValidationError):
            self.report.full_clean()

    def test_report_can_use_email_without_name(self):
        self.report.contact_name = ""
        self.report.contact_email = "ana@example.com"

        self.report.full_clean()

    def test_latitude_must_be_within_geographic_bounds(self):
        self.report.latitude = "91.000000"

        with self.assertRaises(ValidationError):
            self.report.full_clean()


class PublicReportFormTests(TestCase):
    def setUp(self):
        self.url = reverse("reports:create")
        self.valid_data = {
            "contact_name": "Ana",
            "contact_email": "",
            "category": Report.Category.POTHOLE,
            "description": "Hay un bache en la esquina.",
            "latitude": "-35.836000",
            "longitude": "-61.870000",
            "location_address": "San Martín 123, Pehuajó",
        }

    def test_homepage_offers_report_and_map_options(self):
        response = self.client.get(reverse("reports:home"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "reports/base.html")
        self.assertContains(response, "Enviar un reporte")
        self.assertContains(response, "Ver reportes existentes")
        self.assertContains(response, reverse("reports:create"))
        self.assertContains(response, reverse("reports:map"))

    def test_form_is_available(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "reports/base.html")
        self.assertContains(response, "Reportá un problema")
        self.assertTrue(response.context["form"]["latitude"].is_hidden)
        self.assertTrue(response.context["form"]["longitude"].is_hidden)
        self.assertTrue(response.context["form"]["location_address"].is_hidden)
        self.assertNotContains(response, "Latitud")
        self.assertNotContains(response, "Longitud")

    def test_thank_you_page_uses_saved_theme_preference(self):
        response = self.client.get(reverse("reports:thank_you"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "reports/base.html")
        self.assertContains(response, "localStorage.getItem(key)")
        self.assertContains(response, ':root[data-theme="dark"]')

    def test_valid_submission_creates_pending_report_and_redirects(self):
        response = self.client.post(self.url, self.valid_data)

        self.assertRedirects(response, reverse("reports:thank_you"))
        report = Report.objects.get()
        self.assertEqual(report.review_status, Report.ReviewStatus.PENDING)
        self.assertEqual(report.status, Report.Status.RECEIVED)
        self.assertEqual(report.location_address, "San Martín 123, Pehuajó")

    def test_submission_requires_map_location(self):
        data = {
            key: value
            for key, value in self.valid_data.items()
            if key not in {"latitude", "longitude"}
        }

        response = self.client.post(self.url, data)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Report.objects.count(), 0)
        self.assertContains(
            response,
            "Marcá la ubicación en el mapa o elegí una dirección antes de enviar.",
        )

    def test_submission_requires_name_or_email(self):
        response = self.client.post(
            self.url,
            {**self.valid_data, "contact_name": ""},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Report.objects.count(), 0)
        self.assertContains(response, "Ingresá al menos un nombre o un email.")

    def test_submission_rejects_invalid_coordinates(self):
        response = self.client.post(
            self.url,
            {**self.valid_data, "latitude": "91"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Report.objects.count(), 0)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_email_report_requires_confirmation_before_team_review(self):
        response = self.client.post(
            self.url,
            {**self.valid_data, "contact_email": "ana@example.com"},
        )

        report = Report.objects.get()
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "reports/thank_you.html")
        self.assertEqual(report.review_status, Report.ReviewStatus.PENDING_EMAIL)
        self.assertFalse(report.email_verified)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("ana@example.com", mail.outbox[0].to)

        token = re.search(r"/verificar-email/([^/\s]+)", mail.outbox[0].body).group(1)
        confirm_url = reverse("reports:verify_email", args=[token])
        self.client.get(confirm_url)
        report.refresh_from_db()
        self.assertFalse(report.email_verified)

        response = self.client.post(confirm_url)
        report.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "reports/base.html")
        self.assertTrue(report.email_verified)
        self.assertEqual(report.review_status, Report.ReviewStatus.PENDING)
        self.assertContains(response, "localStorage.getItem(key)")
        self.assertContains(response, ':root[data-theme="dark"]')
        self.assertContains(response, "class=\"theme-toggle\"")


class AccountLoginTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="vecina",
            password="test-password",
        )
        self.admin = user_model.objects.create_superuser(
            username="equipo",
            email="equipo@example.com",
            password="test-password",
        )
        self.login_url = reverse("reports:login")

    def test_anonymous_header_offers_login(self):
        response = self.client.get(reverse("reports:home"))

        self.assertContains(response, reverse("reports:login"))
        self.assertContains(response, "Ingresar")
        self.assertContains(response, 'title="No iniciaste sesión"')

    def test_regular_user_login_redirects_to_public_home(self):
        response = self.client.post(
            self.login_url,
            {"username": "vecina", "password": "test-password", "next": "/admin/"},
        )

        self.assertRedirects(response, reverse("reports:home"))
        home_response = self.client.get(reverse("reports:home"))
        self.assertNotContains(home_response, "Ingresar")
        self.assertContains(home_response, "Cerrar sesión")
        self.assertContains(home_response, 'aria-label="Cerrar sesión de vecina"')

    def test_staff_login_redirects_to_admin(self):
        response = self.client.post(
            self.login_url,
            {"username": "equipo", "password": "test-password"},
        )

        self.assertRedirects(response, reverse("admin:index"))

    def test_regular_user_cannot_open_admin(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("admin:index"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response["Location"])

    def test_logout_requires_post_and_clears_session(self):
        self.client.force_login(self.user)
        logout_url = reverse("reports:logout")

        get_response = self.client.get(logout_url)
        post_response = self.client.post(logout_url)

        self.assertEqual(get_response.status_code, 405)
        self.assertRedirects(post_response, reverse("reports:home"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_sessions_expire_after_thirty_minutes_of_inactivity(self):
        self.assertEqual(settings.SESSION_COOKIE_AGE, 30 * 60)
        self.assertTrue(settings.SESSION_SAVE_EVERY_REQUEST)
        self.client.force_login(self.user)

        response = self.client.get(reverse("reports:home"))
        refreshed_response = self.client.get(reverse("reports:home"))

        self.assertEqual(
            response.cookies[settings.SESSION_COOKIE_NAME]["max-age"],
            30 * 60,
        )
        self.assertEqual(refreshed_response.context["user"], self.user)

    def test_development_secret_key_is_stable_between_server_starts(self):
        with TemporaryDirectory() as directory:
            secret_path = Path(directory) / ".django_secret_key"

            first_key = _get_or_create_development_secret_key(secret_path)
            second_key = _get_or_create_development_secret_key(secret_path)

        self.assertEqual(first_key, second_key)


class PublicMapTests(TestCase):
    def setUp(self):
        self.url = reverse("reports:map")
        self.approved_report = Report.objects.create(
            contact_name="Contacto privado",
            contact_email="privado@example.com",
            category=Report.Category.POTHOLE,
            description="Descripción privada de prueba.",
            latitude="-35.836000",
            longitude="-61.870000",
            location_address="San Martín 123, Pehuajó",
            review_status=Report.ReviewStatus.APPROVED,
            email_verified=True,
        )
        Report.objects.create(
            contact_name="Otro contacto",
            category=Report.Category.LIGHTING,
            description="Este reporte sigue pendiente.",
            latitude="-35.837000",
            longitude="-61.871000",
        )
        Report.objects.create(
            contact_name="Email sin confirmar",
            contact_email="sin-confirmar@example.com",
            category=Report.Category.WASTE,
            description="No publicar antes de confirmar.",
            latitude="-35.838000",
            longitude="-61.872000",
            review_status=Report.ReviewStatus.APPROVED,
            email_verified=False,
        )

    def test_map_only_contains_approved_public_fields(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "reports/base.html")
        self.assertEqual(
            response.context["reports"],
            [
                {
                    "latitude": Report.objects.get(pk=self.approved_report.pk).latitude,
                    "longitude": Report.objects.get(pk=self.approved_report.pk).longitude,
                    "location_address": "San Martín 123, Pehuajó",
                    "category": Report.Category.POTHOLE,
                    "status": Report.Status.RECEIVED,
                    "description": "Descripción privada de prueba.",
                }
            ],
        )
        self.assertContains(response, "pothole")
        self.assertContains(response, "location_address")
        self.assertContains(response, "tooltipopen")
        self.assertContains(response, "retryAfter")
        self.assertContains(response, "bindTooltip")
        self.assertEqual(
            response.context["reports"][0]["description"],
            "Descripción privada de prueba.",
        )
        self.assertNotContains(response, "privado@example.com")
        self.assertNotContains(response, "Este reporte sigue pendiente.")


class ManagementMapTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.regular_user = user_model.objects.create_user(
            username="vecina",
            password="test-password",
        )
        self.staff_user = user_model.objects.create_user(
            username="equipo",
            password="test-password",
            is_staff=True,
        )
        self.url = reverse("reports:management_map")

    def test_map_requires_staff_access(self):
        anonymous_response = self.client.get(self.url)
        self.client.force_login(self.regular_user)
        regular_user_response = self.client.get(self.url)

        self.assertEqual(anonymous_response.status_code, 302)
        self.assertIn("/admin/login/", anonymous_response["Location"])
        self.assertEqual(regular_user_response.status_code, 302)
        self.assertIn("/admin/login/", regular_user_response["Location"])

    def test_staff_can_open_map_and_sees_header_link(self):
        self.client.force_login(self.staff_user)
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "reports/management_map.html")

        home_response = self.client.get(reverse("reports:home"))
        self.assertContains(home_response, self.url)
        self.assertContains(home_response, "Abrir mapa de gestión")

    def test_staff_map_includes_all_review_statuses_and_requested_details(self):
        review_statuses = (
            Report.ReviewStatus.PENDING,
            Report.ReviewStatus.PENDING_EMAIL,
            Report.ReviewStatus.APPROVED,
            Report.ReviewStatus.REJECTED,
        )
        for index, review_status in enumerate(review_statuses):
            Report.objects.create(
                contact_name=f"Contacto privado {index}",
                contact_email=(
                    "privado@example.com"
                    if review_status == Report.ReviewStatus.PENDING_EMAIL
                    else ""
                ),
                category=Report.Category.POTHOLE,
                description=f"Descripción privada {index}",
                latitude=f"-35.83{index:04d}",
                longitude=f"-61.87{index:04d}",
                location_address=f"Calle {index}, Pehuajó",
                review_status=review_status,
            )

        self.client.force_login(self.staff_user)
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "reports/management_map.html")
        reports = response.context["reports"]
        self.assertEqual(len(reports), len(review_statuses))
        self.assertEqual(
            {report["review_status"] for report in reports},
            set(review_statuses),
        )
        for report in reports:
            self.assertEqual(
                set(report),
                {
                    "latitude",
                    "longitude",
                    "location_address",
                    "category",
                    "status",
                    "review_status",
                    "contact_name",
                    "description",
                    "email_verified",
                },
            )
        self.assertContains(response, "Pendiente de confirmar email")
        self.assertContains(response, "Contacto privado 0")
        self.assertContains(response, "email_verified")
        self.assertNotContains(response, "privado@example.com")
        self.assertIn(
            "Descripción privada 0",
            [report["description"] for report in reports],
        )


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class AdminStatusNotificationTests(TestCase):
    def setUp(self):
        self.admin_user = get_user_model().objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="test-password",
        )
        self.client.force_login(self.admin_user)
        self.report = Report.objects.create(
            contact_name="Ana",
            contact_email="ana@example.com",
            category=Report.Category.POTHOLE,
            description="Hay un bache.",
            latitude="-35.836000",
            longitude="-61.870000",
            email_verified=True,
        )
        self.change_url = reverse("admin:reports_report_change", args=[self.report.pk])
        self.form_data = {
            "contact_name": "Ana",
            "contact_email": "ana@example.com",
            "category": Report.Category.POTHOLE,
            "description": "Hay un bache.",
            "latitude": "-35.836000",
            "longitude": "-61.870000",
            "status": Report.Status.IN_PROGRESS,
            "review_status": Report.ReviewStatus.PENDING,
        }

    def test_admin_uses_site_theme_preference(self):
        response = self.client.get(reverse("admin:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'const appKey = "pehuajo-reporta-theme"')
        self.assertContains(response, 'const adminKey = "theme"')
        self.assertContains(response, "Inicio de Pehuajó Reporta")
        self.assertContains(response, reverse("reports:management_map"))

    def test_status_change_emails_confirmed_contact(self):
        response = self.client.post(self.change_url, self.form_data)

        self.assertEqual(response.status_code, 302)
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, Report.Status.IN_PROGRESS)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["ana@example.com"])
        self.assertIn("Recibido", mail.outbox[0].body)
        self.assertIn("En curso", mail.outbox[0].body)

    def test_editing_without_status_change_does_not_send_email(self):
        self.client.post(
            self.change_url,
            {**self.form_data, "status": Report.Status.RECEIVED},
        )

        self.assertEqual(len(mail.outbox), 0)

    def test_status_change_does_not_email_unconfirmed_contact(self):
        self.report.email_verified = False
        self.report.save(update_fields=("email_verified",))

        self.client.post(self.change_url, self.form_data)

        self.assertEqual(len(mail.outbox), 0)


class AddressSearchTests(TestCase):
    def setUp(self):
        cache.clear()
        self.url = reverse("reports:search_address")

    def tearDown(self):
        cache.clear()

    def test_search_rejects_short_query(self):
        response = self.client.get(self.url, {"q": "ab"})

        self.assertEqual(response.status_code, 400)

    def test_reverse_search_rejects_invalid_coordinates(self):
        response = self.client.get(
            reverse("reports:reverse_address"),
            {"lat": "91", "lon": "-61.9"},
        )

        self.assertEqual(response.status_code, 400)

    @patch("reports.views.urlopen")
    def test_reverse_search_returns_address_for_map_point(self, mock_urlopen):
        mock_urlopen.return_value = BytesIO(
            json.dumps(
                {
                    "display_name": "Alem y Alfonsín, Pehuajó, Buenos Aires",
                    "lat": "-35.812281",
                    "lon": "-61.897967",
                }
            ).encode()
        )

        response = self.client.get(
            reverse("reports:reverse_address"),
            {"lat": "-35.812281", "lon": "-61.897967"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"address": "Alem y Alfonsín, Pehuajó, Buenos Aires"},
        )
        request = mock_urlopen.call_args.args[0]
        self.assertIn("reverse?", request.full_url)
        self.assertIn("lat=-35.812281", request.full_url)

    @patch("reports.views.urlopen")
    def test_reverse_search_includes_street_number_and_locality(self, mock_urlopen):
        cache.set(
            "reverse-address:-35.812281:-61.897967",
            {"address": "San Martín"},
            timeout=60,
        )
        mock_urlopen.return_value = BytesIO(
            json.dumps(
                {
                    "display_name": "San Martín",
                    "address": {
                        "road": "San Martín",
                        "house_number": "123",
                        "city": "Pehuajó",
                        "county": "Partido de Pehuajó",
                        "state": "Buenos Aires",
                        "country": "Argentina",
                    },
                }
            ).encode()
        )

        response = self.client.get(
            reverse("reports:reverse_address"),
            {"lat": "-35.812281", "lon": "-61.897967"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "address": (
                    "San Martín 123, Pehuajó, Partido de Pehuajó, "
                    "Buenos Aires, Argentina"
                )
            },
        )

    @patch("reports.views.urlopen")
    def test_reverse_search_reports_when_point_has_no_address(self, mock_urlopen):
        mock_urlopen.return_value = BytesIO(json.dumps({}).encode())

        response = self.client.get(
            reverse("reports:reverse_address"),
            {"lat": "-35.812281", "lon": "-61.897967"},
        )

        self.assertEqual(response.status_code, 404)
        self.assertIn("error", response.json())

    def test_intersection_query_accepts_common_separators(self):
        self.assertEqual(_intersection_streets("Alem y Alfonsín"), ("Alem", "Alfonsín"))
        self.assertEqual(
            _intersection_streets("Alem esq. Alfonsín"),
            ("Alem", "Alfonsín"),
        )
        self.assertEqual(
            _intersection_streets("Alem & Alfonsín"),
            ("Alem", "Alfonsín"),
        )

    def test_segment_intersection_finds_crossing_point(self):
        crossing = _segment_intersection(
            (-35.81, -61.91),
            (-35.81, -61.89),
            (-35.82, -61.90),
            (-35.80, -61.90),
        )

        self.assertIsNotNone(crossing)
        self.assertAlmostEqual(crossing[0], -35.81, places=6)
        self.assertAlmostEqual(crossing[1], -61.90, places=6)

    @patch("reports.views.time.sleep")
    @patch("reports.views.urlopen")
    def test_search_returns_intersection_point(self, mock_urlopen, mock_sleep):
        street_geometry = [
            {
                "category": "highway",
                "name": "Leandro N. Alem",
                "geojson": {
                    "type": "LineString",
                    "coordinates": [[-61.91, -35.81], [-61.89, -35.81]],
                },
            },
            {
                "category": "highway",
                "name": "Presidente Raúl Ricardo Alfonsín",
                "geojson": {
                    "type": "LineString",
                    "coordinates": [[-61.90, -35.82], [-61.90, -35.80]],
                },
            },
        ]
        mock_urlopen.side_effect = [
            BytesIO(json.dumps([street_geometry[0]]).encode()),
            BytesIO(json.dumps([street_geometry[1]]).encode()),
        ]

        response = self.client.get(self.url, {"q": "Alem y Alfonsín"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["results"],
            [
                {
                    "label": "Esquina de Alem y Alfonsín, Pehuajó",
                    "latitude": "-35.810000",
                    "longitude": "-61.900000",
                }
            ],
        )
        self.assertEqual(mock_urlopen.call_count, 2)
        self.assertIn("Alem", mock_urlopen.call_args_list[0].args[0].full_url)
        self.assertIn("Alfons", mock_urlopen.call_args_list[1].args[0].full_url)

    @patch("reports.views.urlopen")
    def test_search_returns_address_and_coordinates(self, mock_urlopen):
        mock_urlopen.return_value = BytesIO(
            json.dumps(
                [
                    {
                        "display_name": "San Martín 300, Pehuajó",
                        "lat": "-35.836",
                        "lon": "-61.870",
                    }
                ]
            ).encode()
        )

        response = self.client.get(self.url, {"q": "San Martín 300"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["results"],
            [
                {
                    "label": "San Martín 300, Pehuajó",
                    "latitude": "-35.836",
                    "longitude": "-61.870",
                }
            ],
        )
        request = mock_urlopen.call_args.args[0]
        self.assertIn("Pehuaj%C3%B3", request.full_url)
        self.assertEqual(request.get_header("User-agent"), "PehuajoReporta/0.1 (address search)")
