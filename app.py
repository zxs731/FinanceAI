# app.py

import json
import asyncio
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, JSONResponse
from openai import AsyncOpenAI
import datetime
import akshare as ak
import pandas as pd
import smtplib
from email.mime.text import MIMEText
from email.header import Header
import os
from dotenv import load_dotenv 
load_dotenv()

app = FastAPI()

class GetCurrentTimeTool:
    name = "getCurrentTime"
    description = "获取当前时间"
    inputSchema = {}
    async def call_tool(self, tool_name, tool_args):
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        return type('Resp', (object,), {"content": [type('Text', (object,), {'text': now})()]})

class GetCurrentLocationTool:
    name = "getCurrentLocation"
    description = "获取当前地点"
    inputSchema = {}
    async def call_tool(self, tool_name, tool_args):
        # 简单返回，实际可以集成定位API
        location = "Tianjin"
        return type('Resp', (object,), {"content": [type('Text', (object,), {'text': location})()]})

class GetWeatherTool:
    name = "getWeather"
    description = "根据地点获取天气情况"
    inputSchema = {
        "type": "object",
        "properties": {
            "location": {"type": "string", "description": "查询天气的地点"}
        },
        "required": ["location"],
    }
    async def call_tool(self, tool_name, tool_args):
        location = tool_args.get("location", "Shanghai")
        # 实际可调用天气API，这里用静态值
        weather_info = f"{location}: 温度 7℃-15℃，晴"
        return type('Resp', (object,), {"content": [type('Text', (object,), {'text': weather_info})()]})

class GetGoldPriceTool:
    name = "getGoldPrice"
    description = "获取当前黄金价格"
    inputSchema = {}
    async def call_tool(self, tool_name, tool_args):
        macro_china_au_report_df = ak.spot_quotations_sge()
        au= macro_china_au_report_df.to_string()
        return type('Resp', (object,), {"content": [type('Text', (object,), {'text': au})()]})

class GetHistoryGoldPriceTool:
    name = "GetHistoryGoldPrice"
    description = "获取历史黄金价格"
    inputSchema = {}
    async def call_tool(self, tool_name, tool_args):
        macro_china_au_report_df = ak.macro_china_au_report()
        au100g_df = macro_china_au_report_df[macro_china_au_report_df['商品'] == 'Au100g']
        # 转换日期格式
        au100g_df['日期'] = pd.to_datetime(au100g_df['日期'])

        # 按日期升序排序
        au100g_df_sorted = au100g_df.sort_values(by='日期', ascending=False)
        result_df = au100g_df_sorted[['日期','商品', '开盘价', '收盘价']].head(60)
        au = result_df.to_string()   
        return type('Resp', (object,), {"content": [type('Text', (object,), {'text': au})()]})

class SendEmailTool:
    name = "sendEmail"
    description = "发送邮件给我"
    inputSchema = {
        "type": "object",
        "properties": {
            "subject": {"type": "string", "description": "邮件标题"},
            "body": {"type": "string", "description": "邮件正文"}
        },
        "required": ["subject","body"],
    }
    async def call_tool(self, tool_name, tool_args):
        s = tool_args.get("subject", "AI scheduler")
        b = tool_args.get("body", "AI alert")
        # 邮箱配置
        smtp_server = 'smtp.qq.com'
        smtp_port = 465  # SSL端口
        username = os.environ["email_sender"]  # 发件人邮箱
        password = os.environ["email_key"]  # 邮箱密码或授权码

        # 邮件内容
        sender = username
        receiver = os.environ["email_to"]   # 收件人邮箱
        subject = "Finance AI: "+s
        body = b

        # 构建邮件
        message = MIMEText(body, 'plain', 'utf-8')
        message['From'] = Header(sender)
        message['To'] = Header(receiver)
        message['Subject'] = Header(subject)

        try:
            # 连接SMTP服务器（SSL加密）
            smtp = smtplib.SMTP_SSL(smtp_server, smtp_port)
            smtp.login(username, password)
            smtp.sendmail(sender, [receiver], message.as_string())
            smtp.quit()
            print("邮件发送成功！")
            return "邮件发送成功！"
        except Exception as e:
            print("邮件发送失败：", e)
            return f"邮件发送失败：{e}"
     
class ChatbotDemo:
    def __init__(self):
        self.client = AsyncOpenAI(
            base_url=os.environ["openai_base_url"],  
            api_key=os.environ["openai_api_key"]
        )
        self.tools = [GetCurrentTimeTool(), 
                      GetCurrentLocationTool(), 
                      GetWeatherTool(),
                      GetGoldPriceTool(),
                      GetHistoryGoldPriceTool(),
                      SendEmailTool()]
        self.sessions = {tool.name: tool for tool in self.tools}

    async def chat_stream(self, request_json):
        # messages初始 -> 只保留最后10条，消息串
        messages = request_json['messages'][-10:]
        model = request_json["model"]
        available_tools = [{
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.inputSchema
            }
        } for tool in self.tools]
        function_list = []
        while True:
            # 一轮API call（流式+tool_call分离收集）
            response_gen = await self.client.chat.completions.create(
                model=model,
                messages=messages,
                tools=available_tools,
                stream=True,
            )
            function_list.clear()
            full_content = ""
            last_role = "assistant"
            tool_calls_batch = []
            # 接收流式
            async for chunk in response_gen:
                if chunk and chunk.choices:
                    delta = chunk.choices[0].delta
                    chunk_message = delta.content
                    # 普通内容流输出
                    if chunk_message:
                        yield f'data: {json.dumps({"choices":[{"delta":{"content": chunk_message}}]})}\n\n'
                        full_content += chunk_message
                    # 收集工具调用
                    if getattr(delta, "tool_calls", None):
                        for tool_call in delta.tool_calls:
                            if len(function_list) < tool_call.index + 1:
                                function_list.append({'name': '', 'args': '', 'id': tool_call.id})
                            if tool_call and tool_call.function.name:
                                function_list[tool_call.index]['name'] += tool_call.function.name
                            if tool_call and tool_call.function.arguments:
                                function_list[tool_call.index]['args'] += tool_call.function.arguments
            # tool_call detected, handle tool and react
            if function_list:
                tool_calls = []
                tool_messages = []
                for findex, func in enumerate(function_list):
                    function_name, function_args, toolid = func['name'], func['args'], func['id']
                    if function_name:
                        tool_args = json.loads(function_args or "{}")
                        # 对应函数响应
                        function_response = await self.sessions[function_name].call_tool(function_name, tool_args)
                        # 构建返回tool_call结构
                        tool_calls.append({
                            "id": toolid,
                            "function": {
                                "arguments": func["args"],
                                "name": function_name
                            },
                            "type": "function",
                            "index": findex
                        })
                        # tool角色消息
                        tool_messages.append({
                            "tool_call_id": toolid,
                            "role": "tool",
                            "content": function_response.content[0].text
                        })
                # 注意：需插入 assistant/tool_call消息和tool响应消息
                messages.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": tool_calls,
                })
                for m in tool_messages:
                    messages.append(m)
                # 此处继续死循环进行下一轮API调用，直到tool_call结束：即没有function_list
                continue
            # 无tool_call，轮次结束
            else:
                if full_content:
                    messages.append({"role": last_role, "content": full_content})
                break

    async def chat_full(self, request_json):
        # 用于非stream模式
        chunks = []
        async for item in self.chat_stream(request_json):
            # 把yield的data: ...变成合并内容
            # 只关注delta->content
            if item.startswith("data:"):
                js = json.loads(item[6:])
                chunks.append(js["choices"][0]["delta"].get("content", ""))
        return "".join(chunks)

chatbot = ChatbotDemo()

@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    request_json = await request.json()
    
    stream = request_json.get("stream", False)
    if stream:
        return StreamingResponse(
            chatbot.chat_stream(request_json),
            media_type="text/event-stream"
        )
    else:
        output = await chatbot.chat_full(request_json)
        # 非流式（对齐openai output结构）
        return JSONResponse(content={"choices":[{"message":{"content":output}}]})
    
    #uvicorn app:app --reload --port 8000