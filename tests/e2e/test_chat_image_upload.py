"""E2E-20. Чат: отправка изображений из интерфейса (гость).

Гость открывает виджет, заводит переписку «Započni razgovor» (на стенде вход
покупателя лимитирован 5 разами в час, гостевой поток бесплатен) и цепляет файлы
кнопкой «Dodaj sliku». Картинки из этого теста удаляются вместе с перепиской:
фикстура ``chat_ui_threads`` вычитывает guest_token из cookie ``tb_chat_guest``
и удаляет поток ``POST /store/chat/delete`` — на стенде не остаётся мусора.

ЗАМЕТКИ ПО КОНТРАКТУ (сверено с ChatWidget.tsx / chat-client.ts и живым стендом):
1) Подвал ``footer.tamir-chat-compose`` с кнопкой картинки существует только когда
   есть переписка, поэтому даже негативные проверки сначала жмут «Započni razgovor»
   — эти потоки тоже чистятся фикстурой.
2) Витрина отсекает плохие файлы до сети (``chat-client.ts: imageFileError``): mime
   вне jpg/png/webp и размер >5 MiB дают ``role=alert`` с текстом ``chat.imageInvalid``
   / ``chat.imageTooLarge`` без единого запроса к чату; превью
   ``div.tamir-chat-attachment`` не появляется. ``set_input_files`` обходит фильтр
   ``accept`` — проверка идёт по содержимому.
3) Ответ бэкенда строже интерфейса: файл «.png» с pdf-байтами витрина пропускает
   (mime берётся из расширения), а ``sharp`` на бэкенде отвечает
   ``400 CHAT_IMAGE_INVALID`` — это закрывает API-набор tests/api/test_chat_images.py.
4) Границы размера разъехались: интерфейс обещает 5 MB, Medusa отсекает тело запроса
   позже (7 MiB, ``PAYLOAD_TOO_LARGE``) — коды разные, оба не 5xx.
"""

from __future__ import annotations

import uuid
from typing import Callable

import allure
import pytest
import requests

from qa.chat_images import (
    animated_gif_bytes,
    jpeg_bytes,
    jpeg_over_chat_limit,
    pdf_bytes,
    png_bytes,
)
from qa.ui import (
    CHAT_ALERT,
    CHAT_ATTACH_INPUT,
    CHAT_ATTACHMENT,
    CHAT_COMPOSE,
    CHAT_CLOSE_BUTTONS,
    CHAT_GUEST_COOKIE,
    CHAT_GUEST_EMAIL_INPUT,
    CHAT_GUEST_NAME_INPUT,
    CHAT_LAUNCHER,
    CHAT_PANEL,
    CHAT_SEND_BUTTON,
    CHAT_SENT_IMAGE,
    CHAT_START_BUTTON,
    Storefront,
)

pytestmark = [pytest.mark.e2e]

TEXTAREA_LABEL = '[aria-label="Poruka"]'  # sr.json: chat.message


def _guest_email() -> str:
    """Уникальный e-mail гостя: новый поток на каждую проверку."""
    return f"qa-e2e-chat-img+{uuid.uuid4().hex[:10]}@tamir.qa.example"


def _wait_widget_hydrated(shop: Storefront) -> None:
    """Виджет грузится ``next/dynamic`` без SSR: ждём, пока он вообще появится."""
    if not shop.wait_until(lambda: shop.page.locator(CHAT_LAUNCHER).count() > 0, 15.0):
        pytest.fail(
            "За 15 с на странице так и не появился виджет чата "
            "(кнопка button.tamir-chat-launcher). Витрина на стенде отдаёт "
            "чата-бандл из Next-прокси; возможно, frontend-сборка на стенде "
            "без чата — проверить деплой, а не тест."
        )


def _open_widget_and_start(shop: Storefront) -> None:
    """Открыть виджет и завести гостевую переписку «Započni razgovor»."""
    page = shop.page
    with allure.step("Открыть виджет чата"):
        page.locator(CHAT_LAUNCHER).click()
        panel = page.locator(CHAT_PANEL)
        panel.wait_for(state="visible", timeout=10_000)
        # если у этого cookie уже был поток — виджет покажет подвал сразу
        if page.locator(CHAT_COMPOSE).count():
            return
    with allure.step("Завести гостевую переписку (имя + e-mail)"):
        page.locator(CHAT_GUEST_NAME_INPUT).fill("QA Chat Image Bot")
        page.locator(CHAT_GUEST_EMAIL_INPUT).fill(_guest_email())
        page.locator(CHAT_START_BUTTON).click()
        page.locator(CHAT_COMPOSE).wait_for(state="visible", timeout=15_000)


def _attach(shop: Storefront, path: str) -> None:
    """Выбрать файл кнопкой «Dodaj sliku» (``set_input_files`` обходит accept)."""
    shop.page.locator(CHAT_ATTACH_INPUT).set_input_files(path)


def _write_fixture(tmp_path_factory, name: str, maker: Callable[[str], bytes], **kw) -> str:
    out = tmp_path_factory.mktemp("chat-images") / name
    out.write_bytes(maker(**kw) if kw else maker())
    return str(out)


@pytest.fixture
def chat_ui_threads(page, api_client):
    """Чистит гостевые переписки, заведённые UI-тестом.

    Гостевой токен витрина хранит в httpOnly cookie ``tb_chat_guest`` — из JS его
    не достать, поэтому читаем cookie через Playwright и удаляем поток напрямую
    через ``POST /store/chat/delete``. Повторный delete на стенде отвечает 404
    ``CHAT_NOT_FOUND`` — это уже чистая переписка, считаем успехом.
    """

    yield

    with allure.step("Удалить переписку чата, заведённую тестом"):
        cookies = [c for c in page.context.cookies() if c["name"] == CHAT_GUEST_COOKIE]
        token = next((c["value"] for c in cookies if c["value"]), None)
        if not token:
            return
        resp = api_client.client.post(
            "/store/chat/delete",
            json={},
            headers={"x-tamir-chat-guest": token},
        )
        if resp.status_code == 404:
            return  # поток уже удалён — идеально
        assert resp.status_code == 200, (
            f"DELETE чата не прошёл: {resp.status_code} {resp.text[:160]}. "
            "Токен гостя берём из cookie tb_chat_guest; если 401 — истёк/подменён."
        )


class TestChatImageUploadUi:
    """E2E-20. Картинки в чате витрины: png/jpeg уходят, gif/pdf/big блокируются."""

    def test_png_and_jpeg_appear_in_chat(
        self, page, chat_ui_threads, steps, tmp_path_factory
    ):
        """PNG и JPG до 5 MiB: превью → отправка → картинка в ленте сообщений."""
        shop = Storefront(page)
        shop.goto_catalog()
        _wait_widget_hydrated(shop)
        _open_widget_and_start(shop)

        png = _write_fixture(tmp_path_factory, "good.png", png_bytes)
        jpg = _write_fixture(tmp_path_factory, "good.jpg", jpeg_bytes)
        page = shop.page

        with allure.step("Приложить PNG и отправить"):
            _attach(shop, png)
            preview = page.locator(CHAT_ATTACHMENT)
            preview.wait_for(state="visible", timeout=10_000)
            assert preview.locator("img").count() == 1, (
                "Превью выбранной картинки не отрисовалось "
                "(ожидан div.tamir-chat-attachment с img)."
            )
            page.locator(f'{TEXTAREA_LABEL}').fill("QA E2E chat image PNG")
            page.locator(CHAT_SEND_BUTTON).click()

        with allure.step("PNG появился в панели сообщений"):
            image = page.locator(CHAT_SENT_IMAGE)
            if not shop.wait_until(lambda: image.count() >= 1, 15.0):
                pytest.fail(
                    "Через 15 с в панели чата нет img.tamir-chat-image после "
                    "отправки PNG. Дальше смотреть: POST /api/chat/image-message "
                    "во вкладке Network и лог medusa-chat pm2 на стенде."
                )

        with allure.step("Приложить JPG и отправить"):
            _attach(shop, jpg)
            page.locator(f'{TEXTAREA_LABEL}').fill("QA E2E chat image JPG")
            page.locator(CHAT_SEND_BUTTON).click()

        with allure.step("Обе картинки в ленте"):
            if not shop.wait_until(lambda: page.locator(CHAT_SENT_IMAGE).count() >= 2, 15.0):
                pytest.fail(
                    "После отправки JPG в ленте меньше двух img.tamir-chat-image. "
                    "Витрина после send перезагружает переписку из GET "
                    "/api/chat/messages — проверить, что image=true у обеих."
                )
        with steps("картинки в панели сообщений",
                   "обе отправленные картинки отображаются как img.tamir-chat-image"):
            assert page.locator(CHAT_SENT_IMAGE).count() >= 2

    @pytest.mark.parametrize(
        "maker,filename,expect_fragment",
        [
            pytest.param(
                animated_gif_bytes, "bad.gif", "stati",
                id="gif-blokiruetsya-v-interface",
            ),
            pytest.param(
                pdf_bytes, "document.pdf", "stati",
                id="pdf-blokiruetsya-v-interface",
            ),
            pytest.param(
                jpeg_over_chat_limit, "big.jpg", "5 MB",
                id="jbolshie-5mb-blokiruetsya",
            ),
        ],
    )
    def test_bad_files_blocked_in_ui(
        self, page, chat_ui_threads, steps, tmp_path_factory,
        maker, filename, expect_fragment,
    ):
        """gif / pdf / jpeg >5 MiB: витрина блокирует до сети, превью нет."""
        shop = Storefront(page)
        shop.goto_catalog()
        _wait_widget_hydrated(shop)
        _open_widget_and_start(shop)

        path = _write_fixture(tmp_path_factory, filename, maker)
        page = shop.page

        with allure.step(f"Приложить {filename} кнопкой «Dodaj sliku»"):
            _attach(shop, path)
            alert = page.locator(CHAT_ALERT)
            alert.wait_for(state="visible", timeout=10_000)
            text = alert.inner_text()

        with steps(f"витрина отклонила {filename}",
                   "role=alert с текстом про картинки, превью div.tamir-chat-attachment нет"):
            assert expect_fragment in text, (
                f"Для {filename} в role=alert нет ожидаемого текста про картинки "
                f"(ожидали фрагмент {expect_fragment!r}), получили: {text!r}. "
                "Строки витрины: chat.imageInvalid / chat.imageTooLarge из sr.json."
            )
            assert page.locator(CHAT_ATTACHMENT).count() == 0, (
                f"Витрина показала превью для запрещённого файла {filename} — "
                "файл ушёл бы в чат, а не отклонён на клиенте."
            )
