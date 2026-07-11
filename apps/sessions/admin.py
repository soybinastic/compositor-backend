from django.contrib import admin

from apps.sessions.models import StudioSession


@admin.register(StudioSession)
class StudioSessionAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'host_display_name',
        'status',
        'layout',
        'created_at',
        'ended_at',
    )
    list_filter = ('status', 'layout')
    search_fields = ('id', 'host_display_name', 'invite_token')
    readonly_fields = ('id', 'invite_token', 'created_at', 'ended_at')
