"""Offline exact-stream and optional-dependency tests for the ZX0 backends."""
from pathlib import Path
import builtins
import gc
import importlib.util
import itertools
import random
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import zx0_codec
import zx0_accel

HAS_NUMPY = importlib.util.find_spec('numpy') is not None
HAS_NUMBA = importlib.util.find_spec('numba') is not None


class BackendTests(unittest.TestCase):
    def cases(self):
        for length in range(1, 6):
            for values in itertools.product(range(2), repeat=length):
                yield bytes(values), 32640
        rng = random.Random(42)
        for size in [33, 127, 128, 129, 255, 256, 257, 513]:
            for raw in [bytes(size), (b'abcabcxbc' * (size // 9 + 1))[:size],
                        bytes(rng.randrange(8) for _ in range(size)), rng.randbytes(size)]:
                for limit in [1, 16, 128, 32640]:
                    yield raw, limit

    def verify_backend(self, backend):
        for raw, limit in self.cases():
            with self.subTest(backend=backend, size=len(raw), limit=limit):
                expected = zx0_codec.compress(raw, offset_limit=limit, backend='python')
                packed = zx0_codec.compress(raw, offset_limit=limit, backend=backend)
                self.assertEqual(packed, expected)
                self.assertEqual(zx0_codec.decompress(packed, max_output=len(raw)), raw)

    @unittest.skipUnless(HAS_NUMPY, 'optional NumPy is not installed')
    def test_numpy_exact_streams(self):
        self.verify_backend('numpy')

    @unittest.skipUnless(HAS_NUMBA, 'optional Numba is not installed')
    def test_numba_exact_streams(self):
        self.verify_backend('numba')

    def test_stdlib_fallback_without_optional_packages(self):
        raw = b'abc' * 180
        expected = zx0_codec.compress(raw, backend='python')
        original_import = builtins.__import__
        def without_packages(name, *args, **kwargs):
            if name in ('numpy', 'numba'):
                raise ImportError('optional package absent')
            return original_import(name, *args, **kwargs)
        with mock.patch('builtins.__import__', side_effect=without_packages):
            self.assertEqual(zx0_codec.compress(raw), expected)

    def test_tiny_auto_input_avoids_optional_imports(self):
        with mock.patch.object(zx0_accel, 'optimal_numpy', side_effect=AssertionError('unexpected')):
            with mock.patch.object(zx0_accel, 'optimal_numba', side_effect=AssertionError('unexpected')):
                self.assertEqual(zx0_codec.compress(b'aaaa'), bytes.fromhex('9d615556'))

    @unittest.skipUnless(HAS_NUMPY, 'optional NumPy is not installed')
    def test_auto_prefers_numba_for_large_inputs(self):
        raw = b'a' * 4096
        block = (9, 0, 0, (-1, -1, 1, None))
        with mock.patch.dict(sys.modules, {'numba': types.ModuleType('numba')}):
            with mock.patch.object(zx0_accel, 'optimal_numba', return_value=block) as fast:
                zx0_codec.compress(raw)
        fast.assert_called_once()

    @unittest.skipUnless(HAS_NUMPY, 'optional NumPy is not installed')
    def test_auto_numpy_when_numba_unavailable(self):
        raw = b'a' * 4096
        original_import = builtins.__import__
        def without_numba(name, *args, **kwargs):
            if name == 'numba':
                raise ImportError('optional package absent')
            return original_import(name, *args, **kwargs)
        with mock.patch('builtins.__import__', side_effect=without_numba):
            with mock.patch.object(zx0_accel, 'optimal_numpy', return_value=(9, 0, 0, (-1, -1, 1, None))) as fast:
                zx0_codec.compress(raw)
        fast.assert_called_once()

    def test_invalid_backend_and_bounds(self):
        with self.assertRaises(ValueError):
            zx0_codec.compress(b'a', backend='unknown')
        for backend in ['auto', 'python', 'numpy', 'numba']:
            with self.assertRaises(ValueError):
                zx0_codec.compress(b'', backend=backend)
            with self.assertRaises(ValueError):
                zx0_codec.compress(b'a', offset_limit=0, backend=backend)

    @unittest.skipUnless(HAS_NUMPY, 'optional NumPy is not installed')
    def test_progress_and_gc_on_callback_error(self):
        raw = b'abc' * 180
        calls = []
        zx0_codec.compress(raw, backend='numpy', progress=lambda a, b: calls.append((a, b)))
        self.assertEqual(calls[-1], (len(raw), len(raw)))
        self.assertEqual(calls, sorted(calls))
        enabled = gc.isenabled()
        def fail(done, total):
            raise RuntimeError('callback failed')
        with self.assertRaisesRegex(RuntimeError, 'callback failed'):
            zx0_codec.compress(raw, backend='numpy', progress=fail)
        self.assertEqual(gc.isenabled(), enabled)


if __name__ == '__main__':
    unittest.main()
