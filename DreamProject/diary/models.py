import json
import base64
from django.db import models
from django.conf import settings


class Dream(models.Model):
    """
    Schema de la table de reves
    """

    DREAM_TYPES = [
        ("rêve", "Rêve"),
        ("cauchemar", "Cauchemar"),
    ]

    # Informations de base
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE
    )
    transcription = models.TextField(verbose_name="Transcription du rêve")
    date = models.DateTimeField("Date du rêve enregistré", auto_now_add=True)

    # Analyse émotionnelle
    emotions_json = models.TextField(
        blank=True,
        null=True,
        verbose_name="Émotions (JSON)",
        help_text="Analyse des émotions au format JSON",
    )
    dominant_emotion = models.CharField(
        max_length=50, blank=True, null=True, verbose_name="Émotion dominante"
    )
    dream_type = models.CharField(
        max_length=10,
        choices=DREAM_TYPES,
        default="rêve",
        verbose_name="Type de rêve",
    )

    # Contenu généré - SEULEMENT BASE64
    image_base64 = models.TextField(
        blank=True,
        null=True,
        verbose_name="Image du rêve (base64)",
        help_text="Image générée à partir du rêve encodée en base64",
    )
    interpretation_json = models.TextField(
        blank=True,
        null=True,
        verbose_name="Interprétation (JSON)",
        help_text="Interprétation du rêve au format JSON",
    )

    # Métadonnées
    is_analyzed = models.BooleanField(
        default=False,
        verbose_name="Rêve analysé",
        help_text="Indique si l'analyse complète a été effectuée",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date"]
        verbose_name = "Rêve"
        verbose_name_plural = "Rêves"

    def __str__(self):
        return f"{self.user.username} - {self.date.strftime('%d/%m/%Y %H:%M')} - {self.transcription[:50]}..."

    @property
    def emotions(self):
        """Retourne les émotions sous forme de dictionnaire"""
        if self.emotions_json:
            try:
                return json.loads(self.emotions_json)
            except json.JSONDecodeError:
                return {}
        return {}

    @emotions.setter
    def emotions(self, value):
        """Définit les émotions à partir d'un dictionnaire"""
        if isinstance(value, dict):
            self.emotions_json = json.dumps(value)
        else:
            self.emotions_json = None

    @property
    def interpretation(self):
        """Retourne l'interprétation sous forme de dictionnaire"""
        if self.interpretation_json:
            try:
                return json.loads(self.interpretation_json)
            except json.JSONDecodeError:
                return {}
        return {}

    @interpretation.setter
    def interpretation(self, value):
        """Définit l'interprétation à partir d'un dictionnaire"""
        if isinstance(value, dict):
            self.interpretation_json = json.dumps(value)
        else:
            self.interpretation_json = None

    def set_image_from_bytes(self, image_bytes, format="PNG"):
        """Encode une image en base64 et la stocke"""
        if image_bytes:
            base64_string = base64.b64encode(image_bytes).decode("utf-8")
            mime_type = f"image/{format.lower()}"
            if format.upper() == "JPG":
                mime_type = "image/jpeg"
            self.image_base64 = f"data:{mime_type};base64,{base64_string}"

    @property
    def has_image(self):
        """Vérifie si le rêve a une image"""
        return bool(self.image_base64)

    @property
    def image_url(self):
        """Retourne l'URL d'image en base64"""
        return self.image_base64

    @property
    def short_transcription(self):
        """Retourne une version courte de la transcription"""
        if len(self.transcription) > 100:
            return self.transcription[:100] + "..."
        return self.transcription
