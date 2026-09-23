"""Per-event SDK views: never mutate the adapter's shared client or SDK classes."""

import copy
import json


class View:
    def __init__(self, original, **overrides):
        self._original = original
        self.__dict__.update(overrides)

    def __getattr__(self, name):
        return getattr(self._original, name)


def display_content(value, render):
    """Only human-readable leaves, never user_id, card_id, file_key, or href."""
    if isinstance(value, list):
        return [display_content(item, render) for item in value]
    if isinstance(value, dict):
        return {
            key: render(item)
            if key in {"text", "content", "title"} and isinstance(item, str)
            else display_content(item, render)
            for key, item in value.items()
        }
    return value


def content_text(value) -> str:
    if isinstance(value, list):
        return "\n".join(filter(None, (content_text(item) for item in value)))
    if isinstance(value, dict):
        return "\n".join(
            item if isinstance(item, str) and key in {"text", "content"} else content_text(item)
            for key, item in value.items()
            if isinstance(item, (dict, list)) or key in {"text", "content"}
        )
    return ""


def clone_request(request):
    cloned = copy.copy(request)
    cloned.request_body = copy.deepcopy(request.request_body)
    # lark-oapi builders set both attributes; serialization reads `body`.
    cloned.body = cloned.request_body
    return cloned


def client_view(client, directory, *, in_thread=False, sanitize=True, on_sent=None):
    def should_sanitize():
        return sanitize() if callable(sanitize) else sanitize

    async def send(method, request, *args, **kwargs):
        request = clone_request(request)
        body = request.request_body
        if in_thread:
            body.reply_in_thread = True
        payload = json.loads(body.content)
        if should_sanitize():
            payload = display_content(payload, directory.display)
            body.content = json.dumps(payload, ensure_ascii=False)
        response = await method(request, *args, **kwargs)
        if in_thread and not response.success():
            # AstrBot's stock reply helper only logs failures. Topic sends must fail visibly.
            raise RuntimeError(
                f"Feishu topic send failed: code={response.code}, msg={response.msg}"
            )
        if response.success() and on_sent:
            text = content_text(payload)
            message_id = getattr(getattr(response, "data", None), "message_id", "")
            if text and message_id:
                await on_sent(message_id, text, False)
        return response

    async def reply(request, *args, **kwargs):
        return await send(client.im.v1.message.areply, request, *args, **kwargs)

    async def create(request, *args, **kwargs):
        if in_thread:
            raise RuntimeError("Topic client refuses a top-level create; use reply instead.")
        return await send(client.im.v1.message.acreate, request, *args, **kwargs)

    message = View(client.im.v1.message, areply=reply, acreate=create)
    overrides = {"im": View(client.im, v1=View(client.im.v1, message=message))}
    if getattr(client, "cardkit", None):
        element = client.cardkit.v1.card_element

        async def content(request, *args, **kwargs):
            request = clone_request(request)
            if should_sanitize():
                request.request_body.content = directory.display(
                    request.request_body.content, streaming=True
                )
            response = await element.acontent(request, *args, **kwargs)
            if response.success() and on_sent:
                await on_sent(f"card:{request.card_id}", request.request_body.content, True)
            return response

        overrides["cardkit"] = View(
            client.cardkit,
            v1=View(client.cardkit.v1, card_element=View(element, acontent=content)),
        )
    return View(client, **overrides)
