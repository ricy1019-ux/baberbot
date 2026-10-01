import os
import asyncio
import aiohttp
from datetime import datetime
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
ADMIN_CHAT_ID = 7203467559  # 專屬 ID
GAS_URL = "https://script.google.com/macros/s/AKfycbxdhzx6EWM5TYGOMJm0AMJpI6SUwmWyegeAMDQ-nt6JRORbsMV5VsaLSm75LRGo916H/exec"

# 本機記憶體快取
local_bills = {}
today_plan_text = "• 載入中，請稍候..."
local_wishes = []
local_moods = []
wish_counter = 0

# 背景通訊：加入 allow_redirects=True 跟隨 Google 302 轉址
async def fetch_gas(params):
    try:
        timeout = aiohttp.ClientTimeout(total=10.0)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(GAS_URL, params=params, allow_redirects=True) as resp:
                if resp.status == 200:
                    return await resp.json(content_type=None)
                else:
                    print(f"[GAS回應異常] HTTP {resp.status}")
    except Exception as e:
        print(f"[GAS連線提示] {e}")
    return None

async def bg_sync_all():
    global local_bills, today_plan_text, local_wishes, local_moods, wish_counter
    # 1. 抓請款
    res_bills = await fetch_gas({"action": "get"})
    if isinstance(res_bills, dict):
        local_bills = {int(k): v for k, v in res_bills.items() if str(k).isdigit()}

    # 2. 抓行程
    res_plan = await fetch_gas({"action": "get_plan"})
    if isinstance(res_plan, dict) and "plan" in res_plan:
        if str(res_plan["plan"]).strip():
            today_plan_text = str(res_plan["plan"])

    # 3. 抓願望
    res_wishes = await fetch_gas({"action": "get_wishes"})
    if isinstance(res_wishes, dict) and "wishes" in res_wishes:
        local_wishes = res_wishes["wishes"]
        if local_wishes:
            wish_counter = max([int(w["id"]) for w in local_wishes if str(w["id"]).isdigit()], default=0)

    # 4. 抓心情相簿
    res_moods = await fetch_gas({"action": "get_moods"})
    if isinstance(res_moods, dict) and "moods" in res_moods:
        local_moods = res_moods["moods"]

def get_main_menu_markup():
    keyboard = [
        [InlineKeyboardButton("📸 小寶貝今日心情相簿", callback_data="show_moods")],
        [InlineKeyboardButton("📍 查看今日行程報備", callback_data="show_plan")],
        [InlineKeyboardButton("🎁 查看小寶貝許願池", callback_data="show_wishes")],
        [InlineKeyboardButton("📋 查看待審請款單", callback_data="list_bills")],
        [InlineKeyboardButton("📊 查看已審批總額 (含收款確認)", callback_data="show_approved_summary")],
        [InlineKeyboardButton("💬 金主意見反應箱", callback_data="show_feedback_guide")]
    ]
    return InlineKeyboardMarkup(keyboard)

async def safe_edit_text(query, text, reply_markup=None):
    try:
        await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode="Markdown")
    except BadRequest as e:
        if "Message is not modified" in str(e):
            pass
        elif "There is no text in the message to edit" in str(e):
            try:
                await query.message.delete()
            except Exception:
                pass
            await query.message.reply_text(text=text, reply_markup=reply_markup, parse_mode="Markdown")
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

# 3. 願望指令（僅限本人）：/wish 內容
async def add_wish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global local_wishes, wish_counter
    if update.effective_user.id != ADMIN_CHAT_ID:
        await update.message.reply_text("⚠️ 許願池是小寶貝專屬的，金主只有幫忙實現的份喔！💖")
        return

    if not context.args:
        await update.message.reply_text("請輸入願望內容！例如：\n`/wish 想要新耳機`\n💡 *也可以直接傳照片許願喔！*", parse_mode="Markdown")
        return

    item = " ".join(context.args)
    wish_counter += 1
    local_wishes.append({"id": wish_counter, "item": item, "photoId": "", "status": "待實現"})
    await update.message.reply_text(f"✨ 願望已成功丟進許願池：\n「*{item}*」\n金主已收到風聲！", parse_mode="Markdown")
    asyncio.create_task(fetch_gas({"action": "add_wish", "item": item, "photo_id": ""}))

# 4. 心情日記指令：/mood 文字
async def add_mood(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global local_moods
    if update.effective_user.id != ADMIN_CHAT_ID:
        await update.message.reply_text("⚠️ 心情日記是小寶貝的專屬畫布喔！")
        return

    text = " ".join(context.args) if context.args else "今天也是元氣滿滿的一天～✨"
    time_str = datetime.now().strftime("%H:%M")
    m_id = str(len(local_moods) + 1)
    local_moods.append({"id": m_id, "text": text, "photoId": "", "time": time_str})
    await update.message.reply_text(f"📝 *今日心情已記錄！*\n「{text}」", parse_mode="Markdown")
    asyncio.create_task(fetch_gas({"action": "add_mood", "text": text, "photo_id": ""}))

# 5. 金主意見反應指令：/feedback 意見
async def add_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name = update.effective_user.first_name or "貼心金主"
    if not context.args:
        await update.message.reply_text(
            "💌 *金主意見反應格式：*\n"
            "`/feedback 您想說的話或建議`\n\n"
            "例如：\n"
            "`/feedback 下週末想帶妳去吃泰式料理`",
            parse_mode="Markdown"
        )
        return

    feedback_text = " ".join(context.args)
    await update.message.reply_text("💌 *您的意見已直接送達小寶貝耳邊！*\n謝謝金主的用心反饋～💖", parse_mode="Markdown")

    asyncio.create_task(fetch_gas({
        "action": "add_feedback",
        "user": user_name,
        "feedback": feedback_text
    }))

    notify_text = (
        f"📢 *【金主意見即時來信！】*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"👤 來自：*{user_name}*\n"
        f"💬 內容：\n「{feedback_text}」\n"
        f"━━━━━━━━━━━━━━━\n"
        f"時間：{datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )
    try:
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=notify_text, parse_mode="Markdown")
    except Exception as e:
        print(f"[通報失敗] {e}")

# 照片訊息處理
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global local_wishes, wish_counter, local_moods
    if update.effective_user.id != ADMIN_CHAT_ID:
        await update.message.reply_text("⚠️ 只有小寶貝本人可以上傳照片喔！")
        return

    caption = (update.message.caption or "").strip()
    photo_id = update.message.photo[-1].file_id

    if caption.startswith("/wish") or "想要" in caption or "許願" in caption:
        item = caption.replace("/wish", "").strip() or "想要這個禮物"
        wish_counter += 1
        local_wishes.append({"id": wish_counter, "item": item, "photoId": photo_id, "status": "待實現"})
        await update.message.reply_text(f"📸 *照片願望已存入許願池！*\n• 願望項目：*{item}*", parse_mode="Markdown")
        asyncio.create_task(fetch_gas({"action": "add_wish", "item": item, "photo_id": photo_id}))
    else:
        mood_text = caption if caption else "紀錄美好的一刻 📸"
        time_str = datetime.now().strftime("%H:%M")
        m_id = str(len(local_moods) + 1)
        local_moods.append({"id": m_id, "text": mood_text, "photoId": photo_id, "time": time_str})
        await update.message.reply_text(f"🌸 *已收錄進今日心情相簿！*\n「{mood_text}」", parse_mode="Markdown")
        asyncio.create_task(fetch_gas({"action": "add_mood", "text": mood_text, "photo_id": photo_id}))

# 按鈕回調處理
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global local_bills, today_plan_text, local_wishes, local_moods
    query = update.callback_query
    data = query.data
    user_id = query.from_user.id
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

    # 1. 小寶貝今日心情相簿
    elif data == "show_moods":
        res_moods = await fetch_gas({"action": "get_moods"})
        if isinstance(res_moods, dict) and "moods" in res_moods:
            local_moods = res_moods["moods"]

        if not local_moods:
            text = "🌸 *【小寶貝今日心情相簿】*\n今天還沒有發布日常動態喔！\n小寶貝只要直接傳送照片就會自動收錄進來～"
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

        text = "🌸 *【小寶貝今日心情相簿】*\n點擊查看精彩瞬間："
        keyboard = []
        for m in local_moods:
            icon = "📷 " if m.get("photoId") else "💭 "
            title = f"{icon}{str(m.get('text', ''))[:15]}"
            keyboard.append([InlineKeyboardButton(title, callback_data=f"view_mood_{m['id']}")])
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

    # 查看單筆心情（含本人專屬刪除鈕）
    elif data.startswith("view_mood_"):
        m_id_str = str(data.split("_")[2]).strip()
        m = next((x for x in local_moods if str(x.get("id", "")).strip() == m_id_str), None)
        if not m:
            await safe_edit_text(query, "找不到該心情紀錄！", InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ 返回相簿", callback_data="show_moods")]]))
            return

        caption = f"🌸 *今日心情日常*\n━━━━━━━━━━━━━━━\n「{m['text']}」\n━━━━━━━━━━━━━━━\n時間：{m.get('time', '剛剛')}"
        keyboard = []
        if int(user_id) == int(ADMIN_CHAT_ID):
            keyboard.append([InlineKeyboardButton("🗑️ 【小寶貝專屬】刪除此動態", callback_data=f"del_mood_{m_id_str}")])
            
        keyboard.append([InlineKeyboardButton("📸 看其他動態", callback_data="show_moods")])
        keyboard.append([InlineKeyboardButton("🏠 回主選單", callback_data="back_main")])

        if m.get("photoId"):
            try:
                await query.message.delete()
            except Exception:
                pass
            await context.bot.send_photo(
                chat_id=query.message.chat_id,
                photo=m["photoId"],
                caption=caption,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
        else:
            await safe_edit_text(query, caption, InlineKeyboardMarkup(keyboard))
        return

    # 執行刪除心情（雙重比對 + 同步等待 GAS 回應）
    elif data.startswith("del_mood_"):
        m_id_str = str(data.split("_")[2]).strip()
        
        # 1. 權限檢查
        if int(user_id) != int(ADMIN_CHAT_ID):
            try:
                await query.answer(f"⚠️ 這是小寶貝專屬功能！\n你的ID: {user_id}\n後台ID: {ADMIN_CHAT_ID}", show_alert=True)
            except Exception:
                pass
            return

        try:
            await query.answer("正在刪除動態，請稍候...", show_alert=False)
        except Exception:
            pass

        # 找出該筆心情文字作為備用比對依據
        target_m = next((x for x in local_moods if str(x.get("id", "")).strip() == m_id_str), None)
        target_text = target_m.get("text", "") if target_m else ""

        # 2. 本地快取立即移除
        local_moods = [m for m in local_moods if str(m.get("id", "")).strip() != m_id_str]

        # 3. 同步送出給 GAS 並等待回應（同時附帶 ID 與 文字）
        res = await fetch_gas({"action": "delete_mood", "id": m_id_str, "text": target_text})
        print(f"[刪除除錯回應] 刪除ID {m_id_str}, 文字 {target_text}, 結果: {res}")

        text = "🗑️ *動態已成功刪除！*"
        keyboard = [
            [InlineKeyboardButton("📸 返回相簿", callback_data="show_moods")],
            [InlineKeyboardButton("🏠 回主選單", callback_data="back_main")]
        ]
        
        # 4. 畫面安全更新（防範照片訊息例外）
        try:
            if query.message.photo:
                await query.message.delete()
                await context.bot.send_message(chat_id=query.message.chat_id, text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
            else:
                await safe_edit_text(query, text, InlineKeyboardMarkup(keyboard))
        except Exception:
            await context.bot.send_message(chat_id=query.message.chat_id, text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return

    # 2. 查看今日行程
    elif data == "show_plan":
        res_plan = await fetch_gas({"action": "get_plan"})
        if isinstance(res_plan, dict) and "plan" in res_plan and str(res_plan["plan"]).strip():
            today_plan_text = str(res_plan["plan"])

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

    # 3. 查看許願池清單
    elif data == "show_wishes":
        res_wishes = await fetch_gas({"action": "get_wishes"})
        if isinstance(res_wishes, dict) and "wishes" in res_wishes:
            local_wishes = res_wishes["wishes"]

        pending_wishes = [
            w for w in local_wishes 
            if "已實現" not in str(w.get("status", "")).strip() and str(w.get("item", "")).strip() != ""
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
            btn_title = f"{icon}{w['item']}"
            keyboard.append([InlineKeyboardButton(btn_title, callback_data=f"view_wish_{w['id']}")])
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

    # 4. 請款清單（待審核）
    elif data == "list_bills":
        res_bills = await fetch_gas({"action": "get"})
        if isinstance(res_bills, dict):
            local_bills = {int(k): v for k, v in res_bills.items() if str(k).isdigit()}

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

    # 金主核准
    elif data.startswith("approve_"):
        b_id = int(data.split("_")[1])
        if b_id in local_bills:
            local_bills[b_id]["status"] = "金主已核准"
            item = local_bills[b_id]["item"]
            amount = local_bills[b_id]["amount"]

            text = (
                f"✅ *金主已核准！*\n"
                f"金主 {approver} 已核准單號 #{b_id}（{item} - ${amount} TWD）。\n"
                f"等待小寶貝查核入帳後點擊收款確認！"
            )
            keyboard = [[InlineKeyboardButton("📋 查看其他請款", callback_data="list_bills")]]
            await safe_edit_text(query, text, InlineKeyboardMarkup(keyboard))

            asyncio.create_task(fetch_gas({"action": "update", "id": b_id, "status": "金主已核准"}))

            notify_msg = (
                f"🔔 *【金主核准通報】*\n"
                f"金主 *{approver}* 剛剛核准了請款！\n"
                f"• 單號：#{b_id}\n"
                f"• 項目：{item}\n"
                f"• 金額：${amount} TWD\n"
                f"收到轉帳後，可至「📊 查看已審批總額」確認已收到款項喔！"
            )
            try:
                await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=notify_msg, parse_mode="Markdown")
            except Exception:
                pass
        return

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

    # 5. 查看已審批總額清單
    elif data == "show_approved_summary":
        res_bills = await fetch_gas({"action": "get"})
        if isinstance(res_bills, dict):
            local_bills = {int(k): v for k, v in res_bills.items() if str(k).isdigit()}

        approved_bills = {
            k: v for k, v in local_bills.items() 
            if any(st in v.get("status", "") for st in ["已核銷", "金主已核准", "已入帳", "已結清"])
        }
        
        total_amount = 0
        keyboard = []
        for b_id, b_info in approved_bills.items():
            try:
                amt = float(str(b_info.get("amount", 0)).replace(",", ""))
            except ValueError:
                amt = 0.0
            total_amount += amt
            
            is_received = "已結清" in b_info.get("status", "") or "已核銷" in b_info.get("status", "")
            status_tag = "✅ 已結清" if is_received else "⏳ 待小寶貝確認收款"
            btn_text = f"#{b_id} {b_info['item']} (${int(amt)}) [{status_tag}]"
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"receipt_detail_{b_id}")])

        keyboard.append([InlineKeyboardButton("⬅️ 回主選單", callback_data="back_main")])

        if not approved_bills:
            text = (
                "📊 *【已審批請款統計】*\n"
                "━━━━━━━━━━━━━━━\n"
                "目前尚無任何審核通過的請款紀錄。\n"
                "━━━━━━━━━━━━━━━"
            )
        else:
            text = (
                "📊 *【已審批請款總額總覽】*\n"
                "━━━━━━━━━━━━━━━\n"
                f"💰 *累計審批總額*：`${int(total_amount) if total_amount.is_integer() else total_amount:,} TWD`\n"
                f"📦 *核准總筆數*：{len(approved_bills)} 筆\n"
                "━━━━━━━━━━━━━━━\n"
                "👇 *點擊下方項目查看明細與進行收款核銷：*"
            )

        await safe_edit_text(query, text, InlineKeyboardMarkup(keyboard))
        return

    # 單筆已審批明細（包含本人專屬收款確認鈕）
    elif data.startswith("receipt_detail_"):
        b_id = int(data.split("_")[2])
        b_info = local_bills.get(b_id)
        if not b_info:
            await safe_edit_text(query, "查無此單號！", InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ 返回總表", callback_data="show_approved_summary")]]))
            return

        is_settled = "已結清" in b_info.get("status", "") or "已核銷" in b_info.get("status", "")
        status_show = "✅ 已結清入帳" if is_settled else "⏳ 金主已核准 (等待小寶貝確認收到)"

        text = (
            f"🧾 *已審批款項明細 - 單號 #{b_id}*\n"
            f"━━━━━━━━━━━━━━━\n"
            f"• 項目：*{b_info['item']}*\n"
            f"• 金額：`${b_info['amount']} TWD`\n"
            f"• 目前狀態：*{status_show}*\n"
            f"━━━━━━━━━━━━━━━"
        )

        keyboard = []
        if not is_settled:
            keyboard.append([InlineKeyboardButton("💸 【小寶貝專屬】我已收到款項！", callback_data=f"confirm_received_{b_id}")])
        keyboard.append([InlineKeyboardButton("⬅️ 返回總額清單", callback_data="show_approved_summary")])

        await safe_edit_text(query, text, InlineKeyboardMarkup(keyboard))
        return

    # 點擊「我已收到款項」：權限判定僅限本人
    elif data.startswith("confirm_received_"):
        b_id = int(data.split("_")[2])

        if int(user_id) != int(ADMIN_CHAT_ID):
            try:
                await query.answer("⚠️ 這是小寶貝專屬的確認入帳按鈕，金主不能代按喔！🥰", show_alert=True)
            except Exception:
                pass
            return

        if b_id in local_bills:
            local_bills[b_id]["status"] = "已結清入帳"
            item = local_bills[b_id]["item"]
            amount = local_bills[b_id]["amount"]

            text = (
                f"🎉 *款項核銷結清完成！*\n"
                f"小寶貝已確認收到單號 #{b_id}（{item} - ${amount} TWD）款項！\n"
                f"感謝金主～好感度再度提升！💖"
            )
            keyboard = [[InlineKeyboardButton("📊 返回總額清單", callback_data="show_approved_summary")]]
            await safe_edit_text(query, text, InlineKeyboardMarkup(keyboard))

            asyncio.create_task(fetch_gas({"action": "update", "id": b_id, "status": "已結清入帳"}))
        return

    # 6. 金主意見反應指引
    elif data == "show_feedback_guide":
        text = (
            "💬 *【金主意見反應箱】*\n"
            "━━━━━━━━━━━━━━━\n"
            "金主對小寶貝有任何期許、想吃的餐廳或建議，都可以隨時告訴我！\n\n"
            "📝 *使用方式：*\n"
            "在對話框輸入：\n"
            "`/feedback 您想說的真心話`\n\n"
            "💡 *範例：*\n"
            "`/feedback 下週末想帶妳去吃日本料理`\n"
            "━━━━━━━━━━━━━━━\n"
            "（發送後小寶貝會立刻收到手機即時推播通知喔！）"
        )
        keyboard = [[InlineKeyboardButton("⬅️ 回主選單", callback_data="back_main")]]
        await safe_edit_text(query, text, InlineKeyboardMarkup(keyboard))
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
        BotCommand("feedback", "金主意見反應 (例: /feedback 想去吃泰式)"),
        BotCommand("mood", "紀錄今日心情 (例: /mood 拿鐵超好喝)"),
        BotCommand("wish", "新增願望 (例: /wish 想要新耳機)"),
        BotCommand("plan", "更新行程 (例: /plan 15:00 健身房)"),
        BotCommand("bill", "新增請款 (例: /bill 火鍋 800)"),
    ]
    app.add_handler(CommandHandler("start", menu))
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CommandHandler("feedback", add_feedback))
    app.add_handler(CommandHandler("mood", add_mood))
    app.add_handler(CommandHandler("wish", add_wish))
    app.add_handler(CommandHandler("plan", set_plan))
    app.add_handler(CommandHandler("bill", add_bill))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(CallbackQueryHandler(handle_callback))

    # 1. 啟動 Web 服務供 Render 存活偵測
    await start_web_server()

    # 2. 初始化並啟動 Telegram 機器人
    await app.initialize()
    await app.bot.set_my_commands(commands)
    await app.start()
    await app.updater.start_polling()

    # 3. 開機同步試算表歷史紀錄
    asyncio.create_task(bg_sync_all())

    print("小寶貝維運機器人運行中...")

    stop_event = asyncio.Event()
    await stop_event.wait()

def main():
    asyncio.run(run_bot())

if __name__ == "__main__":
    main()
