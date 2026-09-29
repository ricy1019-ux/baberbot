import os
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

BOT_TOKEN = "8924211445:AAFFUXCsYImG_XadjPGM-ts6XmN8lSTDjzA"

# 暫存請款資料
bills_db = {
    1: {"item": "週五豪華晚餐", "amount": 1280, "applicant": "Lisa", "status": "待審核"},
    2: {"item": "下午茶珍奶全糖", "amount": 75, "applicant": "Lisa", "status": "待審核"}
}
bill_counter = 2

async def add_bill(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global bill_counter
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("格式錯誤！請輸入：`/bill 項目 金額`\n例如：`/bill 火鍋 800`", parse_mode="Markdown")
        return

    item = " ".join(args[:-1])
    amount = args[-1]
    applicant = update.effective_user.first_name

    bill_counter += 1
    bills_db[bill_counter] = {
        "item": item,
        "amount": amount,
        "applicant": applicant,
        "status": "待審核"
    }
    await update.message.reply_text(f"✅ 已成功建立請款項目：*{item}* (${amount} TWD)", parse_mode="Markdown")

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "👑 *【小寶貝維運中心】控制面板*\n請選擇您要執行的操作："
    keyboard = [
        [InlineKeyboardButton("✨ 小寶貝今日活動", callback_data="show_perks")],
        [InlineKeyboardButton("📋 查看待審請款單", callback_data="list_bills")]
    ]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        pending_bills = {k: v for k, v in bills_db.items() if v["status"] == "待審核"}
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
        b_info = bills_db.get(b_id)
        if not b_info or b_info["status"] != "待審核":
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
        if b_id in bills_db:
            bills_db[b_id]["status"] = "已核銷"
            item = bills_db[b_id]["item"]
            amount = bills_db[b_id]["amount"]
            text = (
                f"✅ *核銷成功！*\n"
                f"金主 {approver} 已核准單號 #{b_id}（{item} - ${amount} TWD）。\n"
                f"謝謝老闆！"
            )
            keyboard = [[InlineKeyboardButton("📋 查看其他請款", callback_data="list_bills")]]
            await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("reject_"):
        b_id = int(data.split("_")[1])
        if b_id in bills_db:
            bills_db[b_id]["status"] = "已駁回"
            item = bills_db[b_id]["item"]
            text = (
                f"⚠️ *請款已被駁回！*\n"
                f"審核人 {approver} 駁回了單號 #{b_id}（{item}）。\n"
                f"請老闆自重。"
            )
            keyboard = [[InlineKeyboardButton("📋 查看其他請款", callback_data="list_bills")]]
            await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data == "back_main":
        text = "👑 *【小寶貝維運中心】控制面板*\n請選擇您要執行的操作："
        keyboard = [
            [InlineKeyboardButton("✨ 小寶貝今日活動", callback_data="show_perks")],
            [InlineKeyboardButton("📋 查看待審請款單", callback_data="list_bills")]
        ]
        await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

# 提供給 Render 的心跳首頁
async def health_check(request):
    return web.Response(text="Bot is running!")

async def post_init(application: Application):
    server = web.Application()
    server.router.add_get("/", health_check)
    runner = web.AppRunner(server)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

def main():
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CommandHandler("bill", add_bill))
    app.add_handler(CallbackQueryHandler(handle_callback))

    print("請款機器人運行中...")
    app.run_polling()

if __name__ == "__main__":
    main()
