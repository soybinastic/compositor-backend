"""Tests for video mix backend resolution and factories."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from apps.compositor.video_mix_backend import (
    CpuVideoMixBackend,
    CudaVideoMixBackend,
    GlVideoMixBackend,
    get_video_mix_backend,
    resolve_video_backend,
)


class ResolveVideoBackendTests(SimpleTestCase):
    def test_auto_prefers_cuda_then_gl_then_cpu(self):
        with patch(
            'apps.compositor.video_mix_backend.backend_supported',
            side_effect=lambda name: name == 'cuda',
        ):
            self.assertEqual(resolve_video_backend('auto'), 'cuda')

        with patch(
            'apps.compositor.video_mix_backend.backend_supported',
            side_effect=lambda name: name in ('gl', 'cpu'),
        ):
            self.assertEqual(resolve_video_backend('auto'), 'gl')

        with patch(
            'apps.compositor.video_mix_backend.backend_supported',
            side_effect=lambda name: name == 'cpu',
        ):
            self.assertEqual(resolve_video_backend('auto'), 'cpu')

    def test_explicit_cuda_fails_when_unsupported(self):
        with patch(
            'apps.compositor.video_mix_backend.backend_supported',
            return_value=False,
        ), patch(
            'apps.compositor.video_mix_backend.element_available',
            return_value=False,
        ):
            with self.assertRaises(RuntimeError) as ctx:
                resolve_video_backend('cuda')
            self.assertIn('cudacompositor', str(ctx.exception))

    def test_explicit_gl_fails_when_unsupported(self):
        with patch(
            'apps.compositor.video_mix_backend.backend_supported',
            return_value=False,
        ), patch(
            'apps.compositor.video_mix_backend.element_available',
            return_value=False,
        ):
            with self.assertRaises(RuntimeError) as ctx:
                resolve_video_backend('gl')
            self.assertIn('glcompositor', str(ctx.exception))

    def test_invalid_backend_raises_value_error(self):
        with self.assertRaises(ValueError):
            resolve_video_backend('metal')

    def test_get_video_mix_backend_factories(self):
        self.assertIsInstance(get_video_mix_backend('cpu'), CpuVideoMixBackend)
        self.assertIsInstance(get_video_mix_backend('gl'), GlVideoMixBackend)
        cuda = get_video_mix_backend('cuda', cuda_device_id=0)
        self.assertIsInstance(cuda, CudaVideoMixBackend)
        self.assertEqual(cuda._cuda_device_id, 0)


class CpuVideoMixBackendStructureTests(SimpleTestCase):
    @patch('apps.compositor.video_mix_backend.Gst')
    def test_cpu_ingest_tail_uses_videoscale_and_queue(self, mock_gst):
        mock_gst.SECOND = 1_000_000_000
        mock_gst.MSECOND = 1_000_000

        def make(factory_name, name):
            element = MagicMock(name=name)
            element.get_factory.return_value.get_name.return_value = factory_name
            return element

        mock_gst.ElementFactory.make.side_effect = make
        backend = CpuVideoMixBackend()
        tail = backend.build_ingest_tail('peer-1')
        self.assertEqual(len(tail), 2)
        names = [call.args[0] for call in mock_gst.ElementFactory.make.call_args_list]
        self.assertEqual(names, ['videoscale', 'queue'])
