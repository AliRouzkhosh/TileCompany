# support/forms.py

from django import forms

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _, get_language
from .models import Ticket, TicketReply, TicketCategory
from TileCompany.uploads import MAX_SUPPORT_IMAGE_BYTES, validate_raster_or_pdf


FIELD_CLASS = "sf-support-control"
TEXTAREA_CLASS = FIELD_CLASS + " resize-y"

class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True

class MultipleFileField(forms.FileField):
    widget = MultipleFileInput

    def clean(self, data, initial=None):
        files = data if isinstance(data, (list, tuple)) else ([data] if data else [])
        if not files:
            return []
        return [super(MultipleFileField, self).clean(file, initial) for file in files]


class LocalizedCategoryChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, category):
        if get_language() == "fa" and category.name_fa:
            return category.name_fa
        return category.name


class TicketCreateForm(forms.ModelForm):
    category = LocalizedCategoryChoiceField(queryset=TicketCategory.objects.none())
    attachments = MultipleFileField(label=_("Attachments"), required=False, help_text=_("JPEG, PNG, WEBP, or PDF files up to 5 MB each."), widget=MultipleFileInput(attrs={"multiple": True, "accept": ".jpg,.jpeg,.png,.webp,.pdf"}))
    class Meta:
        model = Ticket
        fields = (
            "category",
            "subject",
            "order",
            "message",
        )
        labels = {
            "category": _("Category"),
            "subject": _("Subject"),
            "order": _("Related order"),
            "message": _("Message"),
        }
        help_texts = {
            "category": _("Choose the option that best matches your request."),
            "subject": _("A short summary of your issue."),
            "order": _("Optionally link an order related to this request."),
            "message": _("Describe your issue in detail so we can help."),
        }

        widgets = {
            "subject": forms.TextInput(
                attrs={
                    "class": FIELD_CLASS,
                    "placeholder": _("Briefly describe your issue"),
                }
            ),
            "order": forms.Select(attrs={"class": FIELD_CLASS}),
            "message": forms.Textarea(
                attrs={
                    "class": TEXTAREA_CLASS,
                    "rows": 6,
                    "placeholder": _("Please describe your issue in as much detail as possible."),
                }
            ),
        }

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["order"].required = False
        self.fields["order"].queryset = user.orders.all()
        self.fields["category"].queryset = TicketCategory.objects.all()
        self.fields["category"].empty_label = _("Select a category")
        self.fields["category"].widget.attrs.setdefault("class", FIELD_CLASS)

    def clean_subject(self):
        subject = self.cleaned_data["subject"].strip()

        if len(subject) < 5:
            raise forms.ValidationError(
                _("Please enter a more descriptive subject.")
            )

        return subject

    def clean_message(self):
        message = self.cleaned_data["message"].strip()

        if len(message) < 20:
            raise forms.ValidationError(
                _("Please provide more details about your issue.")
            )

        return message

    def clean_attachments(self):
        return _validate_attachments(self.files.getlist("attachments"))

class TicketReplyForm(forms.Form):
    message = forms.CharField(
        min_length=2,
        label=_("Your reply"),
        widget=forms.Textarea(
            attrs={
                "class": TEXTAREA_CLASS,
                "rows": 4,
                "placeholder": _("Write your reply..."),
            }
        ),
    )
    attachments = MultipleFileField(label=_("Attachments"), required=False, help_text=_("JPEG, PNG, WEBP, or PDF files up to 5 MB each."), widget=MultipleFileInput(attrs={"multiple": True, "accept": ".jpg,.jpeg,.png,.webp,.pdf"}))

    def clean_message(self):
        return self.cleaned_data["message"].strip()

    def clean_attachments(self):
        return _validate_attachments(self.files.getlist("attachments"))

def _validate_attachments(files):
    for file in files:
        try:
            validate_raster_or_pdf(
                file,
                max_image_bytes=min(settings.SUPPORT_MAX_ATTACHMENT_SIZE, MAX_SUPPORT_IMAGE_BYTES),
                max_pdf_bytes=settings.SUPPORT_MAX_ATTACHMENT_SIZE,
            )
        except ValidationError as error:
            raise forms.ValidationError(error.messages)
    return files
