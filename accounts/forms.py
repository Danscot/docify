from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm
from .models import UserProfile, Workspace, Invitation, WorkspaceMember


class SignupForm(forms.Form):
    first_name   = forms.CharField(max_length=50, widget=forms.TextInput(attrs={"class": "form-input", "placeholder": "First name"}))
    last_name    = forms.CharField(max_length=50, widget=forms.TextInput(attrs={"class": "form-input", "placeholder": "Last name"}))
    email        = forms.EmailField(widget=forms.EmailInput(attrs={"class": "form-input", "placeholder": "you@company.com"}))
    password     = forms.CharField(min_length=8, widget=forms.PasswordInput(attrs={"class": "form-input", "placeholder": "Min. 8 characters"}))
    account_type = forms.ChoiceField(
        choices=[("personal", "Personal"), ("team", "Team")],
        widget=forms.RadioSelect,
        initial="personal",
    )
    # Team-only field
    team_name = forms.CharField(
        max_length=100, required=False,
        widget=forms.TextInput(attrs={"class": "form-input", "placeholder": "Your company or team name"}),
    )

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def clean(self):
        data = super().clean()
        if data.get("account_type") == "team" and not data.get("team_name"):
            self.add_error("team_name", "Please enter a team or company name.")
        return data


class LoginForm(AuthenticationForm):
    username = forms.CharField(
        label="Email",
        widget=forms.TextInput(attrs={"class": "form-input", "placeholder": "you@company.com", "autofocus": True}),
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={"class": "form-input", "placeholder": "Password"}),
    )


class ProfileForm(forms.ModelForm):
    first_name = forms.CharField(max_length=50, required=False,
                                 widget=forms.TextInput(attrs={"class": "form-input"}))
    last_name  = forms.CharField(max_length=50, required=False,
                                 widget=forms.TextInput(attrs={"class": "form-input"}))
    email      = forms.EmailField(widget=forms.EmailInput(attrs={"class": "form-input"}))

    class Meta:
        model  = UserProfile
        fields = ["avatar", "timezone"]
        widgets = {
            "timezone": forms.Select(attrs={"class": "form-select"},
                                     choices=[(t, t) for t in [
                                         "UTC", "Africa/Douala", "Africa/Abidjan",
                                         "Africa/Dakar", "Africa/Nairobi",
                                         "Europe/Paris", "Europe/London",
                                         "America/New_York", "America/Los_Angeles",
                                     ]]),
            "avatar": forms.FileInput(attrs={"class": "file-input", "accept": "image/*"}),
        }


class WorkspaceSettingsForm(forms.ModelForm):
    class Meta:
        model  = Workspace
        fields = ["name", "logo"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-input"}),
            "logo": forms.FileInput(attrs={"class": "file-input", "accept": "image/*"}),
        }


class InviteForm(forms.Form):
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={"class": "form-input", "placeholder": "colleague@company.com"})
    )
    role = forms.ChoiceField(
        choices=[
            (WorkspaceMember.ROLE_MEMBER, "Member — can create documents from existing templates"),
            (WorkspaceMember.ROLE_ADMIN,  "Admin — can also upload and manage templates"),
        ],
        widget=forms.RadioSelect,
        initial=WorkspaceMember.ROLE_MEMBER,
    )

    def __init__(self, workspace=None, *args, **kwargs):
        self.workspace = workspace
        super().__init__(*args, **kwargs)

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if self.workspace:
            if self.workspace.members.filter(user__email=email).exists():
                raise forms.ValidationError("This person is already a member of this workspace.")
            if self.workspace.invitations.filter(email=email, status="pending").exists():
                raise forms.ValidationError("An invitation has already been sent to this email.")
        return email


class ChangePasswordForm(PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-input"
