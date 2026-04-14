"""
Django forms for the stocks app.
Registration, alert creation, watchlist, and profile forms.
"""

from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.forms import UserCreationForm
from stocks.models import Alert, UserProfile


class UserRegistrationForm(UserCreationForm):
    """Extended registration form with email and role."""

    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={
            'class': 'form-control',
            'placeholder': 'Email address',
        })
    )
    first_name = forms.CharField(
        max_length=50,
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'First name',
        })
    )
    last_name = forms.CharField(
        max_length=50,
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Last name',
        })
    )

    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'email', 'password1', 'password2']
        widgets = {
            'username': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Username',
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['password1'].widget.attrs.update({
            'class': 'form-control',
            'placeholder': 'Password',
        })
        self.fields['password2'].widget.attrs.update({
            'class': 'form-control',
            'placeholder': 'Confirm password',
        })

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data['email']
        user.first_name = self.cleaned_data['first_name']
        user.last_name = self.cleaned_data['last_name']
        if commit:
            user.save()
            UserProfile.objects.create(user=user, role='user')
        return user


class UserProfileForm(forms.ModelForm):
    """Profile editing form."""

    class Meta:
        model = UserProfile
        fields = ['bio', 'phone', 'notification_email']
        widgets = {
            'bio': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': 'Tell us about yourself...',
            }),
            'phone': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Phone number',
            }),
            'notification_email': forms.EmailInput(attrs={
                'class': 'form-control',
                'placeholder': 'Notification email',
            }),
        }


class AlertForm(forms.ModelForm):
    """Form for creating stock alerts."""

    symbol = forms.CharField(
        max_length=10,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'e.g. AAPL',
        })
    )

    class Meta:
        model = Alert
        fields = ['condition', 'threshold']
        widgets = {
            'condition': forms.Select(attrs={'class': 'form-select'}),
            'threshold': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': 'Threshold value',
                'step': '0.01',
            }),
        }


class StockSearchForm(forms.Form):
    """Stock search form."""

    query = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Search by symbol or company name...',
            'id': 'stock-search-input',
        })
    )


class WatchlistForm(forms.Form):
    """Form to add a stock to watchlist."""

    symbol = forms.CharField(
        max_length=10,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Stock symbol (e.g. AAPL)',
        })
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'rows': 2,
            'placeholder': 'Optional notes...',
        })
    )
