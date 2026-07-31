from django import forms
from .models import Template, GeneratedDocument


class TemplateUploadForm(forms.ModelForm):
    class Meta:
        model  = Template
        fields = ["name", "source_pdf"]
        widgets = {
            "name": forms.TextInput(attrs={
                "class": "form-input",
                "placeholder": "e.g. Annual Report Template",
            }),
            "source_pdf": forms.FileInput(attrs={
                "class": "file-input",
                "accept": ".pdf",
            }),
        }
        labels = {
            "name": "Template Name",
            "source_pdf": "Source PDF",
        }
        help_texts = {
            "source_pdf": "Upload the PDF whose style you want to replicate.",
        }


class DocumentCreateForm(forms.ModelForm):
    template = forms.ModelChoiceField(
        queryset=Template.objects.filter(status="ready"),
        widget=forms.Select(attrs={"class": "form-select"}),
        label="Style Template",
    )

    class Meta:
        model  = GeneratedDocument
        fields = ["template", "title", "content"]
        widgets = {
            "title": forms.TextInput(attrs={
                "class": "form-input",
                "placeholder": "e.g. Q3 Sales Report",
            }),
            "content": forms.Textarea(attrs={
                "class": "form-textarea",
                "rows": 10,
                "placeholder": "Paste your raw content here. The AI will format it to match the selected style.",
                # Not required in HTML — browser cannot focus a hidden textarea.
                # Required validation is done in the view instead.
                "required": False,
            }),
        }

    def __init__(self, *args, workspace=None, **kwargs):
        super().__init__(*args, **kwargs)
        if workspace:
            self.fields["template"].queryset = Template.objects.filter(
                status="ready", workspace=workspace
            )
        # Make content optional at form level — view validates based on mode
        self.fields["content"].required = False


class DocumentRefineForm(forms.Form):
    feedback = forms.CharField(
        widget=forms.Textarea(attrs={
            "class": "form-textarea",
            "rows": 4,
            "placeholder": "e.g. Make the headings larger and use a darker background for the header section.",
        }),
        label="Refinement Instructions",
        help_text="Describe what changes to apply to the generated document.",
    )
