"""DRF serializers for graphics API payloads."""

from __future__ import annotations

from rest_framework import serializers


class PositionSerializer(serializers.Serializer):
    x = serializers.IntegerField(required=False)
    y = serializers.IntegerField(required=False)
    w = serializers.IntegerField(required=False, min_value=1)
    h = serializers.IntegerField(required=False, min_value=1)


class BackgroundSerializer(serializers.Serializer):
    url = serializers.CharField(required=False, allow_blank=True, default='')
    source = serializers.CharField(required=False, allow_blank=True, default='')
    is_active = serializers.BooleanField(required=False, default=True)
    fit = serializers.ChoiceField(
        choices=('cover', 'stretch'),
        required=False,
        default='cover',
    )


class OverlaySerializer(serializers.Serializer):
    url = serializers.CharField(required=False, allow_blank=True, default='')
    source = serializers.CharField(required=False, allow_blank=True, default='')
    is_active = serializers.BooleanField(required=False, default=False)
    position = PositionSerializer(required=False)


class LogoSerializer(serializers.Serializer):
    url = serializers.CharField(required=False, allow_blank=True, default='')
    source = serializers.CharField(required=False, allow_blank=True, default='')
    is_active = serializers.BooleanField(required=False, default=False)
    placement = serializers.CharField(required=False, allow_blank=True, default='')
    logoPosition = serializers.CharField(required=False, allow_blank=True, default='')
    position = serializers.CharField(required=False, allow_blank=True, default='')


class QrSerializer(serializers.Serializer):
    url = serializers.CharField(required=False, allow_blank=True, default='')
    source = serializers.CharField(required=False, allow_blank=True, default='')
    is_shown = serializers.BooleanField(required=False, default=False)
    position = serializers.JSONField(required=False)
    overlay_width = serializers.IntegerField(required=False, min_value=1)
    overlay_height = serializers.IntegerField(required=False, min_value=1)
    title = serializers.CharField(required=False, allow_blank=True, default='')
    # Content-signature fields (styling) — accepted and stored as-is.
    primary = serializers.CharField(required=False, allow_blank=True)
    secondary = serializers.CharField(required=False, allow_blank=True)
    font = serializers.CharField(required=False, allow_blank=True)


class BannerSerializer(serializers.Serializer):
    title = serializers.CharField(required=False, allow_blank=True, default='')
    description = serializers.CharField(required=False, allow_blank=True, default='')
    is_display = serializers.BooleanField(required=False)
    is_display_names = serializers.BooleanField(required=False)
    theme = serializers.CharField(required=False, allow_blank=True, default='plain')
    primary = serializers.CharField(required=False, allow_blank=True, default='')
    secondary = serializers.CharField(required=False, allow_blank=True, default='')
    parent_data = serializers.DictField(required=False)
    textOverlay = serializers.DictField(required=False)
    graphic = serializers.DictField(required=False)
    font_size = serializers.IntegerField(required=False, min_value=8, max_value=200)


class TickerSerializer(serializers.Serializer):
    tickerText = serializers.CharField(required=False, allow_blank=True, default='')
    ticker_description = serializers.CharField(required=False, allow_blank=True, default='')
    text = serializers.CharField(required=False, allow_blank=True, default='')
    tickerEnabled = serializers.BooleanField(required=False, default=True)
    tickerPosition = serializers.ChoiceField(
        choices=('top', 'bottom'),
        required=False,
        default='bottom',
    )
    tickerDirection = serializers.ChoiceField(
        choices=('rtl', 'ltr'),
        required=False,
        default='rtl',
    )
    tickerSpeed = serializers.FloatField(required=False, default=2.0, min_value=0.1)
    primary = serializers.CharField(required=False, allow_blank=True, default='')
    secondary = serializers.CharField(required=False, allow_blank=True, default='')
    textOverlay = serializers.DictField(required=False)
    bannerTickerStyle = serializers.DictField(required=False)
    chatOverlay = serializers.BooleanField(required=False)


class ChatMessageSerializer(serializers.Serializer):
    author = serializers.CharField(required=False, allow_blank=True, default='')
    text = serializers.CharField(required=False, allow_blank=True, default='')
    message = serializers.CharField(required=False, allow_blank=True, default='')


class ChatSerializer(serializers.Serializer):
    enabled = serializers.BooleanField(required=False, default=False)
    messages = ChatMessageSerializer(many=True, required=False, default=list)


class BannerTickerSerializer(serializers.Serializer):
    banner = BannerSerializer(required=False)
    ticker = TickerSerializer(required=False)


class BulkGraphicsSerializer(serializers.Serializer):
    background = BackgroundSerializer(required=False, allow_null=True)
    overlay = OverlaySerializer(required=False, allow_null=True)
    logo = LogoSerializer(required=False, allow_null=True)
    qr = QrSerializer(required=False, allow_null=True)
    banner = BannerSerializer(required=False, allow_null=True)
    ticker = TickerSerializer(required=False, allow_null=True)
    chat = ChatSerializer(required=False, allow_null=True)


class GraphicsStateSerializer(serializers.Serializer):
    background = serializers.JSONField(allow_null=True, required=False)
    overlay = serializers.JSONField(allow_null=True, required=False)
    logo = serializers.JSONField(allow_null=True, required=False)
    qr = serializers.JSONField(allow_null=True, required=False)
    banner = serializers.JSONField(allow_null=True, required=False)
    ticker = serializers.JSONField(allow_null=True, required=False)
    chat = serializers.JSONField(allow_null=True, required=False)
