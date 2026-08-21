"""Generate IQEngine-compatible visual sidecars for SigMF recordings."""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np

_THUMBNAIL_FFT_SIZE = 512
_THUMBNAIL_SKIP_BYTES = 256_000
_THUMBNAIL_READ_BYTES = _THUMBNAIL_FFT_SIZE * 1024
_MINIMAP_FFT_SIZE = 64
_MINIMAP_BLOCKS = 200

_SOURCE_DTYPES: dict[str, tuple[np.dtype, int]] = {
    "cf32": (np.dtype("<c8"), 8),
    "cf32_le": (np.dtype("<c8"), 8),
    "cu8": (np.dtype("u1"), 2),
    "cu8_le": (np.dtype("u1"), 2),
    "ci8": (np.dtype("i1"), 2),
    "ci8_le": (np.dtype("i1"), 2),
    "i8": (np.dtype("i1"), 2),
    "cs16": (np.dtype("<i2"), 4),
    "ci16": (np.dtype("<i2"), 4),
    "ci16_le": (np.dtype("<i2"), 4),
    "cu16": (np.dtype("<u2"), 4),
    "cu16_le": (np.dtype("<u2"), 4),
    "cs32": (np.dtype("<i4"), 8),
    "ci32": (np.dtype("<i4"), 8),
    "ci32_le": (np.dtype("<i4"), 8),
    "cu32": (np.dtype("<u4"), 8),
    "cu32_le": (np.dtype("<u4"), 8),
    "f16": (np.dtype("<f2"), 4),
    "f16_le": (np.dtype("<f2"), 4),
    "f32": (np.dtype("<f4"), 8),
    "f32_le": (np.dtype("<f4"), 8),
    "cf64": (np.dtype("<c16"), 16),
    "cf64_le": (np.dtype("<c16"), 16),
}


def supported_iqengine_datatypes() -> tuple[str, ...]:
    """Return datatypes supported by the IQEngine sidecar generators."""
    return tuple(sorted(_SOURCE_DTYPES))


def _read_byte_window(
    file_paths: list[str | Path],
    *,
    skip_bytes: int,
    read_bytes: int,
) -> bytes:
    chunks: list[bytes] = []
    remaining_skip = skip_bytes
    remaining_read = read_bytes

    for path_value in file_paths:
        path = Path(path_value)
        size = path.stat().st_size
        if remaining_skip >= size:
            remaining_skip -= size
            continue
        with path.open("rb") as f:
            f.seek(remaining_skip)
            remaining_skip = 0
            chunk = f.read(remaining_read)
        chunks.append(chunk)
        remaining_read -= len(chunk)
        if remaining_read <= 0:
            break
    return b"".join(chunks)


def _iqengine_samples(data_bytes: bytes, datatype: str) -> np.ndarray:
    if datatype in {"ci8", "ci8_le", "i8"}:
        scalars = np.frombuffer(data_bytes, dtype=np.int8).astype(np.float32)
        scalars /= 127.0
        return scalars[::2] + 1j * scalars[1::2]
    if datatype in {"cu8", "cu8_le"}:
        scalars = np.frombuffer(data_bytes, dtype=np.uint8).astype(np.float32)
        scalars -= 127.0
        scalars /= 127.0
        return scalars[::2] + 1j * scalars[1::2]
    if datatype in {"cs16", "ci16", "ci16_le"}:
        scalars = np.frombuffer(data_bytes, dtype="<i2").astype(np.float32)
        scalars /= 32767.0
        return scalars[::2] + 1j * scalars[1::2]
    if datatype in {"cu16", "cu16_le"}:
        scalars = np.frombuffer(data_bytes, dtype="<u2").astype(np.float32)
        scalars -= 32767.0
        scalars /= 32767.0
        return scalars[::2] + 1j * scalars[1::2]
    if datatype in {"cs32", "ci32", "ci32_le"}:
        scalars = np.frombuffer(data_bytes, dtype="<i4").astype(np.float32)
        scalars /= 2147483647.0
        return scalars[::2] + 1j * scalars[1::2]
    if datatype in {"cu32", "cu32_le"}:
        scalars = np.frombuffer(data_bytes, dtype="<u4").astype(np.float32)
        scalars -= 2147483647.0
        scalars /= 2147483647.0
        return scalars[::2] + 1j * scalars[1::2]
    if datatype in {"f16", "f16_le"}:
        scalars = np.frombuffer(data_bytes, dtype="<f2").astype(np.float32)
        return scalars[::2] + 1j * scalars[1::2]
    if datatype in {"f32", "f32_le"}:
        scalars = np.frombuffer(data_bytes, dtype="<f4")
        return scalars[::2] + 1j * scalars[1::2]
    if datatype in {"cf32", "cf32_le"}:
        return np.frombuffer(data_bytes, dtype="<c8")
    if datatype in {"cf64", "cf64_le"}:
        return np.frombuffer(data_bytes, dtype="<c16").astype(np.complex64)
    raise ValueError(f"Unsupported IQEngine datatype {datatype!r}")


def render_iqengine_thumbnail_jpeg(
    content: bytes,
    datatype: str,
    *,
    fft_size: int = _THUMBNAIL_FFT_SIZE,
) -> bytes:
    """Render the same single spectrogram thumbnail shape IQEngine stores as .jpg."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from PIL import Image

    samples = _iqengine_samples(content, datatype)
    if len(samples) < fft_size:
        samples = np.pad(samples.astype(np.complex64, copy=False), (0, fft_size - len(samples)))
    num_rows = int(np.floor(len(samples) / fft_size))
    spectrogram = np.zeros((num_rows, fft_size))
    for i in range(num_rows):
        row = samples[i * fft_size: (i + 1) * fft_size]
        spectrogram[i, :] = 10 * np.log10(
            np.maximum(np.abs(np.fft.fftshift(np.fft.fft(row))) ** 2, 1e-20)
        )

    fig = plt.figure(frameon=False)
    ax = plt.Axes(fig, [0.0, 0.0, 1.0, 1.0])
    ax.set_axis_off()
    fig.add_axes(ax)
    ax.imshow(spectrogram, cmap="viridis", aspect="auto", vmin=30 + np.min(spectrogram))
    img_buf = io.BytesIO()
    plt.savefig(img_buf, bbox_inches="tight", pad_inches=0)
    plt.close(fig)
    img_buf.seek(0)

    image = Image.open(img_buf)
    out = io.BytesIO()
    image.convert("RGB").save(out, format="jpeg")
    return out.getvalue()


def render_iqengine_thumbnail_jpeg_from_files(
    file_paths: list[str | Path],
    datatype: str,
) -> bytes:
    """Read IQEngine's thumbnail window and render the sidecar JPEG."""
    content = _read_byte_window(
        file_paths,
        skip_bytes=_THUMBNAIL_SKIP_BYTES,
        read_bytes=_THUMBNAIL_READ_BYTES,
    )
    if not content:
        content = _read_byte_window(file_paths, skip_bytes=0, read_bytes=_THUMBNAIL_READ_BYTES)
    return render_iqengine_thumbnail_jpeg(content, datatype)


def _sample_count(file_paths: list[str | Path], datatype: str) -> int:
    _, bytes_per_sample = _SOURCE_DTYPES[datatype]
    return sum(Path(path).stat().st_size for path in file_paths) // bytes_per_sample


def _read_cf32_at(
    file_paths: list[str | Path],
    datatype: str,
    sample_index: int,
    n_samples: int,
) -> np.ndarray:
    _, bytes_per_sample = _SOURCE_DTYPES[datatype]
    raw = _read_byte_window(
        file_paths,
        skip_bytes=sample_index * bytes_per_sample,
        read_bytes=n_samples * bytes_per_sample,
    )
    samples = _iqengine_samples(raw, datatype).astype(np.complex64, copy=False)
    if len(samples) < n_samples:
        samples = np.pad(samples, (0, n_samples - len(samples)))
    return samples[:n_samples]


def render_iqengine_minimap_from_files(
    file_paths: list[str | Path],
    datatype: str,
) -> bytes:
    """Return binary minimap bytes used by IQEngine's fast overview loader."""
    total_samples = _sample_count(file_paths, datatype)
    total_ffts = total_samples // _MINIMAP_FFT_SIZE
    if total_ffts <= 0:
        n_bytes = _MINIMAP_BLOCKS * _MINIMAP_FFT_SIZE * np.dtype("<c8").itemsize
        return bytes(n_bytes)

    blocks = []
    for i in range(_MINIMAP_BLOCKS):
        fft_index = int(i * total_ffts / _MINIMAP_BLOCKS)
        sample_index = fft_index * _MINIMAP_FFT_SIZE
        blocks.append(_read_cf32_at(file_paths, datatype, sample_index, _MINIMAP_FFT_SIZE))
    return np.concatenate(blocks).astype("<c8", copy=False).tobytes()
