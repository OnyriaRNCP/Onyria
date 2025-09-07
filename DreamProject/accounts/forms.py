from django import forms
from django.contrib.auth.forms import UserCreationForm, PasswordChangeForm
from .models import CustomUser
from datetime import date

class RegisterForm(UserCreationForm):
    email = forms.EmailField(
        label="Adresse mail",
        widget=forms.EmailInput(attrs={'placeholder': 'Écrivez votre adresse mail'})
    )
    username = forms.CharField(
        label="Nom d'utilisateur",
        widget=forms.TextInput(attrs={'placeholder': 'Écrivez votre nom d\'utilisateur'})
    )

    date_of_birth = forms.DateField(
        label="Date de naissance",
        required=True,
        widget=forms.DateInput(attrs={
            'type': 'date',
            'placeholder': 'AAAA-MM-JJ'
        })
    )

    sexe = forms.ChoiceField(
        label="Sexe",
        choices=[('', 'Sélectionnez votre sexe')] + CustomUser.GENDER_CHOICES,
        required=False,
    )

    password1 = forms.CharField(
        label="Mot de passe",
        widget=forms.PasswordInput(attrs={'placeholder': 'Écrivez votre mot de passe'})
    )
    password2 = forms.CharField(
        label="Confirmation du mot de passe",
        widget=forms.PasswordInput(attrs={'placeholder': 'Confirmez votre mot de passe'})
    )

    class Meta:
        model = CustomUser
        fields = ('email', 'username', 'date_of_birth', 'sexe', 'password1', 'password2')

    def clean_date_of_birth(self):
        dob = self.cleaned_data['date_of_birth']
        today = date.today()
        if dob > today:
            raise forms.ValidationError("La date de naissance ne peut pas être dans le futur.")
        years = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
        if years < 13:
            raise forms.ValidationError("L'inscription est réservée aux personnes de 13 ans et plus.")
        if years > 120:
            raise forms.ValidationError("Merci de vérifier votre date de naissance.")
        return dob


class LoginForm(forms.Form):
    email = forms.EmailField(
        label="Adresse mail",
        widget=forms.EmailInput(attrs={'placeholder': 'Écrivez votre adresse mail'})
    )
    password = forms.CharField(
        label="Mot de passe", 
        widget=forms.PasswordInput(attrs={'placeholder': 'Écrivez votre mot de passe'})
    )
    
class CustomPasswordChangeForm(PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Personnalisation des labels
        self.fields['old_password'].label = "Ancien mot de passe"
        self.fields['new_password1'].label = "Nouveau mot de passe"
        self.fields['new_password2'].label = "Confirmez le nouveau mot de passe"
        
        # Ajout des placeholders
        self.fields['old_password'].widget.attrs.update({'placeholder': 'Écrivez votre ancien mot de passe'})
        self.fields['new_password1'].widget.attrs.update({'placeholder': 'Écrivez votre nouveau mot de passe'})
        self.fields['new_password2'].widget.attrs.update({'placeholder': 'Confirmez votre nouveau mot de passe'})


class BioForm(forms.ModelForm):
    class Meta:
        model = CustomUser
        fields = ["bio"]
        widgets = {
            "bio": forms.TextInput(attrs={
                "class": "w-full rounded-xl border px-3 py-2",
                "placeholder": "Ta phrase onirique… (max 180 caractères)",
                "maxlength": 180
            })
        }