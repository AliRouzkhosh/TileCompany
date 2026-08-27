############## blogs/forms.py ################
from django import forms
from django.utils.translation import gettext_lazy as _

from .models import Comment


class CommentForm(forms.ModelForm):
    class Meta:
        model = Comment
        fields = ("body",)

        widgets = {
            "body": forms.Textarea(
                attrs={
                    "rows": 5,
                    "placeholder": _("Share your thoughts..."),
                    "class": (
                        "w-full rounded-xl border border-gray-300 "
                        "px-4 py-3 shadow-sm "
                        "focus:border-blue-500 "
                        "focus:ring-2 focus:ring-blue-500/20"
                    ),
                }
            ),
        }

        labels = {
            "body": _("Your comment"),
        }

        help_texts = {
            "body": "",
        }

    def clean_body(self):
        body = self.cleaned_data["body"].strip()

        if not body:
            raise forms.ValidationError(_("Comment cannot be empty."))

        return body
