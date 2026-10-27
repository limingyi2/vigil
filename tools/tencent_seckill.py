# -*- coding: utf-8 -*-
"""
腾讯云轻量应用服务器秒杀抢购脚本
基于 Playwright 浏览器自动化

功能：
1. 首次运行手动登录腾讯云，cookie 自动保存到本地
2. 后续运行自动加载 cookie，无需重复登录
3. 定时在 10:00 / 15:00 自动点击抢购按钮
4. 支持多页面并发抢购（提高成功率）
5. 抢购成功自动跳转到订单确认页
6. 详细日志 + 失败自动重试

使用方法：
  pip install playwright
  playwright install chromium
  python tencent_seckill.py

首次运行会打开浏览器让你手动登录腾讯云，登录成功后按回车继续。
之后运行会自动加载保存的 cookie。
"""

import json
import os
import sys
import time
import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path

try:
    from playwright.async_api import async_playwright, TimeoutError as PWTimeout
except ImportError:
    print("请先安装 playwright：pip install playwright && playwright install chromium")
    sys.exit(1)

# ── 配置
SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "tencent_seckill_config.json"
LOG_PATH = SCRIPT_DIR / "seckill.log"

def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

CFG = load_config()

# ── 日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("seckill")

# ── 工具函数
def now_str():
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]

def next_seckill_time():
    """计算下一场秒杀时间"""
    now = datetime.now()
    times = []
    for t in CFG["seckill_times"]:
        h, m = map(int, t.split(":"))
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if target > now:
            times.append(target)
    if not times:
        # 今天的都过了，取明天第一场
        h, m = map(int, CFG["seckill_times"][0].split(":"))
        target = (now + timedelta(days=1)).replace(hour=h, minute=m, second=0, microsecond=0)
        times.append(target)
    return min(times)

async def save_cookie(context, path):
    cookies = await context.cookies()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cookies, f, ensure_ascii=False, indent=2)
    log.info(f"cookie 已保存到 {path}（{len(cookies)} 条）")

async def load_cookie(context, path):
    if not os.path.exists(path):
        return False
    with open(path, "r", encoding="utf-8") as f:
        cookies = json.load(f)
    await context.add_cookies(cookies)
    log.info(f"cookie 已加载（{len(cookies)} 条）")
    return True

# ── 核心抢购逻辑
async def login_if_needed(page, cookie_path):
    """检查登录状态，未登录则等待用户手动登录"""
    await page.goto(CFG["activity_url"], wait_until="domcontentloaded")
    await page.wait_for_timeout(3000)

    # 检查是否已登录（页面上有用户头像/昵称说明已登录）
    logged_in = await page.evaluate("""() => {
        // 腾讯云登录后右上角会显示用户名/头像
        const userEl = document.querySelector('.user-name, .username, [class*="user-name"], [class*="username"]');
        if (userEl && userEl.textContent.trim()) return true;
        // 检查是否有"登录"按钮
        const loginBtn = Array.from(document.querySelectorAll('a, button, span')).find(
            el => el.textContent.trim() === '登录' || el.textContent.trim() === '注册/登录'
        );
        return !loginBtn;
    }""")

    if logged_in:
        log.info("检测到已登录状态")
        return True

    log.info("未检测到登录状态，请在浏览器中手动登录腾讯云")
    log.info("登录成功后，回到终端按回车键继续...")
    input()

    # 登录后保存 cookie
    await page.wait_for_timeout(2000)
    await save_cookie(page.context, cookie_path)
    log.info("登录完成，cookie 已保存")
    return True

async def find_and_click_seckill(page, page_id):
    """在活动页面找到目标商品的抢购按钮并点击"""
    try:
        # 滚动到目标商品区域（用普通字符串拼接，避免 f-string 花括号冲突）
        scroll_js = """() => {
            const keyword = '__KEYWORD__';
            const price = '__PRICE__';
            const cards = Array.from(document.querySelectorAll('*')).filter(el => {
                const text = el.textContent || '';
                return text.includes(keyword) &&
                       text.includes(price) &&
                       el.offsetParent !== null;
            });
            if (cards.length > 0) {
                cards[0].scrollIntoView({behavior: 'smooth', block: 'center'});
                return true;
            }
            return false;
        }""".replace("__KEYWORD__", CFG["target_keyword"]).replace("__PRICE__", CFG["target_price"])

        await page.evaluate(scroll_js)

        await page.wait_for_timeout(500)

        # 查找"立即抢购"或"抢购"按钮
        clicked = await page.evaluate("""() => {
            const buttons = Array.from(document.querySelectorAll('button, a, [role="button"], div[class*="btn"], span[class*="btn"]'));
            for (const btn of buttons) {
                const text = (btn.textContent || '').trim();
                if (text === '立即抢购' || text === '抢购' || text === '马上抢' || text === '立即购买') {
                    // 确保按钮可见且可点击
                    const rect = btn.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0 && !btn.disabled) {
                        btn.click();
                        return text;
                    }
                }
            }
            return null;
        }""")

        if clicked:
            log.info(f"[页面{page_id}] 点击了 '{clicked}' 按钮")
            return True
        else:
            log.warning(f"[页面{page_id}] 未找到抢购按钮（可能还没到时间或已抢光）")
            return False

    except Exception as e:
        log.error(f"[页面{page_id}] 点击抢购异常: {e}")
        return False

async def check_order_page(page, page_id):
    """检查是否跳转到了订单确认页（抢购成功）"""
    url = page.url
    if 'order' in url.lower() or 'pay' in url.lower() or 'deal' in url.lower() or 'confirm' in url.lower():
        log.info(f"[页面{page_id}] 可能已进入订单页: {url}")
        return True

    # 检查页面内容
    try:
        content = await page.evaluate("() => document.body.innerText.substring(0, 500)")
        if '订单' in content or '支付' in content or '确认订单' in content or '提交订单' in content:
            log.info(f"[页面{page_id}] 页面内容显示已进入订单流程")
            return True
    except:
        pass
    return False

async def seckill_worker(browser, page_id, target_time, cookie_path):
    """单个页面的抢购工作器"""
    context = await browser.new_context(
        viewport={"width": 1920, "height": 1080},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    )

    # 加载 cookie
    if os.path.exists(cookie_path):
        with open(cookie_path, "r", encoding="utf-8") as f:
            cookies = json.load(f)
        await context.add_cookies(cookies)
        log.info(f"[页面{page_id}] cookie 已加载（{len(cookies)} 条）")

    page = await context.new_page()

    try:
        # 打开活动页面
        log.info(f"[页面{page_id}] 打开活动页面: {CFG['activity_url']}")
        await page.goto(CFG["activity_url"], wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(2000)

        # 等待到秒杀时间
        while True:
            now = datetime.now()
            remaining = (target_time - now).total_seconds()

            if remaining <= 0:
                break

            if remaining > 10:
                # 超过10秒，每秒刷新一次倒计时
                log.info(f"[页面{page_id}] 距离秒杀还有 {remaining:.1f} 秒")
                await asyncio.sleep(min(remaining - 5, 1))
            else:
                # 最后10秒，高频刷新
                log.info(f"[页面{page_id}] 倒计时 {remaining:.2f} 秒")
                await asyncio.sleep(0.05)

        # 秒杀时间到！开始抢购
        log.info(f"[页面{page_id}] ===== 秒杀开始！=====")
        start_time = time.time()

        for attempt in range(CFG["最大重试次数"]):
            elapsed = time.time() - start_time

            # 刷新页面（确保拿到最新的按钮状态）
            if attempt % 5 == 0 and attempt > 0:
                try:
                    await page.reload(wait_until="domcontentloaded", timeout=10000)
                    await page.wait_for_timeout(200)
                except:
                    pass

            # 点击抢购
            success = await find_and_click_seckill(page, page_id)

            if success:
                await page.wait_for_timeout(1000)
                # 检查是否进入订单页
                if await check_order_page(page, page_id):
                    log.info(f"[页面{page_id}] +++++ 抢购成功！已进入订单页 +++++")
                    log.info(f"[页面{page_id}] 请在浏览器中完成支付（60分钟内有效）")
                    # 保持页面打开，不关闭
                    await asyncio.sleep(300)  # 保持5分钟让用户操作
                    return True

            # 检查是否已抢光
            try:
                sold_out = await page.evaluate("""() => {
                    const text = document.body.innerText;
                    return text.includes('已抢光') || text.includes('已售罄') || text.includes('卖完了') || text.includes('暂无库存');
                }""")
                if sold_out:
                    log.warning(f"[页面{page_id}] 商品已抢光，停止重试")
                    break
            except:
                pass

            if attempt % 10 == 0:
                log.info(f"[页面{page_id}] 第 {attempt+1} 次尝试，已耗时 {elapsed:.1f} 秒")

            await asyncio.sleep(CFG["点击间隔毫秒"] / 1000)

        log.warning(f"[页面{page_id}] 抢购失败（尝试 {CFG['最大重试次数']} 次后未成功）")
        return False

    except Exception as e:
        log.error(f"[页面{page_id}] 工作器异常: {e}")
        return False
    finally:
        # 不关闭 context，让用户能看到最终页面
        pass

async def main():
    log.info("=" * 60)
    log.info("腾讯云轻量应用服务器秒杀抢购脚本启动")
    log.info(f"活动页面: {CFG['activity_url']}")
    log.info(f"目标商品: {CFG['target_keyword']} {CFG['target_price']}元")
    log.info(f"秒杀时间: {', '.join(CFG['seckill_times'])}")
    log.info(f"并发页面数: {CFG['并发页面数']}")
    log.info("=" * 60)

    cookie_path = SCRIPT_DIR / CFG["cookie_file"]

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=CFG["headless"])

        # 第一步：登录（用一个单独的页面完成登录）
        login_page = await browser.new_page()
        await login_if_needed(login_page, cookie_path)

        # 计算下一场秒杀时间
        target = next_seckill_time()
        log.info(f"下一场秒杀时间: {target.strftime('%Y-%m-%d %H:%M:%S')}")

        now = datetime.now()
        wait_seconds = (target - now).total_seconds()

        if wait_seconds > CFG["提前启动秒数"]:
            log.info(f"距离秒杀还有 {wait_seconds/60:.1f} 分钟，等待到提前 {CFG['提前启动秒数']} 秒再启动抢购页面...")
            await asyncio.sleep(wait_seconds - CFG["提前启动秒数"])

        log.info(f"提前 {CFG['提前启动秒数']} 秒，启动 {CFG['并发页面数']} 个抢购页面...")

        # 启动多个并发抢购页面
        tasks = []
        for i in range(CFG["并发页面数"]):
            task = asyncio.create_task(seckill_worker(browser, i+1, target, cookie_path))
            tasks.append(task)
            await asyncio.sleep(0.5)  # 错开启动，避免同时请求

        # 等待任意一个成功
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

        # 检查是否有成功的
        success = any(task.result() for task in done if task.done())

        if success:
            log.info("=" * 60)
            log.info("抢购成功！请在浏览器中完成支付")
            log.info("=" * 60)
            # 保持浏览器打开，让用户操作
            log.info("浏览器将保持打开状态，按 Ctrl+C 退出")
            await asyncio.sleep(3600)  # 保持1小时
        else:
            log.warning("所有页面抢购失败")
            # 取消剩余任务
            for task in pending:
                task.cancel()

        await browser.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("用户中断，退出")
    except Exception as e:
        log.error(f"脚本异常退出: {e}", exc_info=True)
