import os
import requests
import asyncio
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

BOT_TOKEN = "8924211445:AAFFUXCsYImG_XadjPGM-ts6XmN8lSTDjzA"
ADMIN_CHAT_ID = 7203467559
GAS_URL = "https://script.google.com/macros/s/AKfycbxR8e4WUAQABE1wqa_WNpwKi-Bf6GOhlUi0bbCtYsR3GRpP-zFCb1vCoDrjKW6_OAbyvg/exec"

local_cache = {}
is_cache_loaded = False
today_plan_cache = "• 下午三點去運動\n• 晚上乖乖回家"
wishes_cache = []

def sync_get_all():
    global local_cache, today_plan_cache, wishes_cache
    try:
        res_bills = requests.get(GAS_URL, params={"action": "get"}, timeout=5).json()
        local_cache = {int(k): v for k, v in res_bills.items()}
    except Exception as e:
        print(f"抓請款失敗: {e}")

    try:
        res_plan = requests.get(GAS_URL, params={"action": "get_plan"}, timeout=5).json()
        today_plan_cache = res_plan.get("plan", today_plan_cache)
    except Exception as e:
        print(f"抓行程失敗: {e}")

    try:
        res_wishes = requests.get(GAS_URL, params={"action": "get_wishes"}, timeout=5).json()
        wishes_cache = res_wishes.get("wishes", [])
    except Exception as e:
        print(f"抓願望失敗: {e}")

def sync_add_bill(item, amount, applicant):
    try:
        res = requests.get(GAS_URL, params={"action": "add", "item": item, "amount": amount, "applicant": applicant}, timeout=8).json()
        return res.get("id")
    except Exception:
        return None

def sync_update_bill_status(bill_id, status):
    try:
        requests.get(GAS_URL, params={"action": "update", "id": bill_id, "status": status}, timeout=8)
    except Exception:
        pass

def sync_set_plan(plan_text):
    try:
        requests.get(GAS_URL, params={"action": "set_plan", "plan": plan_text}, timeout=8)
    except Exception:
        pass

def sync_add_wish(item, photo_id=""):
    try:
        res = requests.get(GAS_URL, params={"action": "add_wish", "item": item, "photo_id": photo_id}, timeout=8).json()
        return res.get("id")
    except Exception:
        return None

def sync_fulfill_wish(wish_id):
    try:
        requests.get(GAS_URL, params={"action": "fulfill_wish", "id": wish_id}, timeout=8)
    except Exception:
        pass

# 1. 請款指令：/bill 項目 金額
async def add_bill(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global local_cache
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("格式錯誤！請輸入：`/bill 項目 金額`\n例如：`/bill 火鍋 800`", parse_mode="Markdown")
        return

    item = " ".join(args[:-1])
    amount = args[-1]
    applicant = update.effective_user.first_name

    msg = await update.message.reply_text("⏳ 正在存入 Google 試算表...")
    loop = asyncio.get_running_loop()
    bill_id = await loop.run_in_executor(None, sync_add_bill, item, amount, applicant)

    if bill_id:
        local_cache[int(bill_id)] = {
            "item": item,
            "amount": amount,
            "applicant": applicant,
            "status": "待審核"
        }
        await msg.edit_text(f"✅ 已成功建立並永久存入！\n單號：#{bill_id} *{item}* (${amount} TWD)", parse_mode="Markdown")
    else:
        await msg.edit_text("連線試算表逾時，請稍後再試。")

# 2. 行程報備指令：/plan 內容
async def set_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global today_plan_cache
    if update.effective_user.id != ADMIN_CHAT_ID:
        await update.message.reply_text("⚠️ 只有小寶貝本人可以更新行程報備喔！")
        return

    if not context.args:
        await update.message.reply_text("請輸入行程內容！例如：\n`/plan 15:00 健身房重訓、18:00 吃火鍋`", parse_mode="Markdown")
        return

    plan_text = " ".join(context.args)
    today_plan_cache = plan_text

    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, sync_set_plan, plan_text)

    await update.message.reply_text(f"📍 *今日行程報備已更新並永久存檔！*\n━━━━━━━━━━━━━━━\n{plan_text}", parse_mode="Markdown")

# 3. 文字許願指令：/wish 願望內容
async def add_wish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global wishes_cache
    if not context.args:
        await update.message.reply_text("請輸入願望內容！例如：\n`/wish 想要一隻可愛大娃娃`\n💡 *也可以直接傳送照片，並在照片說明填寫願望喔！*", parse_mode="Markdown")
        return

    item = " ".join(context.args)
    msg = await update.message.reply_text("⏳ 正在丟入許願池...")
    loop = asyncio.get_running_loop()
    wish_id = await loop.run_in_executor(None, sync_add_wish, item, "")

    if wish_id:
        wishes_cache.append({"id": wish_id, "item": item, "photoId": "", "status": "待實現"})
        await msg.edit_text(f"✨ 願望已成功丟進許願池：\n「*{item}*」\n金主已收到風聲！", parse_mode="Markdown")
    else:
        await msg.edit_text("連線試算表失敗，請稍後再試。")

# 4. 照片許願：傳照片 + 說明
async def handle_photo_wish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global wishes_cache
    if update.effective_user.id != ADMIN_CHAT_ID:
        return

    caption = update.message.caption or "想要這個禮物"
    # 如果有打 /wish 就把指令字眼去掉，沒打就直接當作願望文字
    if caption.startswith("/wish"):
        item = caption.replace("/wish", "").strip() or "想要這個禮物"
    else:
        item = caption

    photo_id = update.message.photo[-1].file_id  # 拿最高畫質的那張

    msg = await update.message.reply_text("⏳ 收到照片！正在將願望存入許願池...")
    loop = asyncio.get_running_loop()
    wish_id = await loop.run_in_executor(None, sync_add_wish, item, photo_id)

    if wish_id:
        wishes_cache.append({"id": wish_id, "item": item, "photoId": photo_id, "status": "待實現"})
        await msg.edit_text(f"📸 *照片願望已成功存入許願池！*\n• 願望項目：*{item}*\n金主點選時會直接展示照片喔！", parse_mode="Markdown")
    else:
        await msg.edit_text("連線試算表失敗，請稍後再試。")

# 主選單指令：/menu
async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "👑 *【小寶貝維運中心】控制面板*\n請選擇您要執行的操作："
    keyboard = [
        [InlineKeyboardButton("📍 查看今日行程報備", callback_data="show_plan")],
        [InlineKeyboardButton("🎁 查看小寶貝許願池", callback_data="show_wishes")],
        [InlineKeyboardButton("📋 查看待審請款單", callback_data="list_bills")]
    ]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global local_cache, today_plan_cache, wishes_cache
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
        keyboard = [
            [InlineKeyboardButton("📍 查看今日行程報備", callback_data="show_plan")],
            [InlineKeyboardButton("🎁 查看小寶貝許願池", callback_data="show_wishes")],
            [InlineKeyboardButton("📋 查看待審請款單", callback_data="list_bills")]
        ]
        if query.message.photo:
            await query.message.delete()
            await context.bot.send_message(chat_id=query.message.chat_id, text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        else:
            await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return

    # 今日行程
    elif data == "show_plan":
        text = (
            "📍 *【小寶貝今日行程報備】*\n"
            "━━━━━━━━━━━━━━━\n"
            f"{today_plan_cache}\n"
            "━━━━━━━━━━━━━━━\n"
            "行程即時同步，請金主放心！"
        )
        keyboard = [
            [InlineKeyboardButton("🎁 去看許願池", callback_data="show_wishes")],
            [InlineKeyboardButton("⬅️ 回主選單", callback_data="back_main")]
        ]
        await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return

    # 許願清單
    elif data == "show_wishes":
        pending_wishes = [w for w in wishes_cache if w.get("status") == "待實現"]
        if not pending_wishes:
            text = "✨ 目前許願池空空如也，或是金主已經把所有願望全部實現了！"
            keyboard = [[InlineKeyboardButton("⬅️ 回主選單", callback_data="back_main")]]
            if query.message.photo:
                await query.message.delete()
                await context.bot.send_message(chat_id=query.message.chat_id, text=text, reply_markup=InlineKeyboardMarkup(keyboard))
            else:
                await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard))
            return

        text = "🎁 *【小寶貝的許願池】*\n點選願望查看詳情與照片："
        keyboard = []
        for w in pending_wishes:
            icon = "📸 " if w.get("photoId") else "✨ "
            keyboard.append([InlineKeyboardButton(f"{icon}{w['item']}", callback_data=f"view_wish_{w['id']}")])
        keyboard.append([InlineKeyboardButton("⬅️ 回主選單", callback_data="back_main")])

        if query.message.photo:
            await query.message.delete()
            await context.bot.send_message(chat_id=query.message.chat_id, text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        else:
            await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return

    # 檢視單個願望（含照片展示）
    elif data.startswith("view_wish_"):
        w_id = int(data.split("_")[2])
        w = next((x for x in wishes_cache if x["id"] == w_id), None)
        if not w or w.get("status") != "待實現":
            await query.edit_message_text("此願望已實現或不存在！", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ 回許願池", callback_data="show_wishes")]]))
            return

        caption = f"🎁 *願望詳情*\n━━━━━━━━━━━━━━━\n項目：*{w['item']}*\n━━━━━━━━━━━━━━━\n金主要幫忙實現嗎？"
        keyboard = [
            [InlineKeyboardButton("💖 幫妳實現", callback_data=f"fulfill_{w_id}")],
            [InlineKeyboardButton("⬅️ 返回許願池", callback_data="show_wishes")]
        ]

        if w.get("photoId"):
            # 有照片：刪除舊文字訊息，發出附帶照片的新卡片
            await query.message.delete()
            await context.bot.send_photo(
                chat_id=query.message.chat_id,
                photo=w["photoId"],
                caption=caption,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
        else:
            await query.edit_message_text(text=caption, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return

    # 認領/實現願望
    elif data.startswith("fulfill_"):
        w_id = int(data.split("_")[1])
        target_wish = next((w for w in wishes_cache if w["id"] == w_id), None)
        if target_wish and target_wish.get("status") == "待實現":
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
                await query.edit_message_caption(caption=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
            else:
                await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

            loop = asyncio.get_running_loop()
            loop.run_in_executor(None, sync_fulfill_wish, w_id)

            notify_msg = (
                f"🎊 *【願望達成通報！】*\n"
                f"金主 *{approver}* 剛剛認領了你的願望！\n"
                f"• 願望項目：{item}\n"
                f"太棒了～快去給他一個大擁抱吧！"
            )
            try:
                await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=notify_msg, parse_mode="Markdown")
            except Exception as e:
                print(f"通報發送失敗: {e}")
        return

    # 請款清單
    elif data == "list_bills":
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
        return

    # 單筆請款
    elif data.startswith("view_"):
        b_id = int(data.split("_")[1])
        b_info = local_cache.get(b_id)
        if not b_info or b_info.get("status") != "待審核":
            await query.edit_message_text("此請款單已處理完成或不存在！", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ 回主選單", callback_data="back_main")]]))
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
        return

    # 核銷
    elif data.startswith("approve_"):
        b_id = int(data.split("_")[1])
        if b_id in local_cache:
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

            loop = asyncio.get_running_loop()
            loop.run_in_executor(None, sync_update_bill_status, b_id, "已核銷")

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
        return

    # 駁回
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

            loop = asyncio.get_running_loop()
            loop.run_in_executor(None, sync_update_bill_status, b_id, "已駁回")

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
        return

async def health_check(request):
    return web.Response(text="Bot is running!")

async def post_init(application: Application):
    commands = [
        BotCommand("menu", "喚出主選單"),
        BotCommand("wish", "文字許願 (例: /wish 想要耳機)"),
        BotCommand("plan", "更新行程 (例: /plan 15:00 健身房)"),
        BotCommand("bill", "新增請款 (例: /bill 火鍋 800)"),
    ]
    await application.bot.set_my_commands(commands)

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, sync_get_all)

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
    app.add_handler(CommandHandler("wish", add_wish))
    app.add_handler(CommandHandler("plan", set_plan))
    app.add_handler(CommandHandler("bill", add_bill))
    
    # 支援直接傳照片許願
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo_wish))
    
    app.add_handler(CallbackQueryHandler(handle_callback))

    print("請款與秘書機器人運行中...")
    app.run_polling()

if __name__ == "__main__":
    main()
