from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("", views.home, name="home"),
    path("ingresar/", views.RoleBasedLoginView.as_view(), name="login"),
    path("registrarse/", views.register, name="register"),
    path(
        "reenviar-verificacion/",
        views.resend_account_verification,
        name="resend_account_verification",
    ),
    path("salir/", views.LogoutView.as_view(next_page="reports:home"), name="logout"),
    path("admin/mapa-gestion/", views.management_map, name="management_map"),
    path("reportar/", views.create_report, name="create"),
    path("mis-reportes/", views.my_reports, name="my_reports"),
    path(
        "mis-reportes/<int:report_id>/foto/",
        views.my_report_photo,
        name="my_report_photo",
    ),
    path("gracias/", views.thank_you, name="thank_you"),
    path("mapa/", views.public_map, name="map"),
    path(
        "verificar-cuenta/<str:token>/",
        views.verify_account_email,
        name="verify_account_email",
    ),
    path("verificar-email/<str:token>/", views.verify_report_email, name="verify_email"),
    path("buscar-direccion/", views.search_address, name="search_address"),
    path("direccion-desde-ubicacion/", views.reverse_address, name="reverse_address"),
]
