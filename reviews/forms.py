######### reviews/forms.py ############
from django import forms
from django.utils.translation import gettext_lazy as _
from .models import Review, ReviewComment


class ReviewForm(forms.ModelForm):

    class Meta:

        model = Review

        fields = (
            "rating",
            "comment",
        )

        widgets = {

            "rating": forms.Select(
                attrs={
                    "class": "w-full rounded-xl border border-stone-300 bg-white px-4 py-3 text-sm shadow-sm transition focus:border-amber-500 focus:outline-none focus:ring-4 focus:ring-amber-100",
                },
            ),

            "comment": forms.Textarea(
                attrs={
                    "rows": 4,
                    "placeholder": _("Share your experience with this product..."),
                    "class": "w-full rounded-xl border border-stone-300 bg-white px-4 py-3 text-sm shadow-sm transition focus:border-amber-500 focus:outline-none focus:ring-4 focus:ring-amber-100",
                },
            ),

        }

    def clean_comment(self):
        comment = self.cleaned_data["comment"].strip()
        if not comment:
            raise forms.ValidationError(_("A review cannot be empty."))
        return comment


class ReviewCommentForm(forms.ModelForm):

    class Meta:

        model = ReviewComment

        fields = (
            "comment",
        )

        widgets = {

            "comment": forms.Textarea(
                attrs={
                    "rows": 2,
                    "placeholder": _("Write a comment..."),
                    "class": "w-full rounded-xl border border-stone-300 bg-white px-4 py-3 text-sm shadow-sm transition focus:border-amber-500 focus:outline-none focus:ring-4 focus:ring-amber-100",
                },
            ),

        }

    def clean_comment(self):
        comment = self.cleaned_data["comment"].strip()
        if not comment:
            raise forms.ValidationError(_("A comment cannot be empty."))
        return comment
