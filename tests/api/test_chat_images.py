"""Чат: отправка изображений — API-контракт ``POST /store/chat/image-message``.

Тест идёт как гость витрины: ``POST /store/chat/session`` с блоком ``guest``
(вход покупателя на стенде лимитирован 5 разами в час, гостевой поток стоит один
запрос), дальше каждый запрос несёт заголовок ``x-tamir-chat-guest``. Созданный
поток удаляется ``POST /store/chat/delete`` в финализаторе фикстуры — переписка
не остаётся на стенде даже если тест упал в середине.

Контракт, проверенный живыми запросами на ``test-api.tamir.rs`` (2026-11-27):

* png / jpeg / webp (data-url, декодируются в ~2-90 КБ) -> ``200`` с телом
  ``{"id": <message id>}``; пустой ``text`` рядом с картинкой разрешён;
* ``GET /store/chat/messages?id=<поток>`` отдаёт это сообщение с ``has_image: true``;
* jpeg весом чуть больше 5 MiB (но тело запроса меньше 7 MiB) -> ``413`` и
  ``{"code": "CHAT_IMAGE_TOO_LARGE"}`` — это правило самого чата
  (``backend/src/utils/chat/images.ts``);
* png весом больше 6 MiB -> его data-url (~8 МБ) не проходит лимит тела маршрута
  ``image-message`` (``bodyParser`` = 7 MiB, ``backend/src/api/middlewares.ts``):
  ``413`` и ``{"code": "PAYLOAD_TOO_LARGE"}``. Так что у очень больших файлов
  падает сначала Medusa, а правило чата до них просто не доезжает — коды разные,
  и оба не 5xx;
* gif (формат вне белого списка) и pdf, притворившийся png, -> ``400`` и
  ``{"code": "CHAT_IMAGE_INVALID"}``: бэкенд смотрит на содержимое, а не только
  на mime, поэтому 500 здесь нет и быть не должно;
* повтор с тем же ``clientId`` -> ``200`` и тот же ``id`` сообщения, дубликата в
  ленте нет;
* лимит обращений к чату на стенде — 10 ``image-message`` в минуту на IP, ответ
  ``429 CHAT_RATE_LIMIT`` тесты считают окружением и скипают, а не падением.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

import allure
import pytest
import requests

from qa.chat_images import (
    CHAT_IMAGE_MAX_BYTES,
    animated_gif_bytes,
    data_url,
    data_url_parts,
    jpeg_bytes,
    jpeg_over_chat_limit,
    pdf_bytes,
    png_bytes,
    png_over_body_limit,
    webp_bytes,
)

pytestmark = [pytest.mark.api]

GUEST_NAME = "QA automat"
SESSION_LOCALE = "sr"
RATE_LIMIT_SKIP = (
    "стенд отдал 429 CHAT_RATE_LIMIT: лимит обращений к чату (10 image-message в минуту на IP) "
    "исчерпан — прогоны чат-тестов нужно разносить минимум на минуту"
)

#: png / jpeg / webp — белый список бэкенда и атрибута accept у витрины.
SUPPORTED_IMAGES: list[tuple[str, Callable[[], bytes]]] = [
    ("png", png_bytes),
    ("jpeg", jpeg_bytes),
    ("webp", webp_bytes),
]


def _chat_post(http: Any, action: str, body: dict[str, Any], token: str | None = None):
    """Запрос к витринному чату: publishable-ключ уже в клиенте, токен гостя — заголовком."""
    headers = {"x-tamir-chat-guest": token} if token else {}
    return http.post(f"/store/chat/{action}", json=body, headers=headers, timeout=90.0)


def _chat_get(http: Any, path: str, token: str | None = None):
    headers = {"x-tamir-chat-guest": token} if token else {}
    return http.get(path, headers=headers, timeout=30.0)


def _code_of(response) -> str:
    """Код ошибки из ответа чата: чатные ошибки — это ``{"code": ...}``.

    ``None`` возвращает, если тела вообще нет (например, ``413`` от прокси без JSON).
    """
    try:
        payload = response.json()
    except ValueError:
        return ""
    if isinstance(payload, dict):
        return str(payload.get("code") or payload.get("message") or "")
    return ""


@dataclass
class GuestThread:
    """Одна гостевая переписка: токен, id потока и отправленные сообщения."""

    http: Any
    token: str
    conversation_id: str
    sent_ids: list[str] = field(default_factory=list)

    def post(self, action: str, body: dict[str, Any]):
        return _chat_post(self.http, action, body, self.token)

    def send_image(self, image: str, *, text: str = "", client_id: str | None = None):
        """``POST /store/chat/image-message`` — тело ровно то, что шлёт витрина.

        ``429 CHAT_RATE_LIMIT`` (10 обращений с картинкой в минуту на IP) — потолок
        стенда, а не дефект, поэтому такой ответ превращается в skip теста.
        """
        response = self.post(
            "image-message",
            {"id": self.conversation_id, "text": text, "clientId": client_id or str(uuid.uuid4()), "image": image},
        )
        if response.status_code == 429:
            pytest.skip(RATE_LIMIT_SKIP)
        return response

    def messages(self):
        response = _chat_get(self.http, f"/store/chat/messages?id={self.conversation_id}", self.token)
        if response.status_code == 429:
            pytest.skip(RATE_LIMIT_SKIP)
        return response


def _delete_thread(http: Any, token: str, conversation_id: str) -> None:
    """Убирает поток стенда и не роняет тест из-за уборки.

    Повторное удаление отдает ``404 CHAT_NOT_FOUND`` (сервер не идемпотентен),
    для финализатора это «уже удалено».
    """
    try:
        response = _chat_post(http, "delete", {"id": conversation_id}, token)
    except requests.RequestException as exc:  # сеть упала — фиксируем, но не падаем
        allure.attach(f"поток {conversation_id}: уборка не выполнена: {exc}", "chat cleanup", allure.attachment_type.TEXT)
        return
    ok = response.status_code == 200 or (response.status_code == 404 and _code_of(response) == "CHAT_NOT_FOUND")
    allure.attach(
        f"поток {conversation_id}: delete -> {response.status_code} {_code_of(response)} "
        f"{'(убран)' if ok else '(ВНИМАНИЕ: поток мог остаться на стенде)'}",
        "chat cleanup",
        allure.attachment_type.TEXT,
    )


@pytest.fixture
def guest_thread(api_client):
    """Гостевая переписка одного теста; удаление потока гарантировано финализатором."""
    http = api_client.client
    guest_email = f"qa-chat-{uuid.uuid4().hex[:10]}@tamir-qa.example"
    response = _chat_post(
        http,
        "session",
        {"guest": {"name": GUEST_NAME, "email": guest_email, "phone": ""}, "locale": SESSION_LOCALE},
    )
    if response.status_code == 429:
        pytest.skip(RATE_LIMIT_SKIP)
    assert response.status_code == 200, (
        f"POST /store/chat/session вернул {response.status_code}: {response.text[:300]!r}"
    )
    payload = response.json()
    token = str(payload.get("guest_token") or "")
    conversation_id = str((payload.get("conversation") or {}).get("id") or "")
    assert token and conversation_id, f"в ответе сессии нет guest_token/conversation: {list(payload)}"

    thread = GuestThread(http=http, token=token, conversation_id=conversation_id)
    allure.attach(
        f"поток {conversation_id} (гость {guest_email}), locale={SESSION_LOCALE}",
        "гостевая переписка",
        allure.attachment_type.TEXT,
    )
    try:
        yield thread
    finally:
        _delete_thread(http, token, conversation_id)


@allure.story("Чат: отправка изображений")
@allure.severity(allure.severity_level.CRITICAL)
@pytest.mark.parametrize("fmt,builder", SUPPORTED_IMAGES, ids=[fmt for fmt, _ in SUPPORTED_IMAGES])
def test_supported_images_accepted(guest_thread, steps, fmt: str, builder: Callable[[], bytes]):
    """png / jpeg / webp принимаются чатом, сообщение с картинкой попадает в ленту."""
    raw = builder()
    image = data_url(f"image/{fmt}", raw)
    mime, decoded = data_url_parts(image)

    with steps(f"отправить в чат {fmt} ({len(raw)} байт) без текста",
               "200 и id сообщения: пустой текст рядом с картинкой разрешён"):
        response = guest_thread.send_image(image, text="")
        assert response.status_code == 200, (
            f"отправка {fmt} вернула {response.status_code} (код={_code_of(response)!r}): "
            f"{response.text[:300]!r}"
        )
        message_id = str((response.json() or {}).get("id") or "")
        assert message_id, f"в ответе нет id сообщения: {response.text[:200]!r}"
        guest_thread.sent_ids.append(message_id)
        allure.attach(
            f"{fmt}: mime={mime} байт={len(decoded)} data-url={len(image)} id={message_id}",
            f"отправлен {fmt}",
            allure.attachment_type.TEXT,
        )

    with steps("прочитать сообщения переписки", "сообщение с has_image=true и автор customer"):
        response = guest_thread.messages()
        assert response.status_code == 200, f"GET messages вернул {response.status_code}: {response.text[:200]!r}"
        mine = [m for m in response.json().get("messages", []) if str(m.get("id")) == message_id]
        assert mine, f"сообщение {message_id} не появилось в GET /store/chat/messages"
        assert mine[0].get("has_image") is True, f"у сообщения has_image={mine[0].get('has_image')!r}, ожидался true"
        assert mine[0].get("sender") == "customer", f"sender={mine[0].get('sender')!r}, ожидался customer"


@allure.story("Чат: отправка изображений")
@allure.severity(allure.severity_level.NORMAL)
def test_image_over_five_mebibytes_rejected(guest_thread, steps):
    """Файл больше 5 MiB отбивается правилом чата: 413 CHAT_IMAGE_TOO_LARGE, не 500."""
    raw = jpeg_over_chat_limit()
    image = data_url("image/jpeg", raw)

    with steps(f"отправить jpeg {len(raw)} байт (>5 MiB, тело запроса <7 MiB)",
               "413 и код CHAT_IMAGE_TOO_LARGE"):
        assert len(raw) > CHAT_IMAGE_MAX_BYTES, (
            f"фикстура должна быть больше 5 MiB, а получилась {len(raw)} байт"
        )
        response = guest_thread.send_image(image)
        assert response.status_code == 413, (
            f"изображение >5 MiB вернуло {response.status_code} (код={_code_of(response)!r}), "
            f"ожидался 413: {response.text[:300]!r}"
        )
        assert _code_of(response) == "CHAT_IMAGE_TOO_LARGE", (
            f"код ошибки {_code_of(response)!r}, ожидался CHAT_IMAGE_TOO_LARGE"
        )
        assert not 500 <= response.status_code < 600, "ошибка размера не должна быть 5xx"
        allure.attach(f"jpeg {len(raw)} байт -> 413 CHAT_IMAGE_TOO_LARGE", "правило чата", allure.attachment_type.TEXT)


@allure.story("Чат: отправка изображений")
@allure.severity(allure.severity_level.NORMAL)
def test_body_over_route_limit_rejected_without_server_error(guest_thread, steps):
    """Огромный png отсекает лимит тела маршрута (7 MiB): 413 PAYLOAD_TOO_LARGE, не 500.

    Проверенное поведение стенда: data-url от png в 6 MiB весит ~8 МБ и не доходит
    до правила чата про 5 MiB — Medusa отвечает ``PAYLOAD_TOO_LARGE``. Это не баг
    (ответ корректный 4xx), но фронтенд и чат описывают пользователю лимит «5 МБ»,
    а реальная граница отказа тела запроса — 7 МБ тела, о чём говорит код ответа.
    """
    raw = png_over_body_limit()
    image = data_url("image/png", raw)

    with steps(f"отправить png {len(raw)} байт (data-url ~{len(image) // 1024} КБ)",
               "413 и код PAYLOAD_TOO_LARGE (лимит тела маршрута), не 5xx"):
        response = guest_thread.send_image(image)
        assert 400 <= response.status_code < 500, (
            f"огромное тело вернуло {response.status_code} (код={_code_of(response)!r}) — "
            f"ожидался 4xx, не ответ сервера: {response.text[:300]!r}"
        )
        assert response.status_code == 413, (
            f"ожидался 413 PAYLOAD_TOO_LARGE, получился {response.status_code}: {response.text[:200]!r}"
        )
        assert _code_of(response) == "PAYLOAD_TOO_LARGE", (
            f"код ошибки {_code_of(response)!r}, ожидался PAYLOAD_TOO_LARGE — если лимит тела "
            "подняли, тест нужно перевести на CHAT_IMAGE_TOO_LARGE"
        )


@allure.story("Чат: отправка изображений")
@allure.severity(allure.severity_level.CRITICAL)
@pytest.mark.parametrize(
    "label,image",
    [
        ("gif", data_url("image/gif", animated_gif_bytes())),
        ("pdf под mime image/png", data_url("image/png", pdf_bytes())),
    ],
    ids=["gif", "pdf-as-png"],
)
def test_non_image_content_rejected(guest_thread, steps, label: str, image: str):
    """gif и pdf отбиваются валидацией содержимого: 400 CHAT_IMAGE_INVALID, не 500."""
    with steps(f"отправить {label}", "400 и код CHAT_IMAGE_INVALID, сервер не упал"):
        response = guest_thread.send_image(image)
        assert not 500 <= response.status_code < 600, (
            f"{label} вызвал ответ сервера {response.status_code}: {response.text[:300]!r}"
        )
        assert response.status_code == 400, (
            f"{label} вернул {response.status_code} (код={_code_of(response)!r}), ожидался 400"
        )
        assert _code_of(response) == "CHAT_IMAGE_INVALID", (
            f"код ошибки {_code_of(response)!r}, ожидался CHAT_IMAGE_INVALID"
        )
        allure.attach(f"{label}: {response.status_code} {_code_of(response)}", "валидация формата", allure.attachment_type.TEXT)


@allure.story("Чат: отправка изображений")
@allure.severity(allure.severity_level.NORMAL)
def test_repeated_client_id_does_not_duplicate_image(guest_thread, steps):
    """Повтор того же ``clientId`` (витрина шлёт его при ретраях) не множит картинки."""
    image = data_url("image/png", png_bytes())
    client_id = str(uuid.uuid4())

    with steps("отправить картинку и повторить тот же запрос с тем же clientId",
               "оба ответа 200 с одинаковым id сообщения"):
        first = guest_thread.send_image(image, text="первый", client_id=client_id)
        assert first.status_code == 200, f"первый запрос: {first.status_code} {first.text[:200]!r}"
        second = guest_thread.send_image(image, text="первый", client_id=client_id)
        assert second.status_code == 200, f"повтор: {second.status_code} {second.text[:200]!r}"
        first_id = str((first.json() or {}).get("id") or "")
        second_id = str((second.json() or {}).get("id") or "")
        assert first_id and first_id == second_id, (
            f"повтор с тем же clientId создал другое сообщение: {first_id!r} -> {second_id!r}"
        )

    with steps("посчитать картинки в ленте", "одна картинка на clientId"):
        messages = guest_thread.messages().json().get("messages", [])
        with_image = [m for m in messages if m.get("has_image")]
        assert len(with_image) == 1, (
            f"после повтора в ленте {len(with_image)} картинок вместо одной: "
            f"{[m.get('id') for m in with_image]}"
        )
