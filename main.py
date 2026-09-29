import os
import asyncio
import aiohttp
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.error import BadRequest
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# 確保在 Python 3.12+ / 3.14 環境下具備全域 Event Loop
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

BOT_TOKEN = "8924211445:AAFFUXCsYImG_XadjPGM-ts6XmN8lSTDjzA"
ADMIN_CHAT_ID = 7203467559
GAS_URL = "https://script.google.com/macros/s/AKfycbxdhzx6EWM5TYGOMJm0AMJpI6SUwmWyegeAMDQ-nt6JRORbsMV5VsaLSm75LRGo916H/exec"

# 本機記憶體快取
local_bills = {}
today_plan_text = "• 載入中，請稍候..."
local_wishes = []
wish_counter = 0

# 背景非同步與 Google 試算表通訊
async def fetch_gas(params):
    try:
        timeout = aiohttp.ClientTimeout(total=4.0)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(GAS_URL, params=params) as resp:
                if resp.status == 200:
                    return await resp.json()
    except Exception as e:
        print(f"[GAS連線提示] {e}")
    return None

async def bg_sync_all():
    global local_bills, today_plan_text, local_wishes, wish_counter
    # 抓請款
    res_bills = await fetch_gas({"action": "get"})
    if isinstance(res_bills, dict):
        local_bills = {int(k): v for k, v in res_bills.items()}

    # 抓行程
    res_plan = await fetch_gas({"action": "get_plan"})
    if isinstance(res_plan, dict) and "plan" in res_plan:
        if res_plan["plan"].strip():
            today_plan_text = res_plan["plan"]

    # 抓願望
    res_wishes = await fetch_gas({"action": "get_wishes"})
    if isinstance(res_wishes, dict) and "wishes" in res_wishes:
        local_wishes = res_wishes["wishes"]
        if local_wishes:
            wish_counter = max([int(w["id"]) for w in local_wishes if str(w["id"]).isdigit()], default=0)

def get_main_menu_markup():
    keyboard = [
        [InlineKeyboardButton("📍 查看今日行程報備", callback_data="show_plan")],
        [InlineKeyboardButton("🎁 查看小寶貝許願池", callback_data="show_wishes")],
        [InlineKeyboardButton("📋 查看待審請款單", callback_data="list_bills")]
    ]
    return InlineKeyboardMarkup(keyboard)

async def safe_edit_text(query, text, reply_markup=None):
    try:
        await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode="Markdown")
    except BadRequest as e:
        if "Message is not modified" in str(e):
            pass
        else:
            raise e

# 主選單
async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "👑 *【小寶貝維運中心】控制面板*\n請選擇您要執行的操作："
    await update.message.reply_text(text, reply_markup=get_main_menu_markup(), parse_mode="Markdown")

# 1. 請款指令（僅限本人）：/bill 項目 金額
async def add_bill(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global local_bills
    if update.effective_user.id != ADMIN_CHAT_ID:
        await update.message.reply_text("⚠️ 只有小寶貝本人可以請款，金主請乖乖審核就好喔！🥰")
        return

    args = context.args
    if len(args) < 2:
        await update.message.reply_text("格式錯誤！請輸入：`/bill 項目 金額`\n例如：`/bill 火鍋 800`", parse_mode="Markdown")
        return

    item = " ".join(args[:-1])
    amount = args[-1]
    applicant = update.effective_user.first_name
    
    new_id = (max(local_bills.keys(), default=0)) + 1
    local_bills[new_id] = {
        "item": item,
        "amount": amount,
        "applicant": applicant,
        "status": "待審核"
    }
    await update.message.reply_text(f"✅ 已建立請款單！\n單號：#{new_id} *{item}* (${amount} TWD)", parse_mode="Markdown")
    asyncio.create_task(fetch_gas({"action": "add", "item": item, "amount": amount, "applicant": applicant}))

# 2. 行程報備指令（僅限本人）：/plan 內容
async def set_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global today_plan_text
    if update.effective_user.id != ADMIN_CHAT_ID:
        await update.message.reply_text("⚠️ 只有小寶貝本人可以更新行程報備喔！")
        return

    if not context.args:
        await update.message.reply_text("請輸入行程內容！例如：\n`/plan 15:00 健身房重訓、18:00 吃火鍋`", parse_mode="Markdown")
        return

    plan_text = " ".join(context.args)
    today_plan_text = plan_text
    await update.message.reply_text(f"📍 *今日行程報備已更新並永久存檔！*\n━━━━━━━━━━━━━━━\n{today_plan_text}", parse_mode="Markdown")
    asyncio.create_task(fetch_gas({"action": "set_plan", "plan": today_plan_text}))

# 3. 文字許願指令（僅限本人）：/wish 內容
async def add_wish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global local_wishes, wish_counter
    if update.effective_user.id != ADMIN_CHAT_ID:
        await update.message.reply_text("⚠️️ 許願池是小寶貝專屬的，金主只有幫忙實現的份喔！💖")
        return

    if not context.args:
        await update.message.reply_text("請輸入願望內容！例如：\n`/wish 想要一隻可愛大娃娃`\n💡 *也可以直接傳照片留言許願喔！*", parse_mode="Markdown")
        return

    item = " ".join(context.args)
    wish_counter += 1
    local_wishes.append({"id": wish_counter, "item": item, "photoId": "", "status": "待實現"})
    await update.message.reply_text(f"✨ 願望已成功丟進許願池：\n「*{item}*」\n金主已收到風聲！", parse_mode="Markdown")
    asyncio.create_task(fetch_gas({"action": "add_wish", "item": item, "photo_id": ""}))

# 4. 照片許願指令（僅限本人）：傳照片 + 留言
async def handle_photo_wish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global local_wishes, wish_counter
    if update.effective_user.id != ADMIN_CHAT_ID:
        await update.message.reply_text("⚠️ 只有小寶貝本人可以傳照片許願喔！")
        return

    caption = update.message.caption or "想要這個禮物"
    item = caption.replace("/wish", "").strip() or "想要這個禮物"
    photo_id = update.message.photo[-1].file_id

    wish_counter += 1
    local_wishes.append({"id": wish_counter, "item": item, "photoId": photo_id, "status": "待實現"})
    await update.message.reply_text(f"📸 *照片願望已存入許願池！*\n• 願望項目：*{item}*", parse_mode="Markdown")
    asyncio.create_task(fetch_gas({"action": "add_wish", "item": item, "photo_id": photo_id}))

# 按鈕回調處理
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global local_bills, today_plan_text, local_wishes
    query = update.callback_query
    data = query.data
    approver = query.from_user.first_name

    try:
        await query.answer()
    except Exception:
        pass

    # 回主選單
    if data == "back_main":
        text = "👑 *【小寶貝維運中心】控制面板*\n請選擇您要執行的操作："
        if query.message.photo:
            try:
                await query.message.delete()
            except Exception:
                pass
            await context.bot.send_message(chat_id=query.message.chat_id, text=text, reply_markup=get_main_menu_markup(), parse_mode="Markdown")
        else:
            await safe_edit_text(query, text, get_main_menu_markup())
        return

    # 1. 查看今日行程
    elif data == "show_plan":
        text = (
            "📍 *【小寶貝今日行程報備】*\n"
            "━━━━━━━━━━━━━━━\n"
            f"{today_plan_text}\n"
            "━━━━━━━━━━━━━━━\n"
            "行程即時同步，請金主放心！"
        )
        keyboard = [
            [InlineKeyboardButton("🎁 去看許願池", callback_data="show_wishes")],
            [InlineKeyboardButton("⬅️ 回主選單", callback_data="back_main")]
        ]
        await safe_edit_text(query, text, InlineKeyboardMarkup(keyboard))
        return

    # 2. 查看許願池清單（每次點擊即時重新拉取試算表）
    elif data == "show_wishes":
        res_wishes = await fetch_gas({"action": "get_wishes"})
        if isinstance(res_wishes, dict) and "wishes" in res_wishes:
            local_wishes = res_wishes["wishes"]

        pending_wishes = [
            w for w in local_wishes 
            if "已實現" not in str(w.get("status", "")).strip()
        ]

        if not pending_wishes:
            text = "✨ 目前許願池空空如也，或是金主已經把所有願望全部實現了！"
            keyboard = [[InlineKeyboardButton("⬅️ 回主選單", callback_data="back_main")]]
            if query.message.photo:
                try:
                    await query.message.delete()
                except Exception:
                    pass
                await context.bot.send_message(chat_id=query.message.chat_id, text=text, reply_markup=InlineKeyboardMarkup(keyboard))
            else:
                await safe_edit_text(query, text, InlineKeyboardMarkup(keyboard))
            return

        text = "🎁 *【小寶貝的許願池】*\n點選願望查看詳情與照片："
        keyboard = []
        for w in pending_wishes:
            icon = "📸 " if w.get("photoId") else "✨ "
            keyboard.append([InlineKeyboardButton(f"{icon}{w['item']}", callback_data=f"view_wish_{w['id']}")])
        keyboard.append([InlineKeyboardButton("⬅️ 回主選單", callback_data="back_main")])

        if query.message.photo:
            try:
                await query.message.delete()
            except Exception:
                pass
            await context.bot.send_message(chat_id=query.message.chat_id, text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        else:
            await safe_edit_text(query, text, InlineKeyboardMarkup(keyboard))
        return

    # 3. 檢視願望詳情
    elif data.startswith("view_wish_"):
        w_id = int(data.split("_")[2])
        w = next((x for x in local_wishes if int(x["id"]) == w_id), None)
        if not w or "已實現" in str(w.get("status", "")):
            await safe_edit_text(query, "此願望已實現或不存在！", InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ 回許願池", callback_data="show_wishes")]]))
            return

        caption = f"🎁 *願望詳情*\n━━━━━━━━━━━━━━━\n項目：*{w['item']}*\n━━━━━━━━━━━━━━━\n金主要幫忙實現嗎？"
        keyboard = [
            [InlineKeyboardButton("💖 幫妳實現", callback_data=f"fulfill_{w_id}")],
            [InlineKeyboardButton("⬅️ 返回許願池", callback_data="show_wishes")]
        ]

        if w.get("photoId"):
            try:
                await query.message.delete()
            except Exception:
                pass
            await context.bot.send_photo(
                chat_id=query.message.chat_id,
                photo=w["photoId"],
                caption=caption,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
        else:
            await safe_edit_text(query, caption, InlineKeyboardMarkup(keyboard))
        return

    # 4. 認領/實現願望
    elif data.startswith("fulfill_"):
        w_id = int(data.split("_")[1])
        target_wish = next((w for w in local_wishes if int(w["id"]) == w_id), None)
        if target_wish and "已實現" not in str(target_wish.get("status", "")):
            target_wish["status"] = "已實現"
            item = target_wish["item"]

            text = (
                f"🎉 *願望認領成功！*\n"
                f"大方金主 {approver} 承諾實現：\n"
                f"「*{item}*」\n"
                f"好感度直接提升 100 分！"
            )
            keyboard = [[InlineKeyboardButton("🎁 查看其他願望", callback_data="show_wishes")]]
            
            if query.message.photo:
                try:
                    await query.edit_message_caption(caption=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
                except Exception:
                    pass
            else:
                await safe_edit_text(query, text, InlineKeyboardMarkup(keyboard))

            asyncio.create_task(fetch_gas({"action": "fulfill_wish", "id": w_id}))

            notify_msg = (
                f"🎊 *【願望達成通報！】*\n"
                f"金主 *{approver}* 剛剛認領了你的願望！\n"
                f"• 願望項目：{item}\n"
                f"太棒了～快去給他一個大擁抱吧！"
            )
            try:
                await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=notify_msg, parse_mode="Markdown")
            except Exception:
                pass
        return

    # 5. 請款清單
    elif data == "list_bills":
        pending_bills = {k: v for k, v in local_bills.items() if v.get("status") == "待審核"}
        if not pending_bills:
            text = "🎉 目前沒有任何待審核的請款單！金主已完全結清。"
            keyboard = [[InlineKeyboardButton("⬅️ 回主選單", callback_data="back_main")]]
            await safe_edit_text(query, text, InlineKeyboardMarkup(keyboard))
            return

        text = "📋 *【待審核請款清單】*\n點選欲審核的項目："
        keyboard = []
        for b_id, b_info in pending_bills.items():
            btn_text = f"單號#{b_id}：{b_info['item']} (${b_info['amount']})"
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"view_{b_id}")])
        keyboard.append([InlineKeyboardButton("⬅️ 回主選單", callback_data="back_main")])
        await safe_edit_text(query, text, InlineKeyboardMarkup(keyboard))
        return

    # 6. 單筆請款
    elif data.startswith("view_"):
        b_id = int(data.split("_")[1])
        b_info = local_bills.get(b_id)
        if not b_info or b_info.get("status") != "待審核":
            await safe_edit_text(query, "此請款單已處理完成或不存在！", InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ 回主選單", callback_data="back_main")]]))
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
        await safe_edit_text(query, text, InlineKeyboardMarkup(keyboard))
        return

    # 7. 准予核銷
    elif data.startswith("approve_"):
        b_id = int(data.split("_")[1])
        if b_id in local_bills:
            local_bills[b_id]["status"] = "已核銷"
            item = local_bills[b_id]["item"]
            amount = local_bills[b_id]["amount"]

            text = (
                f"✅ *核銷成功！*\n"
                f"金主 {approver} 已核准單號 #{b_id}（{item} - ${amount} TWD）。\n"
                f"謝謝老闆！"
            )
            keyboard = [[InlineKeyboardButton("📋 查看其他請款", callback_data="list_bills")]]
            await safe_edit_text(query, text, InlineKeyboardMarkup(keyboard))

            asyncio.create_task(fetch_gas({"action": "update", "id": b_id, "status": "已核銷"}))

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
            except Exception:
                pass
        return

    # 8. 大膽駁回
    elif data.startswith("reject_"):
        b_id = int(data.split("_")[1])
        if b_id in local_bills:
            local_bills[b_id]["status"] = "已駁回"
            item = local_bills[b_id]["item"]

            text = (
                f"⚠️ *請款已被駁回！*\n"
                f"審核人 {approver} 駁回了單號 #{b_id}（{item}）。\n"
                f"請老闆自重。"
            )
            keyboard = [[InlineKeyboardButton("📋 查看其他請款", callback_data="list_bills")]]
            await safe_edit_text(query, text, InlineKeyboardMarkup(keyboard))

            asyncio.create_task(fetch_gas({"action": "update", "id": b_id, "status": "已駁回"}))

            notify_msg = (
                f"🚨 *【駁回警報】*\n"
                f"金主 *{approver}* 駁回了請款！\n"
                f"• 單號：#{b_id}\n"
                f"• 項目：{item}\n"
                f"請準備啟動防護機制。"
            )
            try:
                await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=notify_msg, parse_mode="Markdown")
            except Exception:
                pass
        return

async def health_check(request):
    return web.Response(text="OK")

async def start_web_server():
    server = web.Application()
    server.router.add_get("/", health_check)
    runner = web.AppRunner(server)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

async def run_bot():
    app = Application.builder().token(BOT_TOKEN).build()
    
    commands = [
        BotCommand("menu", "喚出主選單"),
        BotCommand("wish", "新增願望 (例: /wish 想要新耳機)"),
        BotCommand("plan", "更新行程 (例: /plan 15:00 健身房)"),
        BotCommand("bill", "新增請款 (例: /bill 火鍋 800)"),
    ]
    app.add_handler(CommandHandler("start", menu))
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CommandHandler("wish", add_wish))
    app.add_handler(CommandHandler("plan", set_plan))
    app.add_handler(CommandHandler("bill", add_bill))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo_wish))
    app.add_handler(CallbackQueryHandler(handle_callback))

    # 啟動 Web 服務保持活躍
    await start_web_server()

    # 初始化並啟動 Telegram 機器人
    await app.initialize()
    await app.bot.set_my_commands(commands)
    await app.start()
    await app.updater.start_polling()

    # 開機同步試算表歷史紀錄
    asyncio.create_task(bg_sync_all())

    print("小寶貝維運機器人運行中...")

    stop_event = asyncio.Event()
    await stop_event.wait()

def main():
    asyncio.run(run_bot())

if __name__ == "__main__":
    main()
