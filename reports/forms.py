from django import forms

from .models import Report


class ReportForm(forms.ModelForm):
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
