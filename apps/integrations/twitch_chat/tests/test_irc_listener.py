from django.test import SimpleTestCase

from apps.integrations.twitch_chat.irc_listener import parse_privmsg


class ParsePrivmsgTests(SimpleTestCase):
    def test_parses_display_name_and_message(self):
        line = (
            '@badge-info=;badges=;color=#FF0000;display-name=CoolViewer;'
            'emotes=;first-msg=0;flags=;id=abc;mod=0;room-id=1;subscriber=0;tmi-sent-ts=1;'
            'turbo=0;user-id=2;user-type= :coolviewer!coolviewer@coolviewer.tmi.twitch.tv '
            'PRIVMSG #streamer_pro :Hello chat!'
        )
        parsed = parse_privmsg(line)
        self.assertEqual(parsed, ('coolviewer', 'Hello chat!'))

    def test_ignores_non_privmsg(self):
        self.assertIsNone(parse_privmsg('PING :tmi.twitch.tv'))
        self.assertIsNone(parse_privmsg(':tmi.twitch.tv NOTICE #channel :Login unsuccessful'))
