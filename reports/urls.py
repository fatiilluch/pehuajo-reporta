from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("", views.home, name="home"),
    path("ingresar/", views.RoleBasedLoginView.as_view(), name="login"),
    path("salir/", views.LogoutView.as_view(next_page="reports:home"), name="logout"),
    path("admin/mapa-gestion/", views.management_map, name="management_map"),
    path("reportar/", views.create_report, name="create"),
    path("gracias/", views.thank_you, name="thank_you"),
    path("mapa/", views.public_map, name="map"),
    path("verificar-email/<str:token>/", views.verify_report_email, name="verify_email"),
    path("buscar-direccion/", views.search_address, name="search_address"),
    path("direccion-desde-ubicacion/", views.reverse_address, name="reverse_address"),
]
