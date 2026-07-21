from django.test import SimpleTestCase

from apps.recording.gstreamer_recorder import _is_expected_finalize_error


class FinalizeErrorClassificationTests(SimpleTestCase):
    def test_not_linked_is_expected(self):
        err = Exception('Internal data stream error.')
        debug = (
            '../subprojects/gstreamer/plugins/elements/gstqueue.c(1081): '
            'gst_queue_handle_sink_event (): /GstPipeline:p/GstQueue:rec_a_queue:\n'
            'streaming stopped, reason not-linked (-1)'
        )
        self.assertTrue(_is_expected_finalize_error(err, debug))

    def test_unrelated_error_is_not_expected(self):
        err = Exception('Could not open resource for writing.')
        debug = 'filesink error'
        self.assertFalse(_is_expected_finalize_error(err, debug))
