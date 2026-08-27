from django import forms
from django.utils.translation import gettext_lazy as _


class InventoryPriceImportUploadForm(forms.Form):
    source = forms.FileField(label=_("CSV or XLSX file"))

    def clean_source(self):
        source = self.cleaned_data["source"]
        if not source.name.lower().endswith((".csv", ".xlsx")):
            raise forms.ValidationError(_("Choose a CSV or XLSX file."))
        if source.size > 5 * 1024 * 1024:
            raise forms.ValidationError(_("The file exceeds the 5 MiB limit."))
        return source
