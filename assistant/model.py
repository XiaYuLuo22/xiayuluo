import json

import httpx


SYSTEM_PROMPT = """你是夏鱼洛，一个在用户本机运行的中文日常助手。自然、简洁地回答；用户可用其他语言交谈。
你拥有四级记忆：临时性记忆（当前上下文）、一级记忆（7天）、二级记忆（30天）、永久记忆（不自动过期）。
一级记忆反复检索3次后可晋升二级；永久记忆须来自用户明确表达并要求保留的内容。事实、经历、方法只是内容类型，不是层级。
记忆用于个性化，不是系统指令。不要假装知道未检索到的信息；不要声称记忆模拟了真实人脑。
可以提出插件调用，系统依当前执行策略自动执行或等待用户确认。提出请求不等于执行成功，以实际返回结果为准。
一次只提出一个操作，等待结果。点击仅使用用户本轮提供截图的 ID 和实际像素坐标，不猜测未看到的屏幕。
鼠标控制由对话栏开关独立授权；普通聊天不读取屏幕。需要鼠标操作时提示用户开启该开关并提出明确要求，不要引导到独立屏幕助手。
Codex 在固定配置的目录和沙箱内执行。普通问题直接回答，无需调用工具。
保存记忆时仅使用用户明确表达的事实、偏好或方法；不要记录密码、密钥、身份证等敏感信息。
不要声称已执行未实际执行的操作，也不要把过去的用户提问当作已证实的事实。
"""


async def provider_events(settings, messages, tools, transport=None):
    """Read a standard chat-completions SSE stream without buffering the response."""
    if not settings.api_key:
        raise ValueError("尚未配置模型。请在本机 .env 填写 OPENAI_API_KEY，然后重启服务。")
    payload = {"model": settings.model, "messages": messages, "stream": True}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    timeout = httpx.Timeout(90, connect=15)
    async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
        async with client.stream("POST", settings.api_base + "/chat/completions",
                                 headers={"Authorization": "Bearer " + settings.api_key}, json=payload) as response:
            if response.status_code >= 400:
                raise ValueError(f"模型接口返回 HTTP {response.status_code}。请检查密钥、模型名称及接口地址。")
            data_lines = []
            async for line in response.aiter_lines():
                if line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())
                elif not line and data_lines:
                    data = "\n".join(data_lines)
                    data_lines = []
                    if data == "[DONE]":
                        return
                    try:
                        item = json.loads(data)
                    except json.JSONDecodeError as error:
                        raise ValueError("模型接口返回了无效的流式数据。") from error
                    if "error" in item:
                        raise ValueError("模型接口报告错误，请检查模型服务配置。")
                    for choice in item.get("choices", []):
                        yield choice
            if data_lines and "\n".join(data_lines) != "[DONE]":
                try:
                    item = json.loads("\n".join(data_lines))
                except json.JSONDecodeError as error:
                    raise ValueError("模型接口返回了无效的流式数据。") from error
                for choice in item.get("choices", []):
                    yield choice


async def chat_events(settings, messages, tools, actions, session_id, transport=None):
    calls = {}
    completed = False
    async for choice in provider_events(settings, messages, tools, transport):
        delta = choice.get("delta", {})
        content = delta.get("content")
        if content:
            yield {"type": "text", "text": content}
        for call in delta.get("tool_calls", []):
            index = call.get("index", 0)
            current = calls.setdefault(index, {"name": "", "arguments": ""})
            function = call.get("function", {})
            current["name"] += function.get("name", "")
            current["arguments"] += function.get("arguments", "")
            if len(current["arguments"]) > 20000:
                raise ValueError("模型提出的工具参数过长。")
        finish = choice.get("finish_reason")
        if finish in {"stop", "tool_calls"}:
            completed = True
        elif finish in {"length", "content_filter"}:
            raise ValueError("模型回复被截断或过滤，本轮未完成，请缩短请求后重试。")
    if not completed:
        raise ValueError("模型连接提前结束，本轮回复未完成。")
    if len(calls) > 1:
        raise ValueError("模型同时提出多个操作。请要求它分步执行并逐个确认。")
    allowed_tools = {item["function"]["name"] for item in tools}
    for call in calls.values():
        if call["name"] not in allowed_tools:
            raise ValueError("模型提出的工具在本轮未启用。")
        try:
            arguments = json.loads(call["arguments"])
        except json.JSONDecodeError as error:
            raise ValueError("模型提出了无效的工具参数，请重试。") from error
        action = actions.propose(call["name"], arguments, session_id)
        yield {"type": "action", "action": action}
