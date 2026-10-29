from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm

from .models import Report


class ReportForm(forms.ModelForm):
    def __init__(self, *args, account_user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if account_user is not None:
            contact_name = account_user.get_full_name().strip() or account_user.get_username()
            for field_name, value in (
                ("contact_name", contact_name),
                ("contact_email", account_user.email),
            ):
                self.fields[field_name].initial = value
                self.fields[field_name].disabled = True
            self.fields["contact_name"].help_text = "Tomamos el nombre de tu cuenta."
            self.fields["contact_email"].help_text = (
                "Usamos el email que ya confirmaste al crear tu cuenta."
                if account_user.email
                else "Tu cuenta no tiene un email asociado."
            )

    class Meta:
        model = Report
        fields = (
            "contact_name",
            "contact_email",
            "category",
            "description",
            "latitude",
            "longitude",
            "location_address",
            "photo",
        )
        labels = {
            "contact_name": "Tu nombre",
            "contact_email": "Tu email",
            "category": "Tipo de problema",
            "description": "Contanos qué sucede",
            "location_address": "Dirección aproximada",
            "photo": "Foto (opcional)",
        }
        help_texts = {
            "contact_name": "Completá tu nombre, tu email o ambos.",
            "photo": "Imagen de hasta 5 MB. Solo la verá el equipo municipal.",
        }
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "latitude": forms.HiddenInput(),
            "longitude": forms.HiddenInput(),
            "location_address": forms.HiddenInput(),
            "photo": forms.ClearableFileInput(attrs={"accept": "image/*"}),
        }

    def clean_photo(self):
        photo = self.cleaned_data.get("photo")
        if photo and photo.size > 5 * 1024 * 1024:
            raise forms.ValidationError("La foto no puede superar los 5 MB.")
        return photo

    def clean(self):
        cleaned_data = super().clean()
        contact_name = cleaned_data.get("contact_name", "").strip()
        contact_email = cleaned_data.get("contact_email", "").strip()
        if not contact_name and not contact_email:
            raise forms.ValidationError("Ingresá al menos un nombre o un email.")
        return cleaned_data


class MyReportsFilterForm(forms.Form):
    search = forms.CharField(label="Buscar", required=False, max_length=100)
    category = forms.ChoiceField(
        label="Categoría",
        required=False,
        choices=(("", "Todas"), *Report.Category.choices),
    )
    review_status = forms.ChoiceField(
        label="Revisión",
        required=False,
        choices=(("", "Todas"), *Report.ReviewStatus.choices),
    )
    status = forms.ChoiceField(
        label="Estado",
        required=False,
        choices=(("", "Todos"), *Report.Status.choices),
    )


class RegistrationForm(UserCreationForm):
    email = forms.EmailField(label="Email", required=True)
    first_name = forms.CharField(label="Nombre", max_length=150, required=True)
    last_name = forms.CharField(label="Apellido", max_length=150, required=True)

    class Meta(UserCreationForm.Meta):
        model = get_user_model()
        fields = ("username", "first_name", "last_name", "email")

    def clean_email(self):
        email = self.cleaned_data["email"].strip()
        if get_user_model().objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("Ya existe una cuenta con este email.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        user.is_active = False
        if commit:
            user.save()
        return user


class ResendAccountVerificationForm(forms.Form):
    email = forms.EmailField(label="Email")
