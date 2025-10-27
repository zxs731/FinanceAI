# demo.py

import asyncio
import datetime
import json
from openai import AsyncOpenAI
import akshare as ak
import pandas as pd
import smtplib
from email.mime.text import MIMEText
from email.header import Header
import os
from dotenv import load_dotenv 
load_dotenv()

class GetCurrentTimeTool:
    name = "getCurrentTime"
    description = "获取当前时间"
    inputSchema = {}
    
    async def call_tool(self, tool_name, tool_args):
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        return type('Resp', (object,), {"content": [type('Text', (object,), {'text': now})()]})
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
            return type('Resp', (object,), {"content": [type('Text', (object,), {'text': '邮件发送成功！'})()]})

        except Exception as e:
            print("邮件发送失败：", e)
            return type('Resp', (object,), {"content": [type('Text', (object,), {'text': f'邮件发送失败：{e}'})()]})

class ChatbotDemo:
    def __init__(self):
        # 替换成你的OpenAI Client初始化方法
        self.client = AsyncOpenAI(  
            base_url=os.environ["openai_base_url"],  
            api_key=os.environ["openai_api_key"]
        )
        
        self.messages = []
        self.tools = [GetCurrentTimeTool(),
                      GetGoldPriceTool(),
                      GetHistoryGoldPriceTool(),
                      SendEmailTool()]
        self.sessions = {tool.name: tool for tool in self.tools}

    async def process_query(self, query: str):
        sysmesg = {'role': 'system', 'content': '你是一个智能助手'}
        messages = self.messages[-10:]
        available_tools = [{
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.inputSchema
            }
        } for tool in self.tools]
        response_gen = await self.client.chat.completions.create(
            model=os.environ["model"],
            messages=[sysmesg] + messages + [{"role": "user", "content": query}],
            tools=available_tools,
            stream=True,
        )
        final_text = []
        function_list = []
        while True:
            result = ''
            async for chunk in response_gen:
                if chunk and chunk.choices:
                    delta = chunk.choices[0].delta
                    chunk_message = delta.content
                    # 关键：流式输出非 tool_call 的内容
                    if chunk_message:
                        print(chunk_message, end='', flush=True)
                        final_text.append(chunk_message)
                        result += chunk_message
                    # tool 调用收集
                    if getattr(delta, "tool_calls", None):
                        for tool_call in delta.tool_calls:
                            if len(function_list) < tool_call.index + 1:
                                function_list.append({'name': '', 'args': '', 'id': tool_call.id})
                            if tool_call and tool_call.function.name:  
                                function_list[tool_call.index]['name'] += tool_call.function.name  
                            if tool_call and tool_call.function.arguments:  
                                function_list[tool_call.index]['args'] += tool_call.function.arguments  
                            
            # tool 调用部分
            if function_list:
                tool_calls = []
                temp_messages = []
                for findex, func in enumerate(function_list):
                    function_name, function_args, toolid = func['name'], func['args'], func['id']
                    if function_name:
                        tool_args = json.loads(function_args or "{}")
                        function_response = await self.sessions[function_name].call_tool(function_name, tool_args)
                        tool_calls.append({
                            "id": toolid,
                            "function": {
                                "arguments": func["args"],
                                "name": function_name
                            },
                            "type": "function",
                            "index": findex
                        })
                        temp_messages.append({
                            "tool_call_id": toolid,
                            "role": "tool",
                            "content": function_response.content[0].text
                        })
                messages.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": tool_calls,
                })
                for m in temp_messages:
                    messages.append(m)
                response_gen = await self.client.chat.completions.create(
                    model=os.environ["model"],
                    messages=messages,
                    tools=available_tools,
                    stream=True,
                )
                function_list.clear()  # 防止死循环，清空 function_list
            else:
                if result:
                    self.messages.append({"role": "assistant", "content": result})
                    print()  # 换行
                    break
        return result

    async def run_console(self):
        print("您好，可以开始对话 (输入 /exit 结束)：")
        while True:
            query = input("你：")
            if query.strip().lower() == "/exit":
                break
            print("Bot：", end='', flush=True)
            await self.process_query(query)

if __name__ == "__main__":
    # Windows 下直接用 asyncio.run()
    asyncio.run(ChatbotDemo().run_console())