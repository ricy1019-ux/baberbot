import os
import requests
import asyncio
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

BOT_TOKEN = "8924211445:AAFFUXCsYImG_XadjPGM-ts6XmN8lSTDjzA"
ADMIN_CHAT_ID = 7203467559
GAS_URL = "https://script.google.com/macros/s/AKfycbxR8e4WUAQABE1wqa_WNpwKi-Bf6GOhlUi0bbCtYsR3GRpP-zFCb1vCoDrjKW6_OAbyvg/exec"

# 本地快取，加速讀取
local_cache = {}
is_cache_loaded = False

def sync_get_bills():
    try:
        res = requests.get(GAS_URL, params={"action": "get"}, timeout=8).json()
        return {int(k): v for k, v in res.items()}
    except Exception as e:
        print(f"讀取試算表失敗: {e}")
        return {}

def sync_add_bill(item, amount, applicant):
    try:
        res = requests.get(GAS_URL, params={
            "action": "add",
            "item": item,
            "amount": amount,
            "applicant": applicant
        }, timeout=8).json()
        return res.get("id")
    except Exception as e:
        print(f"寫入試算表失敗: {e}")
        return None

def sync_update_status(bill_id, status):
    try:
        requests.get(GAS_URL, params={
            "action": "update",
            "id": bill_id,
            "status": status
        }, timeout=8)
    except Exception as e:
        print(f"更新狀態失敗: {e}")

async def add_bill(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global local_cache
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("格式錯誤！請輸入：`/bill 項目 金額`\n例如：`/bill 火鍋 800`", parse_mode="Markdown")
        return

    item = " ".join(args[:-1])
    amount = args[-1]
    applicant = update.effective_user.first_name

    # 提示使用者正在寫入
    msg = await update.message.reply_text("⏳ 正在登記中...")
    loop = asyncio.get_running_loop()
    bill_id = await loop.run_in_executor(None, sync_add_bill, item, amount, applicant)

    if bill_id:
        local_cache[int(bill_id)] = {
            "item": item,
            "amount": amount,
            "applicant": applicant,
            "status": "待審核"
        }
        await msg.edit_text(f"✅ 已成功建立並存入請款單！\n單號：#{bill_id} *{item}* (${amount} TWD)", parse_mode="Markdown")
    else:
        await msg.edit_text("連線試算表逾時，請稍後再試。")

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "👑 *【小寶貝維運中心】控制面板*\n請選擇您要執行的操作："
    keyboard = [
        [InlineKeyboardButton("✨ 小寶貝今日活動", callback_data="show_perks")],
        [InlineKeyboardButton("📋 查看待審請款單", callback_data="list_bills")]
    ]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global local_cache, is_cache_loaded
    query = update.callback_query
    data = query.data
    approver = query.from_user.first_name
    await query.answer()

    if data == "show_perks":
        text = (
            "🎁 *【小寶貝今日活動】*\n"
            "━━━━━━━━━━━━━━━\n"
            "• 下午三點去運動\n"
            "━━━━━━━━━━━━━━━\n"
        )
        keyboard = [
            [InlineKeyboardButton("📝 前往審核請款單", callback_data="list_bills")],
            [InlineKeyboardButton("⬅️ 回主選單", callback_data="back_main")]
        ]
        await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data == "list_bills":
        # 第一次從 Google 讀取，之後優先使用快速快取
        if not is_cache_loaded:
            loop = asyncio.get_running_loop()
            local_cache = await loop.run_in_executor(None, sync_get_bills)
            is_cache_loaded = True

        pending_bills = {k: v for k, v in local_cache.items() if v.get("status") == "待審核"}
        if not pending_bills:
            text = "🎉 目前沒有任何待審核的請款單！金主已完全結清。"
            keyboard = [[InlineKeyboardButton("⬅️ 回主選單", callback_data="back_main")]]
            await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard))
            return

        text = "📋 *【待審核請款清單】*\n點選欲審核的項目："
        keyboard = []
        for b_id, b_info in pending_bills.items():
            btn_text = f"單號#{b_id}：{b_info['item']} (${b_info['amount']})"
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"view_{b_id}")])
        keyboard.append([InlineKeyboardButton("⬅️ 回主選單", callback_data="back_main")])
        await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("view_"):
        b_id = int(data.split("_")[1])
        b_info = local_cache.get(b_id)
        if not b_info or b_info.get("status") != "待審核":
            await query.edit_message_text("此請款單已處理完成或不存在！")
            return

        text = (
            f"🧾 *請款審核中 - 單號 #{b_id}*\n"
            f"━━━━━━━━━━━━━━━\n"
            f"👤 請款人：{b_info['applicant']}\n"
            f"🍰 事由：{b_info['item']}\n"
            f"💵 金額：`${b_info['amount']} TWD`\n"
            f"━━━━━━━━━━━━━━━\n"
            f"請審核人決定："
        )
        keyboard = [
            [
                InlineKeyboardButton("💰 准予核銷", callback_data=f"approve_{b_id}"),
                InlineKeyboardButton("❌ 大膽駁回", callback_data=f"reject_{b_id}")
            ],
            [InlineKeyboardButton("⬅️ 返回清單", callback_data="list_bills")]
        ]
        await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("approve_"):
        b_id = int(data.split("_")[1])
        if b_id in local_cache:
            # 立即在本地標註核銷，秒速更新畫面給男友看
            local_cache[b_id]["status"] = "已核銷"
            item = local_cache[b_id]["item"]
            amount = local_cache[b_id]["amount"]

            text = (
                f"✅ *核銷成功！*\n"
                f"金主 {approver} 已核准單號 #{b_id}（{item} - ${amount} TWD）。\n"
                f"謝謝老闆！"
            )
            keyboard = [[InlineKeyboardButton("📋 查看其他請款", callback_data="list_bills")]]
            await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

            # 背景非同步同步至 Google 試算表（不阻塞使用者操作）
            loop = asyncio.get_running_loop()
            loop.run_in_executor(None, sync_update_status, b_id, "已核銷")

            # 主動通報你
            notify_msg = (
                f"🔔 *【入帳通報】*\n"
                f"金主 *{approver}* 剛剛核准了請款！\n"
                f"• 單號：#{b_id}\n"
                f"• 項目：{item}\n"
                f"• 金額：${amount} TWD\n"
                f"請記得查核入帳款項～"
            )
            try:
                await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=notify_msg, parse_mode="Markdown")
            except Exception as e:
                print(f"通報發送失敗: {e}")

    elif data.startswith("reject_"):
        b_id = int(data.split("_")[1])
        if b_id in local_cache:
            local_cache[b_id]["status"] = "已駁回"
            item = local_cache[b_id]["item"]

            text = (
                f"⚠️ *請款已被駁回！*\n"
                f"審核人 {approver} 駁回了單號 #{b_id}（{item}）。\n"
                f"請老闆自重。"
            )
            keyboard = [[InlineKeyboardButton("📋 查看其他請款", callback_data="list_bills")]]
            await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

            # 背景非同步同步至 Google 試算表
            loop = asyncio.get_running_loop()
            loop.run_in_executor(None, sync_update_status, b_id, "已駁回")

            # 主動通報你
            notify_msg = (
                f"🚨 *【駁回警報】*\n"
                f"金主 *{approver}* 駁回了請款！\n"
                f"• 單號：#{b_id}\n"
                f"• 項目：{item}\n"
                f"請準備啟動防護機制。"
            )
            try:
                await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=notify_msg, parse_mode="Markdown")
            except Exception as e:
                print(f"通報發送失敗: {e}")

    elif data == "back_main":
        text = "👑 *【小寶貝維運中心】控制面板*\n請選擇您要執行的操作："
        keyboard = [
            [InlineKeyboardButton("✨ 小寶貝今日活動", callback_data="show_perks")],
            [InlineKeyboardButton("📋 查看待審請款單", callback_data="list_bills")]
        ]
        await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def health_check(request):
    return web.Response(text="Bot is running!")

async def post_init(application: Application):
    global local_cache, is_cache_loaded
    commands = [
        BotCommand("menu", "喚出主選單"),
        BotCommand("bill", "新增請款 (例: /bill 火鍋 800)"),
    ]
    await application.bot.set_my_commands(commands)

    # 機器人啟動時就先在背景把 Google 試算表資料抓下來存好
    loop = asyncio.get_running_loop()
    local_cache = await loop.run_in_executor(None, sync_get_bills)
    is_cache_loaded = True

    server = web.Application()
    server.router.add_get("/", health_check)
    runner = web.AppRunner(server)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

def main():
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", menu))
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CommandHandler("bill", add_bill))
    app.add_handler(CallbackQueryHandler(handle_callback))

    print("請款機器人運行中...")
    app.run_polling()

if __name__ == "__main__":
    main()
