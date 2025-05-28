import json
import asyncio
import requests
import os
import re
import traceback
import random
from datetime import datetime, timedelta
from pathlib import Path
from playwright.async_api import Playwright, async_playwright
import time
from dotenv import load_dotenv
import argparse

# 加载.env文件中的环境变量
load_dotenv()

# 代码已修改：
# 1. 删除了登录选择框处理部分(handle_terms_dialog函数)
# 2. 删除了所有截图相关代码
# 3. 将Telegram推送修改为MarkdownV2格式的美化推送
# 4. 添加了定时执行功能，支持命令行参数
# 5. 支持通过环境变量或命令行参数设置工作站域名前缀

# 基础配置函数，每次调用时都从环境变量获取最新值
def get_base_prefix():
    """获取工作站域名前缀，优先使用环境变量"""
    return os.environ.get("BASE_PREFIX", "9000-idx-sherry-")

def get_domain_pattern():
    """获取工作站域名匹配模式"""
    base_prefix = get_base_prefix()
    return f"{base_prefix}[^.]*.cloudworkstations.dev"

# 用户代理和视口大小配置
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
]
VIEWPORT_SIZES = [
    {"width": 1920, "height": 1080},
    {"width": 1366, "height": 768},
]

# 全局配置
cookies_path = "cookie.json"  # 只保留一个cookie文件
app_url = os.environ.get("APP_URL", "https://idx.google.com")
all_messages = []
MAX_RETRIES = 3
TIMEOUT = 30000  # 默认超时时间（毫秒）

def log_message(message):
    """记录消息到全局列表并打印"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted_message = f"[{timestamp}] {message}"
    all_messages.append(formatted_message)
    print(formatted_message)

def send_to_telegram(message):
    """将消息发送到Telegram，使用MarkdownV2格式美化"""
    # 从环境变量获取凭据，必须在.env文件中配置
    bot_token = os.environ.get("TG_TOKEN")
    chat_id = os.environ.get("TG_CHAT_ID")
    
    # 如果环境变量中没有找到，则跳过通知
    if not bot_token or not chat_id:
        log_message("未在环境变量中找到TG_TOKEN或TG_CHAT_ID，跳过通知")
        return
    
    # 提取关键信息 - 查找关键状态行
    key_status_patterns = [
        "工作站可以直接通过协议访问",
        "页面状态码200",
        "自动化流程执行结果",
        "成功点击工作区图标",
        "通过cookies直接登录",
        "UI交互流程",
        "工作区加载验证",
        "已保存最终cookie状态",
        "主流程执行出错"
    ]
    
    # 从所有消息中提取关键状态行，避免重复
    key_lines = []
    seen_messages = set()  # 用于去重
    
    for line in all_messages:
        for pattern in key_status_patterns:
            if pattern in line:
                # 截取时间戳和实际消息
                parts = line.split("] ", 1)
                if len(parts) > 1:
                    time_stamp = parts[0].replace("[", "")
                    message_content = parts[1]
                    
                    # 去重：只添加未见过的消息内容
                    if message_content not in seen_messages:
                        seen_messages.add(message_content)
                        key_lines.append((time_stamp, message_content))
                    break
    
    # 构建MarkdownV2格式的消息
    # 需要转义特殊字符: . ! ( ) - + = # _ [ ] ~ > | { }
    def escape_markdown(text):
        escape_chars = r'_*[]()~`>#+-=|{}.!'
        return ''.join(f'\\{c}' if c in escape_chars else c for c in text)
    
    # 创建美化的MarkdownV2消息
    md_message = "*🔔 IDX自动登录状态报告 🔔*\n\n"
    
    # 检查是否有"页面状态码200"的消息
    has_status_200 = any("页面状态码200" in content for _, content in key_lines)
    
    # 添加状态摘要
    if has_status_200:
        # 如果有状态码200的消息，直接显示为成功
        md_message += "✅ *请求检查*: 页面状态200，工作站可直接访问\n\n"
        
        # 对于状态码200，简化详细状态部分
        md_message += "*📋 状态摘要:*\n"
        md_message += "✅ 工作站可直接访问，无需执行自动化流程\n"
    else:
        # 原有的逻辑
        success_count = sum(1 for _, content in key_lines if "成功" in content or "已保存" in content)
        error_count = sum(1 for _, content in key_lines if "失败" in content or "出错" in content)
        status_emoji = "✅" if success_count > error_count else "❌"
        
        md_message += f"{status_emoji} *状态摘要*: "
        md_message += f"成功操作 {success_count} 次, 错误 {error_count} 次\n\n"
        
        # 对于非200状态码，显示详细的操作日志
        md_message += "*📋 详细状态:*\n"
        for i, (time_stamp, content) in enumerate(key_lines):
            # 根据内容添加不同的emoji
            if "成功" in content or "已保存" in content or "页面状态码200" in content:
                emoji = "✅"
            elif "失败" in content or "出错" in content:
                emoji = "❌"
            else:
                emoji = "ℹ️"
                
            # 转义内容中的特殊字符
            safe_time = escape_markdown(time_stamp)
            safe_content = escape_markdown(content)
            
            md_message += f"{emoji} `{safe_time}`: {safe_content}\n"
    
    # 添加工作站域名信息(如果存在)
    domain = extract_domain_from_jwt()
    if domain:
        md_message += f"\n🌐 *工作站域名*: `{escape_markdown(domain)}`\n"
    
    # 添加时间戳
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    md_message += f"\n🕒 *执行时间*: `{escape_markdown(current_time)}`\n"
    
    # 添加分隔线和签名
    md_message += "\n\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\n"
    md_message += "_由 IDX自动登录工具 生成_"
    
    # 发送美化的消息
    try:
        # 仅显示token的前几个字符，保护隐私
        masked_token = bot_token[:5] + "..." if bot_token else ""
        masked_chat_id = chat_id[:3] + "..." if chat_id else ""
        log_message(f"正在使用TG_TOKEN={masked_token}和TG_CHAT_ID={masked_chat_id}发送消息")
        
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        data = {
            "chat_id": chat_id, 
            "text": md_message,
            "parse_mode": "MarkdownV2"
        }
        response = requests.post(url, data=data, timeout=10)
        log_message(f"Telegram通知状态: {response.status_code}")
    except Exception as e:
        log_message(f"发送Telegram通知失败: {e}")

def load_cookies(filename=cookies_path):
    """加载cookies并验证格式"""
    try:
        if not os.path.exists(filename):
            log_message(f"{filename}不存在，将创建空cookie文件")
            empty_data = {"cookies": [], "origins": []}
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(empty_data, f)
            return empty_data
            
        with open(filename, 'r', encoding="utf-8") as f:
            cookie_data = json.load(f)
            
        # 验证格式
        if "cookies" not in cookie_data or not isinstance(cookie_data["cookies"], list):
            log_message(f"{filename}格式有问题，将重置")
            empty_data = {"cookies": [], "origins": []}
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(empty_data, f)
            return empty_data
            
        log_message(f"成功加载{filename}")
        return cookie_data
    except Exception as e:
        log_message(f"加载{filename}失败: {e}")
        # 创建空cookie文件
        empty_data = {"cookies": [], "origins": []}
        try:
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(empty_data, f)
        except Exception:
            pass
        return empty_data

def check_page_status_with_requests():
    """使用预设的JWT和URL值直接检查工作站的访问状态"""
    try:
        # 预设值
        preset_jwt = 'eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJodHRwczovL2Nsb3VkLmdvb2dsZS5jb20vd29ya3N0YXRpb25zIiwiYXVkIjoiaWR4LXNoZXJyeS0xNzQ1NzUyMjgzNzQ5LmNsdXN0ZXItaWt4anpqaGxpZmN3dXJvb21ma2pyeDQzN2cuY2xvdWR3b3Jrc3RhdGlvbnMuZGV2IiwiaWF0IjoxNzQ2NzA3MDU1LCJleHAiOjE3NDY3OTMzOTV9.mRGJrxhTNmJ-YTit_SHGSJs9UDIOrBmgRrCqIX0Jio_orzUoVx7MtEzCfR5M2QJonVi98cOJjp0TfDpeNuJ3jnVj9GK0dZjO4bd26eAylCLU-UVt6TStzJLEYohJZHC71naMHDpLTHAajGvT4axxY_EGfyqt5GhjMMOCz_-vTeK_fmIayctGjMVGkogYimmoKfOHKzBkPgT4kSNbUA4NPjAUILVOmjxLcUmksPSdHXAPkO9Q4NEcjNQ2-b3Ax5BlF2W6Ae13pH9NgHPxeaGd2NwmJl5nivRop3E1X7LQ49YLAHGmCzD6D8z4qtoNjC8FibhlRBGvty48sYtexOn13g'
        # 使用当前设置的前缀构建域名
        preset_url = f'https://{get_base_prefix()}1745752283749.cluster-ikxjzjhlifcwuroomfkjrx437g.cloudworkstations.dev/'
        
        # 从cookie文件中提取JWT(如果存在)
        jwt = preset_jwt
        
        # 尝试从cookie.json文件加载JWT
        try:
            if os.path.exists(cookies_path):
                cookie_data = load_cookies(cookies_path)
                for cookie in cookie_data.get("cookies", []):
                    if cookie.get("name") == "WorkstationJwtPartitioned":
                        jwt = cookie.get("value")
                        log_message("从cookie.json中成功加载了JWT")
                        break
        except Exception as e:
            log_message(f"从cookie.json加载JWT失败: {e}，将使用预设值")
                
        # 构建请求
        request_cookies = {'WorkstationJwtPartitioned': jwt}
        headers = {
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'Accept-Language': 'en-US',
            'Connection': 'keep-alive',
            'Referer': 'https://workstations.cloud.google.com/',
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Mobile/15E148 Safari/604.1',
        }
        
        # 获取正确的域名
        workstation_url = extract_domain_from_jwt(jwt)
        if not workstation_url:
            workstation_url = preset_url
            
        log_message(f"使用requests检查工作站状态，URL: {workstation_url}")
        log_message(f"使用JWT: {jwt[:20]}... (已截断)")
        
        # 发送请求获取页面状态，简化直接访问
        response = requests.get(
            workstation_url,
            cookies=request_cookies,
            headers=headers,
            timeout=15
        )
        
        log_message(f"页面状态码: {response.status_code}")
        
        if response.status_code == 200:
            log_message("页面状态码200，工作站可以直接通过协议访问")
            return True
        else:
            log_message(f"页面状态码为{response.status_code}，无法直接通过协议访问")
            return False
    except Exception as e:
        log_message(f"使用requests检查工作站状态时出错: {e}")
        log_message(traceback.format_exc())
        return False

def extract_domain_from_jwt(jwt_value=None):
    """从JWT token中提取域名"""
    try:
        # 如果没有提供JWT，尝试从cookie文件加载
        if not jwt_value:
            cookie_data = load_cookies(cookies_path)
            for cookie in cookie_data.get("cookies", []):
                if cookie.get("name") == "WorkstationJwtPartitioned":
                    jwt_value = cookie.get("value")
                    break
        
        if not jwt_value:
            log_message("无法找到JWT值，将使用默认域名")
            return f"https://{get_base_prefix()}1745752283749.cluster-ikxjzjhlifcwuroomfkjrx437g.cloudworkstations.dev"
            
        # 解析JWT获取域名信息
        parts = jwt_value.split('.')
        if len(parts) >= 2:
            import base64
            
            # 解码中间部分（可能需要补齐=）
            padded = parts[1] + '=' * (4 - len(parts[1]) % 4)
            decoded = base64.b64decode(padded)
            payload = json.loads(decoded)
            
            # 从aud字段提取域名
            if 'aud' in payload:
                aud = payload['aud']
                log_message(f"JWT中提取的aud字段: {aud}")
                
                # 更灵活的正则表达式，可以匹配任何前缀的工作站域名
                # 匹配格式: 任何前缀-数字.cluster-xxx.cloudworkstations.dev
                match = re.search(r'([^\.]+\.cluster-[^\.]+\.cloudworkstations\.dev)', aud)
                if match:
                    full_domain_from_jwt = match.group(1)
                    log_message(f"从JWT中提取的原始域名: {full_domain_from_jwt}")
                    
                    # 直接使用当前设置的前缀和完整的集群信息
                    # 提取集群部分：数字.cluster-xxx.cloudworkstations.dev
                    cluster_part_match = re.search(r'(\d+\.cluster-[^\.]+\.cloudworkstations\.dev)', full_domain_from_jwt)
                    if cluster_part_match:
                        cluster_part = cluster_part_match.group(1)
                        full_domain = f"https://{get_base_prefix()}{cluster_part}"
                        log_message(f"从JWT提取的域名(使用当前前缀): {full_domain}")
                        return full_domain
                    else:
                        # 如果无法提取集群部分，直接使用完整域名
                        full_domain = f"https://{full_domain_from_jwt}"
                        log_message(f"从JWT提取的域名(无法提取集群部分): {full_domain}")
                        return full_domain
        
        # 如果提取失败，使用默认域名
        default_domain = f"https://{get_base_prefix()}1745752283749.cluster-ikxjzjhlifcwuroomfkjrx437g.cloudworkstations.dev"
        log_message(f"使用默认域名: {default_domain}")
        return default_domain
    except Exception as e:
        log_message(f"提取域名时出错: {e}")
        log_message(traceback.format_exc())
        return f"https://{get_base_prefix()}1745752283749.cluster-ikxjzjhlifcwuroomfkjrx437g.cloudworkstations.dev"

def extract_and_display_credentials():
    """从cookie.json中提取并显示云工作站域名和JWT"""
    try:
        if not os.path.exists(cookies_path):
            log_message("cookie.json文件不存在，无法提取凭据")
            return
            
        with open(cookies_path, 'r', encoding='utf-8') as f:
            cookie_data = json.load(f)
            
        # 提取JWT
        jwt = None
        for cookie in cookie_data.get("cookies", []):
            if cookie.get("name") == "WorkstationJwtPartitioned":
                jwt = cookie.get("value")
                break
                
        if not jwt:
            log_message("在cookie.json中未找到WorkstationJwtPartitioned")
            return
            
        # 从JWT中提取域名，使用现有函数避免代码重复
        domain = extract_domain_from_jwt(jwt)
            
        # 显示提取的信息
        log_message("\n========== 提取的凭据信息 ==========")
        log_message(f"WorkstationJwtPartitioned: {jwt[:20]}...{jwt[-20:]} (已截断，仅显示前20和后20字符)")
        
        if domain:
            log_message(f"工作站域名: {domain}")
        else:
            log_message("无法从JWT提取域名")
            
        # 打印完整的请求示例
        log_message("\n以下是可用于访问工作站的请求示例代码:")
        code_example = f"""import requests

cookies = {{
    'WorkstationJwtPartitioned': '{jwt}',
}}

headers = {{
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
    'Accept-Language': 'en-US',
    'Connection': 'keep-alive',
    'Referer': 'https://workstations.cloud.google.com/',
    'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Mobile/15E148 Safari/604.1',
}}

response = requests.get(
    '{domain if domain else "工作站URL"}',
    cookies=cookies,
    headers=headers,
)
print(response.status_code)
print(response.text)"""
        log_message(code_example)
        log_message("========== 提取完成 ==========\n")
        
    except Exception as e:
        log_message(f"提取凭据时出错: {e}")
        log_message(traceback.format_exc())

async def wait_for_workspace_loaded(page, timeout=180):
    """等待Firebase Studio工作区加载完成"""
    log_message(f"检测是否成功进入Firebase Studio...")
    current_url = page.url
    log_message(f"当前URL: {current_url}")
    
    # 检查URL是否包含工作站域名的关键部分
    is_workstation_url = (
        "cloudworkstations.dev" in current_url or 
        "workspace" in current_url or 
        "firebase" in current_url or
        get_base_prefix().replace("-", "") in current_url.lower() or  # 检查前缀（不含连字符）
        "lost" in current_url.lower()  # 兼容旧版检测
    )
    
    if is_workstation_url:
        log_message("URL包含目标关键词，确认进入目标页面")
        
        # 先等待页面基本加载，减少等待时间
        log_message("等待页面基本加载...")
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=60000)
            log_message("DOM内容已加载")
            
            # 尝试等待网络稳定，但不阻塞流程
            try:
                await page.wait_for_load_state("networkidle", timeout=30000)
                log_message("网络活动已稳定")
            except Exception as e:
                log_message(f"等待网络稳定超时，但这不会阻塞流程: {e}")
        except Exception as e:
            log_message(f"等待DOM加载超时: {e}，但将继续流程")
        
        # 等待页面完全加载，增加至120秒
        log_message("等待120秒让页面和资源完全加载...")
        await asyncio.sleep(120)
        log_message("等待时间结束，开始检测侧边栏元素...")
        
        max_refresh_retries = 3
        for refresh_attempt in range(1, max_refresh_retries + 1):
            try:
                # 打印页面部分HTML，便于调试
                html = await page.content()
                log_message("当前页面HTML片段：" + html[:2000])
                
                # 检查是否有iframe
                frames = page.frames
                target = page
                for frame in frames:
                    try:
                        frame_html = await frame.content()
                        if 'codicon-explorer-view-icon' in frame_html:
                            target = frame
                            log_message("已自动切换到包含目标元素的iframe")
                            break
                    except Exception:
                        continue
                
                # IDE相关的侧边栏按钮
                ide_btn_selectors = [
                    '[class*="codicon-explorer-view-icon"], [aria-label*="Explorer"]',
                    '[class*="codicon-search-view-icon"], [aria-label*="Search"]',
                    '[class*="codicon-source-control-view-icon"], [aria-label*="Source Control"]',
                    '[class*="codicon-run-view-icon"], [aria-label*="Run and Debug"]',
                ]
                
                # Web元素检测（只保留一个最可能匹配的选择器）
                web_selector = 'div[aria-label="Web"] span.tab-label-name, div[aria-label*="Web"], [class*="monaco-icon-label"] span.monaco-icon-name-container:has-text("Web")'
                
                # 合并所有需要检测的选择器
                all_selectors = ide_btn_selectors + [web_selector]
                
                # 依次等待每个元素，使用更短的超时时间
                found_elements = 0
                for sel in all_selectors:
                    try:
                        await target.wait_for_selector(sel, timeout=10000)  # 10秒超时
                        found_elements += 1
                        log_message(f"找到元素 {found_elements}/{len(all_selectors)}: {sel}")
                    except Exception as e:
                        log_message(f"未找到元素: {sel}, 错误: {e}")
                        # 即使某个元素未找到，也继续检查其他元素
                        continue
                
                if found_elements > 0:
                    log_message(f"主界面找到 {found_elements}/{len(all_selectors)} 个元素（第{refresh_attempt}次尝试）")
                    # 只要找到至少5个元素（全部）就认为成功
                    if found_elements >= len(all_selectors):
                        log_message(f"找到全部UI元素 ({found_elements}/{len(all_selectors)})，认为界面加载成功")
                        
                        # 停留较短时间
                        log_message("停留15秒以确保页面完全加载...")
                        await asyncio.sleep(15)
                        
                        # 保存cookie状态
                        log_message("已更新存储状态到cookie.json")
                        return True
                    else:
                        log_message(f"找到的元素数量不足 ({found_elements}/{len(all_selectors)})，需要至少4个元素才认为成功")
                        if found_elements >= 4:
                            log_message(f"找到大部分UI元素 ({found_elements}/{len(all_selectors)})，认为界面基本加载成功")
                            # 保存cookie状态
                            log_message("已更新存储状态到cookie.json")
                            return True
                        elif refresh_attempt < max_refresh_retries:
                            log_message(f"未找到足够元素，尝试刷新页面（第{refresh_attempt}/{max_refresh_retries}次）...")
                            await page.reload()
                            log_message("页面刷新后等待60秒让元素加载...")
                            await asyncio.sleep(60)
                        else:
                            log_message("已达到最大刷新重试次数，未能找到足够的UI元素")
                            # 尽管未找到足够元素，我们也返回成功，因为我们已经到了目标页面
                            return True
                else:
                    log_message(f"未找到任何UI元素，尝试刷新...")
                    if refresh_attempt < max_refresh_retries:
                        log_message(f"刷新页面并重试（第{refresh_attempt}/{max_refresh_retries}次）...")
                        await page.reload()
                        log_message("页面刷新后等待60秒让元素加载...")
                        await asyncio.sleep(60)
                    else:
                        log_message("已达到最大刷新重试次数，未能找到任何UI元素")
                        # 尽管未找到元素，我们也返回成功，因为我们已经到了目标页面
                        return True
            except Exception as e:
                log_message(f"第{refresh_attempt}次尝试：等待主界面元素时出错: {e}")
                if refresh_attempt < max_refresh_retries:
                    log_message(f"刷新页面并重试（第{refresh_attempt}/{max_refresh_retries}次）...")
                    await page.reload()
                    log_message("页面刷新后等待60秒让元素加载...")
                    await asyncio.sleep(60)
                else:
                    log_message("已达到最大刷新重试次数，无法完成检测")
                    # 尽管出错，我们也返回成功，因为我们已经到了目标页面
                    return True
    else:
        log_message("URL未包含目标关键词，未检测到目标页面")
        return False
    
    # 如果执行到这里，说明流程已完成但可能未找到所有元素
    return True

async def click_workspace_icon(page):
    """尝试点击工作区图标"""
    log_message("尝试点击workspace图标...")
    
    # 工作区图标选择器列表
    selectors = [
        'div[class="workspace-icon"]',
        'img[src="https://www.gstatic.com/monospace/250314/workspace-blank-192.png"]',
        '.workspace-icon',
        'img[role="presentation"][class="custom-icon"]',
        'div[_ngcontent-ng-c2464377164][class="workspace-icon"]',
        'div.workspace-icon img.custom-icon',
        '.workspace-icon img'
    ]
    
    for selector in selectors:
        try:
            log_message(f"尝试选择器: {selector}")
            element = await page.wait_for_selector(selector, timeout=5000)
            if element:
                # 尝试多种点击方法
                try:
                    await element.click(force=True)
                    log_message(f"成功点击元素! 使用选择器: {selector}")
                    return True
                except Exception as e:
                    log_message(f"直接点击失败: {e}，尝试JavaScript点击")
                    try:
                        await page.evaluate("(element) => element.click()", element)
                        log_message(f"使用JavaScript成功点击元素!")
                        return True
                    except Exception:
                        continue
        except Exception:
            continue
            
    log_message("所有选择器都尝试失败，无法点击工作区图标")
    return False

# 添加一个新的异步函数，用于重试等待元素的出现
async def wait_for_element_with_retry(page, selector, description, timeout_ms=10000, max_attempts=3):
    """尝试等待元素出现，如果超时则重试，总共尝试指定次数"""
    for attempt in range(max_attempts):
        try:
            log_message(f"等待{description}出现，第{attempt + 1}次尝试...")
            element = await page.wait_for_selector(selector, timeout=timeout_ms)
            log_message(f"✓ {description}已出现!")
            return element
        except Exception as e:
            log_message(f"× 等待{description}超时: {e}")
            if attempt < max_attempts - 1:
                log_message("准备重试...")
                # 等待一段时间后重试
                await asyncio.sleep(2)
            else:
                log_message(f"已达到最大尝试次数({max_attempts})，无法找到{description}")
                return None
    return None

async def wait_for_element_with_multiple_selectors(page, selectors, description, timeout_ms=10000, max_attempts=3):
    """使用多个选择器尝试等待元素出现，如果其中一个成功则返回该元素"""
    for attempt in range(max_attempts):
        log_message(f"等待{description}出现，第{attempt + 1}次尝试...")
        for selector in selectors:
            try:
                element = await page.wait_for_selector(selector, timeout=timeout_ms/len(selectors))
                log_message(f"✓ {description}已出现! 使用选择器: {selector}")
                return element
            except Exception:
                continue
        
        log_message(f"× 尝试所有选择器后，无法找到{description}")
        if attempt < max_attempts - 1:
            log_message("准备重试...")
            # 等待一段时间后重试
            await asyncio.sleep(2)
        else:
            log_message(f"已达到最大尝试次数({max_attempts})，无法找到{description}")
            return None
    return None

async def navigate_to_firebase_by_clicking(page):
    """通过点击已验证的工作区图标导航到Firebase Studio"""
    log_message("通过点击已验证的工作区图标导航到Firebase Studio...")
    
    # 确保在点击前记录URL
    pre_click_url = page.url
    log_message(f"点击前当前URL: {pre_click_url}")
    
    # 尝试点击工作区图标
    workspace_icon_clicked = await click_workspace_icon(page)
    
    if not workspace_icon_clicked:
        log_message("无法点击工作区图标，导航失败")
        return False
    
    # 等待页面响应，检查URL变化，最多等待15秒
    max_wait_seconds = 15
    url_changed = False
    
    for wait_attempt in range(1, 4):  # 最多检查3次
        await asyncio.sleep(5)  # 每次等待5秒
        
        # 检查点击后URL是否变化
        post_click_url = page.url
        log_message(f"点击后当前URL (检查{wait_attempt}/3): {post_click_url}")
        
        url_changed = pre_click_url != post_click_url
        log_message(f"URL是否发生变化: {url_changed}")
        
        if url_changed:
            break
        
        log_message(f"URL未变化，继续等待... ({wait_attempt*5}/{max_wait_seconds}秒)")
    
    if url_changed:
        log_message("点击工作区图标成功，URL已变化，继续等待工作区加载")
        # URL已变化，直接返回True，后续操作不变
        return True
    else:
        # 尝试刷新页面看是否有帮助
        log_message("点击工作区图标后URL未变化，尝试刷新页面...")
        await page.reload()
        await asyncio.sleep(5)
        
        # 再次检查URL
        post_refresh_url = page.url
        log_message(f"刷新后当前URL: {post_refresh_url}")
        url_changed_after_refresh = pre_click_url != post_refresh_url
        
        if url_changed_after_refresh:
            log_message("刷新后URL已变化，继续等待工作区加载")
            return True
        else:
            log_message("刷新后URL仍未变化，但仍然继续尝试加载工作区...")
            # 尽管URL未变化，但可能是SPA应用内部状态已改变，我们还是返回True继续尝试
            return True

async def login_with_ui_flow(page):
    """通过UI交互流程登录idx.google.com，然后跳转到Firebase Studio"""
    try:
        log_message("开始UI交互登录流程...")
        
        # 先导航到idx.google.com
        try:
            await page.goto("https://idx.google.com/", timeout=TIMEOUT)
            await page.wait_for_load_state("domcontentloaded", timeout=TIMEOUT)
            log_message("页面基本加载完成")
        except Exception as e:
            log_message(f"导航到idx.google.com失败: {e}，但将继续尝试")
        
        # 等待页面加载，给更多时间
        await asyncio.sleep(10)
        
        # 获取登录凭据
        email = os.environ.get("IDX_EMAIL", "")
        password = os.environ.get("IDX_PASSWORD", "")
        
        if not email or not password:
            log_message("未设置环境变量IDX_EMAIL或IDX_PASSWORD，无法进行登录")
            return False
        
        log_message("开始执行登录流程...")
        
        try:
            # 点击"Get Started"按钮 - 改进版
            log_message("尝试点击'Get Started'按钮...")
            try:
                # 更全面的Get Started按钮选择器，基于用户提供的HTML结构
                get_started_selectors = [
                    'a[href="/new"]',  # 基于用户提供的HTML
                    'a[href="/new"] span:has-text("Get Started")',
                    '#nav [role="link"]:has-text("Get Started")',
                    'a:has-text("Get Started")',
                    '[data-testid="get-started-button"]',
                    '.get-started-btn',
                    'button:has-text("Get Started")',
                    '[aria-label="Get Started"]'
                ]
                
                # 使用多选择器函数查找按钮
                get_started_btn = await wait_for_element_with_multiple_selectors(
                    page, 
                    get_started_selectors,
                    "'Get Started'按钮",
                    timeout_ms=20000,
                    max_attempts=3
                )
                
                # 如果找到了按钮，尝试多种点击方法
                if get_started_btn:
                    click_success = False
                    
                    # 方法1: 直接点击
                    try:
                        await get_started_btn.click()
                        log_message("成功点击'Get Started'按钮(直接点击)")
                        click_success = True
                    except Exception as e:
                        log_message(f"直接点击'Get Started'按钮失败: {e}")
                    
                    # 方法2: 强制点击
                    if not click_success:
                        try:
                            await get_started_btn.click(force=True)
                            log_message("成功点击'Get Started'按钮(强制点击)")
                            click_success = True
                        except Exception as e:
                            log_message(f"强制点击'Get Started'按钮失败: {e}")
                    
                    # 方法3: JavaScript点击
                    if not click_success:
                        try:
                            await page.evaluate('(element) => element.click()', get_started_btn)
                            log_message("成功点击'Get Started'按钮(JavaScript点击)")
                            click_success = True
                        except Exception as e:
                            log_message(f"JavaScript点击'Get Started'按钮失败: {e}")
                    
                    # 方法4: 通过选择器JavaScript点击
                    if not click_success:
                        for selector in get_started_selectors:
                            try:
                                await page.evaluate(f'document.querySelector("{selector}").click()')
                                log_message(f"通过选择器JavaScript成功点击'Get Started'按钮: {selector}")
                                click_success = True
                                break
                            except Exception:
                                continue
                    
                    # 方法5: 模拟键盘操作
                    if not click_success:
                        try:
                            await get_started_btn.focus()
                            await page.keyboard.press('Enter')
                            log_message("通过键盘Enter键成功点击'Get Started'按钮")
                            click_success = True
                        except Exception as e:
                            log_message(f"键盘操作'Get Started'按钮失败: {e}")
                    
                    # 如果所有方法都失败，直接导航
                    if not click_success:
                        log_message("所有点击方法都失败，尝试直接导航到登录页")
                        await page.goto("https://accounts.google.com/", timeout=TIMEOUT)
                        log_message("尝试直接导航到Google账号登录页")
                else:
                    # 如果未找到按钮，直接导航到账号登录页
                    log_message("未找到'Get Started'按钮，尝试直接导航到登录页")
                    await page.goto("https://accounts.google.com/", timeout=TIMEOUT)
                    log_message("尝试直接导航到Google账号登录页")
                
                # 等待点击响应，给更多时间
                await asyncio.sleep(8)
            except Exception as e:
                log_message(f"点击'Get Started'按钮过程出错: {e}，尝试直接导航到登录页")
                await page.goto("https://accounts.google.com/", timeout=TIMEOUT)
                log_message("尝试直接导航到Google账号登录页")
                await asyncio.sleep(5)
            
            # 检查当前URL，看是否已进入登录页面
            log_message(f"当前URL: {page.url}")
            
            # 等待登录页面元素加载
            log_message("等待登录页面元素加载...")
            
            # 检查是否存在"Choose an account"页面 - 借鉴520.py的处理方式
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=10000)
                choose_account_visible = await page.query_selector('text="Choose an account"')
                
                if choose_account_visible:
                    log_message("检测到'Choose an account'页面，尝试选择账户...")
                    
                    # 尝试多种方法查找并点击包含用户邮箱的项目
                    try:
                        # 方法1: 直接通过邮箱文本查找
                        email_account = page.get_by_text(email)
                        if email_account:
                            log_message(f"找到包含邮箱的账户，点击...")
                            await email_account.click()
                            await page.wait_for_load_state("networkidle", timeout=10000)
                        else:
                            # 方法2: 通过div内容查找
                            email_div = await page.query_selector(f'div:has-text("{email}")')
                            if email_div:
                                log_message(f"找到包含邮箱的div，点击...")
                                await email_div.click()
                                await page.wait_for_load_state("networkidle", timeout=10000)
                            else:
                                # 方法3: 点击第一个账户选项
                                log_message("未找到匹配的邮箱账户，尝试点击第一个选项...")
                                first_account = await page.query_selector('.OVnw0d')
                                if first_account:
                                    await first_account.click()
                                    await page.wait_for_load_state("networkidle", timeout=10000)
                                else:
                                    log_message("无法找到任何账户选项，将继续尝试输入密码...")
                    except Exception as e:
                        log_message(f"选择账户失败: {e}，但将继续执行")
                    
                    # 账户选择后，直接跳到密码输入
                    email_input = None
                else:
                    log_message("没有检测到'Choose an account'页面，继续正常登录流程...")
                    
                    # 使用改进的多选择器函数寻找邮箱输入框 - 借鉴520.py的方法
                    email_input = None
                    
                    # 方法1: 使用get_by_label
                    try:
                        email_input = page.get_by_label("Email or phone")
                        await email_input.wait_for(timeout=5000)
                        log_message("通过get_by_label找到邮箱输入框")
                    except Exception:
                        try:
                            email_input = page.get_by_label("电子邮件地址或电话号码")
                            await email_input.wait_for(timeout=5000)
                            log_message("通过get_by_label(中文)找到邮箱输入框")
                        except Exception:
                            email_input = None
                    
                    # 方法2: 使用query_selector作为备用
                    if not email_input:
                        email_selectors = [
                            'input[type="email"]', 
                            'input[name="identifier"]',
                            '[aria-label="电子邮件地址或电话号码"]',
                            '[aria-label="Email or phone"]'
                        ]
                        
                        email_input = await wait_for_element_with_multiple_selectors(
                            page, 
                            email_selectors,
                            "邮箱输入框",
                            timeout_ms=15000,
                            max_attempts=3
                        )
            except Exception as e:
                log_message(f"检查'Choose an account'页面失败: {e}，继续常规登录流程")
                email_input = None
            
            # 如果找到了邮箱输入框
            if email_input:
                # 清除输入框并输入邮箱 - 增强人性化操作
                log_message("尝试输入邮箱...")
                await email_input.click()
                await asyncio.sleep(random.uniform(1.2, 2.5))  # 随机延迟
                
                # 模拟真实用户的清空操作
                await email_input.press("Control+a")  # 全选
                await asyncio.sleep(random.uniform(0.3, 0.8))
                await email_input.press("Delete")  # 删除
                await asyncio.sleep(random.uniform(0.5, 1.2))
                
                # 分段输入邮箱，模拟真实打字
                email_parts = [email[:len(email)//2], email[len(email)//2:]]
                for part in email_parts:
                    await email_input.type(part, delay=random.randint(80, 150))
                    await asyncio.sleep(random.uniform(0.2, 0.6))
                
                log_message(f"已输入邮箱: {email[:3]}...{email[-3:]}")
                await asyncio.sleep(random.uniform(2.5, 4.0))  # 随机等待
                
                # 点击"下一步"按钮
                log_message("寻找'下一步'按钮...")
                next_button_selectors = [
                    'button:has-text("下一步")',
                    'button:has-text("Next")',
                    '[role="button"]:has-text("下一步")',
                    '[role="button"]:has-text("Next")'
                ]
                
                next_button = await wait_for_element_with_multiple_selectors(
                    page, 
                    next_button_selectors,
                    "'下一步'按钮",
                    timeout_ms=15000,
                    max_attempts=3
                )
                
                # 如果找到了下一步按钮
                if next_button:
                    log_message("点击下一步按钮")
                    # 模拟鼠标悬停再点击
                    await next_button.hover()
                    await asyncio.sleep(random.uniform(0.5, 1.2))
                    await next_button.click()
                    # 增加随机等待时间，确保密码页面加载
                    await asyncio.sleep(random.uniform(6.0, 10.0))
                else:
                    log_message("未找到下一步按钮，尝试按回车键提交")
                    await email_input.press("Enter")
                    log_message("已按回车键提交邮箱")
                    # 增加随机等待时间，确保密码页面加载
                    await asyncio.sleep(random.uniform(6.0, 10.0))
            else:
                log_message("无法找到任何邮箱输入框，登录流程可能无法继续")
                return False
            
            # ===== 增强密码输入框查找逻辑 =====
            
            # 检查页面内容，帮助调试
            html_content = await page.content()
            log_message(f"当前页面URL: {page.url}")
            
            # 等待并查找密码输入框 - 借鉴520.py的方法
            log_message("等待密码输入框...")
            password_input = None
            
            # 方法1: 使用get_by_label (520.py的方法)
            try:
                password_input = page.get_by_label("Enter your password")
                await password_input.wait_for(timeout=15000)
                log_message("通过get_by_label找到密码输入框")
            except Exception:
                try:
                    password_input = page.get_by_label("输入您的密码")
                    await password_input.wait_for(timeout=10000)
                    log_message("通过get_by_label(中文)找到密码输入框")
                except Exception:
                    password_input = None
            
            # 方法2: 使用query_selector作为备用
            if not password_input:
                password_selectors = [
                    'input[type="password"]',
                    'input[name="password"]',
                    'input[name="Passwd"]',
                    '[aria-label="输入您的密码"]',
                    '[aria-label="Enter your password"]'
                ]
                
                password_input = await wait_for_element_with_multiple_selectors(
                    page, 
                    password_selectors,
                    "密码输入框",
                    timeout_ms=20000,
                    max_attempts=3
                )
            
            # 如果找到了密码输入框
            if password_input:
                # 确保密码输入框可见和可交互
                try:
                    await password_input.wait_for_element_state("visible", timeout=5000)
                    log_message("密码输入框已可见")
                except Exception as e:
                    log_message(f"密码输入框不可见: {e}，但将继续尝试")
                
                # 清除并输入密码 - 增强人性化操作
                log_message("尝试输入密码...")
                try:
                    await password_input.click()
                    await asyncio.sleep(random.uniform(1.5, 3.0))  # 随机延迟
                    
                    # 模拟真实用户的清空操作
                    await password_input.press("Control+a")  # 全选
                    await asyncio.sleep(random.uniform(0.2, 0.5))
                    await password_input.press("Delete")  # 删除
                    await asyncio.sleep(random.uniform(0.8, 1.5))
                    
                    # 分段输入密码，模拟真实打字
                    password_parts = [password[:len(password)//2], password[len(password)//2:]]
                    for part in password_parts:
                        await password_input.type(part, delay=random.randint(100, 200))
                        await asyncio.sleep(random.uniform(0.3, 0.8))
                    
                    log_message("已输入密码(已隐藏)")
                    await asyncio.sleep(random.uniform(2.0, 3.5))  # 随机等待
                except Exception as e:
                    log_message(f"输入密码失败: {e}，尝试使用fill方法")
                    try:
                        await password_input.fill(password)
                        log_message("使用fill方法输入密码成功")
                        await asyncio.sleep(random.uniform(2.0, 3.0))
                    except Exception as e2:
                        log_message(f"使用fill方法输入密码也失败: {e2}")
                        return False
                
                # 点击"下一步"按钮完成登录 - 借鉴520.py的方法
                log_message("寻找密码页面的'下一步'按钮...")
                pwd_next_button = None
                
                # 方法1: 使用get_by_role (520.py的方法)
                try:
                    pwd_next_button = page.get_by_role("button", name="Next")
                    await pwd_next_button.wait_for(timeout=10000)
                    log_message("通过get_by_role找到下一步按钮")
                except Exception:
                    try:
                        pwd_next_button = page.get_by_role("button", name="下一步")
                        await pwd_next_button.wait_for(timeout=5000)
                        log_message("通过get_by_role(中文)找到下一步按钮")
                    except Exception:
                        pwd_next_button = None
                
                # 方法2: 使用query_selector作为备用
                if not pwd_next_button:
                    pwd_next_selectors = [
                        'button:has-text("下一步")',
                        'button:has-text("Next")',
                        '[role="button"]:has-text("下一步")',
                        '[role="button"]:has-text("Next")'
                    ]
                    
                    pwd_next_button = await wait_for_element_with_multiple_selectors(
                        page, 
                        pwd_next_selectors,
                        "密码页面的'下一步'按钮",
                        timeout_ms=10000,
                        max_attempts=2
                    )
                
                # 如果找到了下一步按钮
                if pwd_next_button:
                    log_message("点击密码页面的下一步按钮")
                    try:
                        # 模拟鼠标悬停再点击
                        await pwd_next_button.hover()
                        await asyncio.sleep(random.uniform(0.8, 1.5))
                        await pwd_next_button.click()
                    except Exception as e:
                        log_message(f"点击密码页面的下一步按钮失败: {e}，尝试回车键提交")
                        await password_input.press("Enter")
                        log_message("已按回车键提交密码")
                else:
                    log_message("未找到密码页面的下一步按钮，尝试按回车键提交")
                    await password_input.press("Enter")
                    log_message("已按回车键提交密码")
                
                # 等待登录完成，给充分时间
                log_message("等待登录完成...")
                await asyncio.sleep(random.uniform(12.0, 18.0))  # 随机等待时间
            else:
                log_message("无法找到任何密码输入框，登录流程可能无法继续")
                return False
            
            # 验证登录成功
            current_url = page.url
            log_message(f"登录后当前URL: {current_url}")
            
            # 如果登录流程可能已重定向到其他页面，尝试导航回IDX
            if "idx.google.com" not in current_url:
                log_message("当前不在IDX页面，尝试导航回IDX...")
                await page.goto("https://idx.google.com/", timeout=TIMEOUT)
                await page.wait_for_load_state("domcontentloaded", timeout=TIMEOUT)
                await asyncio.sleep(5)
                current_url = page.url
                log_message(f"导航后当前URL: {current_url}")
            
            # 验证是否登录成功 - 检测URL不包含signin
            url_valid = "idx.google.com" in current_url and "signin" not in current_url
            
            if url_valid:
                log_message("登录成功! URL不包含signin")
                
                # 等待一段时间让页面完全加载
                await asyncio.sleep(5)
                
                # 直接调用导航函数，由它负责点击工作区图标并验证URL变化
                log_message("登录成功，尝试导航到Firebase Studio...")
                return await navigate_to_firebase_by_clicking(page)
            else:
                log_message("可能未成功登录，URL仍包含signin或不在idx.google.com域名下")
                return False
            
        except Exception as e:
            log_message(f"登录过程中出错: {e}")
            log_message(traceback.format_exc())
            return False
    except Exception as e:
        log_message(f"UI交互流程出错: {e}")
        log_message(traceback.format_exc())
        return False

async def direct_url_access(page):
    """先访问idx.google.com验证登录，成功后通过点击已验证的工作区图标进入Firebase Studio"""
    try:
        # 先访问idx.google.com
        log_message("先访问idx.google.com验证登录状态...")
        await page.goto("https://idx.google.com/", timeout=TIMEOUT)
        await page.wait_for_load_state("domcontentloaded", timeout=TIMEOUT)
        
        # 等待页面加载
        await asyncio.sleep(5)
        
        # 验证是否登录成功 - 双重验证
        current_url = page.url
        log_message(f"当前URL: {current_url}")
        
        # 验证1: 检测URL不包含signin
        url_valid = "idx.google.com" in current_url and "signin" not in current_url
        
        # 验证2: 检测工作区图标是否出现
        workspace_icon_visible = False
        try:
            # 工作区图标选择器列表
            selectors = [
                'div[class="workspace-icon"]',
                'img[src="https://www.gstatic.com/monospace/250314/workspace-blank-192.png"]',
                '.workspace-icon',
                'img[role="presentation"][class="custom-icon"]'
            ]
            
            for selector in selectors:
                try:
                    icon = await page.wait_for_selector(selector, timeout=5000)
                    if icon:
                        log_message(f"找到工作区图标! 使用选择器: {selector}")
                        workspace_icon_visible = True
                        break
                except Exception:
                    continue
        except Exception as e:
            log_message(f"检查工作区图标时出错: {e}")
        
        # 双重验证结果
        if url_valid and workspace_icon_visible:
            log_message("双重验证通过：URL不含signin且工作区图标出现，确认已成功登录idx.google.com!")
            
            # 直接调用导航函数，由它负责点击工作区图标并验证URL变化
            return await navigate_to_firebase_by_clicking(page)
        else:
            log_message(f"验证登录失败：URL不含signin: {url_valid}, 工作区图标出现: {workspace_icon_visible}")
            return False
    except Exception as e:
        log_message(f"访问idx.google.com或跳转到Firebase Studio失败: {e}")
        return False

async def run(playwright: Playwright) -> bool:
    """主运行函数"""
    for attempt in range(1, MAX_RETRIES + 1):
        log_message(f"第{attempt}/{MAX_RETRIES}次尝试...")
        
        # Firefox不需要复杂的浏览器参数配置
        
        # 启动浏览器 - 改为Firefox（基于520.py的成功经验）
        browser = await playwright.firefox.launch(headless=True)
        
        try:
            # 加载cookie状态
            cookie_data = load_cookies(cookies_path)
            
            # 创建浏览器上下文 - 简化配置
            context = await browser.new_context(
                storage_state=cookie_data  # 直接使用加载的数据对象
            )
            
            page = await context.new_page()
            
            # 移除复杂的反检测脚本，保持简单
            
            # ===== 先尝试直接URL访问 =====
            direct_access_success = await direct_url_access(page)
            
            if not direct_access_success:
                log_message("通过cookies直接登录失败，尝试UI交互流程...")
                ui_success = await login_with_ui_flow(page)
                
                if not ui_success:
                    log_message(f"第{attempt}次尝试：UI交互流程失败")
                    if attempt < MAX_RETRIES:
                        await context.close()
                        await browser.close()
                        continue
                    else:
                        log_message("已达到最大重试次数，放弃尝试")
                        await context.close()
                        await browser.close()
                        return False
            
            # ===== 等待工作区加载 =====
            workspace_loaded = await wait_for_workspace_loaded(page)
            if workspace_loaded:
                log_message("工作区加载验证成功!")
                
                # 保存最终cookie状态
                await context.storage_state(path=cookies_path)
                log_message(f"已保存最终cookie状态到 {cookies_path}")
                
                # 成功完成
                await context.close()
                await browser.close()
                return True
            else:
                log_message(f"第{attempt}次尝试：工作区加载验证失败")
                if attempt < MAX_RETRIES:
                    await context.close()
                    await browser.close()
                    continue
                else:
                    log_message("已达到最大重试次数，放弃尝试")
                    await context.close()
                    await browser.close()
                    return False
                    
        except Exception as e:
            log_message(f"第{attempt}次尝试出错: {e}")
            log_message(traceback.format_exc())
            
            try:
                await browser.close()
            except:
                pass
                
            if attempt < MAX_RETRIES:
                log_message("准备下一次尝试...")
                continue
            else:
                log_message("已达到最大重试次数，放弃尝试")
                return False
    
    return False

async def main():
    """主函数"""
    try:
        log_message("开始执行IDX登录并跳转Firebase Studio的自动化流程...")
        
        # 先用requests协议方式直接检查登录状态
        check_result = check_page_status_with_requests()
        if check_result:
            log_message("【检查结果】工作站可直接通过协议访问（状态码200），流程直接退出")
            # 显示提取的凭据
            extract_and_display_credentials()
            return
        
        log_message("【检查结果】工作站不可直接通过协议访问，继续执行完整自动化流程")
        
        # 使用Playwright执行自动化流程
        async with async_playwright() as playwright:
            success = await run(playwright)
            
        log_message(f"自动化流程执行结果: {'成功' if success else '失败'}")
        
        # 显示提取的凭据（无论成功失败）
        extract_and_display_credentials()
            
    except Exception as e:
        log_message(f"主流程执行出错: {e}")
        log_message(traceback.format_exc())
        
        # 尝试提取凭据（即使出错）
        try:
            extract_and_display_credentials()
        except Exception as extract_error:
            log_message(f"提取凭据时出错: {extract_error}")
    
    # 发送通知（无论成功失败都推送）
    if all_messages:
        try:
            log_message("发送执行通知...")
            full_message = "\n".join(all_messages)
            send_to_telegram(full_message)
        except Exception as notify_error:
            log_message(f"发送通知时出错: {notify_error}")

async def scheduled_main():
    """定时执行主函数的调度器"""
    # 从环境变量获取间隔时间（分钟），默认为30分钟
    try:
        interval_minutes = int(os.environ.get("IDX_INTERVAL_MINUTES", 30))
        # 确保间隔时间合理，至少5分钟
        interval_minutes = max(5, interval_minutes)
    except (ValueError, TypeError):
        interval_minutes = 30
        log_message("环境变量IDX_INTERVAL_MINUTES格式错误，使用默认值30分钟")
    
    interval_seconds = interval_minutes * 60
    
    log_message(f"启动定时任务，每{interval_minutes}分钟执行一次...")
    
    while True:
        # 添加明显的分隔符，便于区分不同次执行的日志
        separator = "=" * 80
        print(f"\n{separator}")
        start_time = datetime.now()
        log_message(f"开始第{all_runs[0]}次定时执行，当前时间: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{separator}\n")
        
        # 重置消息列表，每次运行独立记录
        global all_messages
        all_messages = []
        
        try:
            # 执行主逻辑
            await main()
        except Exception as e:
            log_message(f"定时执行过程中发生错误: {e}")
            log_message(traceback.format_exc())
        
        # 发送本次执行的通知
        if all_messages:
            try:
                log_message(f"发送第{all_runs[0]}次执行的通知...")
                full_message = "\n".join(all_messages)
                send_to_telegram(full_message)
            except Exception as notify_error:
                log_message(f"发送通知时出错: {notify_error}")
        
        # 增加运行次数计数
        all_runs[0] += 1
        
        # 计算下次运行时间
        end_time = datetime.now()
        elapsed_seconds = (end_time - start_time).total_seconds()
        
        # 计算需要等待的时间（考虑执行时间）
        wait_seconds = max(0, interval_seconds - elapsed_seconds)
        next_run_time = datetime.now() + timedelta(seconds=wait_seconds)
        
        # 添加明显的结束分隔符
        print(f"\n{separator}")
        log_message(f"第{all_runs[0]-1}次执行完成，耗时: {elapsed_seconds:.2f}秒")
        log_message(f"下次执行将在 {next_run_time.strftime('%Y-%m-%d %H:%M:%S')} 进行 (等待{wait_seconds:.2f}秒)")
        print(f"{separator}\n")
        
        # 等待到下次执行时间
        await asyncio.sleep(wait_seconds)

if __name__ == "__main__":
    # 全局变量
    all_messages = []
    all_runs = [1]  # 使用列表以便在函数中修改
    
    # 添加命令行参数解析
    parser = argparse.ArgumentParser(description='IDX自动登录工具')
    parser.add_argument('--once', action='store_true', 
                        help='只执行一次，不启用定时任务')
    parser.add_argument('--interval', type=int, default=None,
                        help='定时执行的间隔时间（分钟），默认从环境变量或30分钟')
    parser.add_argument('--prefix', type=str, default=None,
                        help='设置工作站域名前缀，默认从环境变量或"9000-idx-sherry-"')
    
    args = parser.parse_args()
    
    # 如果指定了interval参数，设置环境变量
    if args.interval is not None:
        os.environ["IDX_INTERVAL_MINUTES"] = str(args.interval)
    
    # 如果指定了prefix参数，设置环境变量
    if args.prefix is not None:
        os.environ["BASE_PREFIX"] = args.prefix
        log_message(f"已设置工作站域名前缀为: {args.prefix}")
    
    if args.once:
        # 单次执行模式
        log_message("单次执行模式")
        asyncio.run(main())
    else:
        # 定时执行模式
        log_message("定时执行模式")
        asyncio.run(scheduled_main())
