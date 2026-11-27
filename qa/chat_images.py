"""Картинки для тестов чата: собираются кодом на лету, бинарников в git нет.

Стенд принимает в ``POST /store/chat/image-message`` data-url вида
``data:image/(jpeg|png|webp);base64,...`` (см. ``backend/src/utils/chat/images.ts``):
после base64-декодирования не больше 5 MiB, не больше 20 МП, соотношение сторон
не больше 20:1, а ``sharp`` перекодировал кадр без ошибок (поэтому GIF, SVG и
«png» из pdf-байтов отбиваются кодом ``CHAT_IMAGE_INVALID``).

Здесь ровно то, что нужно тестам: валидный png/jpeg/webp, заведомо невалидные
gif и pdf, и два размера-применителя — чуть больше 5 MiB (правило чата) и больше
7 MiB тела запроса (лимит ``bodyParser`` у маршрута).
"""

from __future__ import annotations

import base64
import io
import os

from PIL import Image

#: Decoded (base64) image size the chat itself allows — 5 MiB.
CHAT_IMAGE_MAX_BYTES = 5 * 1024 * 1024

MIME_BY_FORMAT = {"png": "image/png", "jpeg": "image/jpeg", "webp": "image/webp"}


def data_url(mime: str, raw: bytes) -> str:
    """Data-url ровно в том виде, который отдаёт витрина (``FileReader`` + base64)."""
    return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")


def data_url_parts(url: str) -> tuple[str, bytes]:
    """Разбирает data-url на mime и декодированные байты (для проверок в тестах)."""
    header, _, payload = url.partition(",")
    mime = header[len("data:") :].split(";", 1)[0]
    return mime, base64.b64decode(payload)


def _save(image: Image.Image, fmt: str, **params: object) -> bytes:
    buf = io.BytesIO()
    image.save(buf, fmt, **params)
    return buf.getvalue()


def noise_image(width: int, height: int) -> Image.Image:
    """Шум: такие пиксели не сжимаются, поэтому размер файла предсказуем."""
    return Image.frombytes("RGB", (width, height), os.urandom(width * height * 3))


def png_bytes(size: tuple[int, int] = (320, 240)) -> bytes:
    """Валидный PNG с двумя цветами — его принимает и чат, и витрина."""
    image = Image.new("RGB", size)
    for x in range(size[0] // 2):
        for y in range(size[1]):
            image.putpixel((x, y), (18, 84, 190))
    return _save(image, "PNG")


def jpeg_bytes(size: tuple[int, int] = (320, 240)) -> bytes:
    """Валидный JPEG (шум, чтобы не сжимался в пару килобайт)."""
    return _save(noise_image(*size), "JPEG", quality=92)


def webp_bytes(size: tuple[int, int] = (320, 240)) -> bytes:
    """Валидный WebP."""
    return _save(noise_image(*size), "WEBP", quality=90)


def animated_gif_bytes() -> bytes:
    """Анимированный GIF: формат вне белого списка ``image/*`` и к тому же не static."""
    frames = [Image.new("P", (32, 32), 1), Image.new("P", (32, 32), 7)]
    return _save(frames[0], "GIF", save_all=True, append_images=frames[1:], duration=100, loop=0)


def pdf_bytes() -> bytes:
    """Настоящий (крошечный) PDF — витрина его не пропускает, а бэкенд проверяет по содержимому."""
    return (
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n"
        b"trailer<</Root 1 0 R>>\n%%EOF\n"
    )


def jpeg_over_chat_limit(min_bytes: int = CHAT_IMAGE_MAX_BYTES + 2_000) -> bytes:
    """JPEG гарантированно больше 5 MiB, но влезает в 7 MiB тела запроса.

    Размер добирается сегментами-комментариями (``FFFE``), которые jpeg-декодер
    пропускает: картинка остаётся валидной, а ``sharp`` её читает. Так проверка
    упирается именно в правило чата, а не в лимит тела запроса.
    """
    raw = jpeg_bytes((1200, 1200))
    filler = min_bytes - len(raw)
    pad = bytearray()
    while filler >= 4:
        chunk = min(filler, 65531)
        pad += b"\xff\xfe" + chunk.to_bytes(2, "big") + bytes(chunk)
        filler -= chunk + 4
    return raw[:2] + bytes(pad) + raw[2:]


def png_over_body_limit(min_bytes: int = 6 * 1024 * 1024) -> bytes:
    """PNG больше 6 MiB: его data-url не проходит уже лимит тела маршрута (7 MiB)."""
    side = int((min_bytes / 3) ** 0.5) + 1
    raw = _save(noise_image(side, side), "PNG")
    while len(raw) < min_bytes:
        side += 32
        raw = _save(noise_image(side, side), "PNG")
    return raw
