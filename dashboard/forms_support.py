from django import forms
from django.utils.text import slugify
from dashboard.forms_blog import DashboardStyledModelForm, INPUT_CLASS, TEXTAREA_CLASS as DASHBOARD_TEXTAREA_CLASS
from support.forms import FIELD_CLASS, TEXTAREA_CLASS, MultipleFileField, MultipleFileInput, _validate_attachments
from support.models import HelpArticle, TicketCategory, TicketPriority

class StaffReplyForm(forms.Form):
    message=forms.CharField(min_length=2, widget=forms.Textarea(attrs={"class":TEXTAREA_CLASS,"rows":5}))
    attachments=MultipleFileField(required=False, widget=MultipleFileInput(attrs={"multiple":True,"accept":".jpg,.jpeg,.png,.webp,.pdf"}))
    def clean_attachments(self): return _validate_attachments(self.files.getlist("attachments"))
class InternalNoteForm(forms.Form): message=forms.CharField(min_length=2, widget=forms.Textarea(attrs={"class":TEXTAREA_CLASS,"rows":4}))
class TicketControlForm(forms.Form):
    assignee=forms.ModelChoiceField(queryset=None, required=False, widget=forms.Select(attrs={"class":FIELD_CLASS}))
    priority=forms.ChoiceField(choices=TicketPriority.choices, widget=forms.Select(attrs={"class":FIELD_CLASS}))
    category=forms.ModelChoiceField(queryset=TicketCategory.objects.all(), widget=forms.Select(attrs={"class":FIELD_CLASS}))
    workflow=forms.ChoiceField(required=False, choices=(("", "Keep current status"),("resolve", "Resolve ticket"),("reopen", "Reopen ticket"),("close", "Close ticket")), widget=forms.Select(attrs={"class":FIELD_CLASS}))
    def __init__(self,*args,ticket=None,agents=None,**kwargs):
        super().__init__(*args,**kwargs); self.fields['assignee'].queryset=agents; 
        if ticket and not self.is_bound: self.initial={"assignee":ticket.assigned_to,"priority":ticket.priority,"category":ticket.category}
class TicketCategoryForm(forms.ModelForm):
    class Meta:
        model=TicketCategory; fields=("name","name_fa","slug","description","description_fa","icon")
        widgets={field:forms.TextInput(attrs={"class":FIELD_CLASS}) for field in ("name","name_fa","slug","icon")}
class HelpArticleForm(DashboardStyledModelForm):
    class Meta:
        model=HelpArticle; fields=("category","title","title_fa","slug","summary","summary_fa","content","content_fa","is_featured","is_published")
        labels={"title":"English title","title_fa":"Persian title","summary":"English summary","summary_fa":"Persian summary","content":"English article","content_fa":"Persian article","is_featured":"Feature in Help Center","is_published":"Publish for customers"}
        help_texts={"slug":"A stable, English URL identifier. Leave blank to generate it from the English title.","summary":"A short description used in Help Center lists and search results.","content":"Write clear step-by-step guidance. Customer messages and tickets stay separate.","is_featured":"Featured articles are shown first in the Support Center.","is_published":"Only published articles are visible on the public Help Center."}
        widgets={"title_fa":forms.TextInput(attrs={"dir":"rtl"}),"summary":forms.Textarea(attrs={"rows":4}),"summary_fa":forms.Textarea(attrs={"rows":4,"dir":"rtl"}),"content":forms.Textarea(attrs={"rows":14}),"content_fa":forms.Textarea(attrs={"rows":14,"dir":"rtl"})}

    def clean_slug(self):
        slug = self.cleaned_data["slug"].strip() or slugify(self.cleaned_data.get("title", ""))
        if not slug:
            raise forms.ValidationError("Provide an English title or a URL slug.")
        return slug
