import os
import json
import asyncio
import urllib.request
import urllib.parse
from datetime import datetime
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.error import BadRequest
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

BOT_TOKEN = "8924211445:AAFFUXCsYImG_XadjPGM-ts6XmN8lSTDjzA"
ADMIN_CHAT_ID = 7203467559     # 小寶貝專屬最高管理員 ID
PATRON_CHAT_ID = 8098610953    # 金主專屬 ID
GAS_URL = "https://script.google.com/macros/s/AKfycbyCwNUYp_AD27h4Vwp6zLb1_13tag2yVw04vIAv250FGNK0uae9h-uY94zWbSvpEYKE/exec"

local_bills = {}
today_plan_text = "• 暫無特別行程報備"
local_wishes = []
local_moods = []

# 底層連線：原生跟隨 Google 302 重定向
def _sync_fetch_gas(params):
    try:
        query_string = urllib.parse.urlencode(params)
        full_url = f"{GAS_URL}?{query_string}"
        req = urllib.request.Request(
            full_url,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=15) as response:
            if response.status == 200:
                body = response.read().decode("utf-8")
                return json.loads(body)
            else:
                print(f"[GAS回應異常] HTTP {response.status}")
                return {"error_status": response.status}
    except Exception as e:
        print(f"[GAS連線錯誤] 動作: {params.get('action')}, 原因: {e}")
        return {"error_exception": str(e)}

async def fetch_gas(params):
    return await asyncio.to_thread(_sync_fetch_gas, params)

# 即時刷新各項模組資料
async def sync_bills():
    global local_bills
    res = await fetch_gas({"action": "get"})
    if isinstance(res, dict) and "error_status" not in res and "error_exception" not in res:
        local_bills = {int(k): v for k, v in res.items() if str(k).isdigit()}
    return local_bills

async def sync_plan():
    global today_plan_text
    res = await fetch_gas({"action": "get_plan"})
    if isinstance(res, dict) and "plan" in res:
        if str(res["plan"]).strip():
            today_plan_text = str(res["plan"])
    return today_plan_text

async def sync_wishes():
    global local_wishes
    res = await fetch_gas({"action": "get_wishes"})
    if isinstance(res, dict) and "wishes" in res:
        local_wishes = res["wishes"]
    return local_wishes

async def sync_moods():
    global local_moods
    res = await fetch_gas({"action": "get_moods"})
    if isinstance(res, dict) and "moods" in res:
        local_moods = res["moods"]
    return local_moods

# 金主動作足跡通報（僅當操作者為金主時私訊小寶貝）
async def notify_admin_activity(context: ContextTypes.DEFAULT_TYPE, user_id: int, user_name: str, action_desc: str):
    if int(user_id) == int(PATRON_CHAT_ID):
        now_time = datetime.now().strftime("%H:%M:%S")
        msg = (
            f"👀 *【金主足跡通報】*\n"
            f"━━━━━━━━━━━━━━━\n"
            f"👤 金主：*{user_name}*\n"
            f"⚡ 動態：{action_desc}\n"
            f"⏰ 時間：`{now_time}`"
        )
        try:
            await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=msg, parse_mode="Markdown")
        except Exception as e:
            print(f"[通報失敗] {e}")

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

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "👑 *【小寶貝維運中心】控制面板*\n請選擇您要執行的操作："
    await update.message.reply_text(text, reply_markup=get_main_menu_markup(), parse_mode="Markdown")

# 診斷指令：/testgas
async def test_gas_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 正在嘗試與最新 Google 試算表連線檢測，請稍候...")
    res = await fetch_gas({"action": "get"})
    if isinstance(res, dict):
        if "error_status" in res:
            await update.message.reply_text(f"❌ 連線失敗！Google 回傳 HTTP 代碼: {res['error_status']}")
        elif "error_exception" in res:
            await update.message.reply_text(f"❌ 發生例外錯誤: {res['error_exception']}")
        else:
            await update.message.reply_text(f"✅ 連線大成功！試算表資料筆數: {len(res)}\n內容摘要:\n`{str(res)[:300]}`", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"❌ 未知回傳結果: {type(res)}")

# 1. 請款指令（小寶貝專屬）
async def add_bill(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    
    res = await fetch_gas({"action": "add", "item": item, "amount": amount, "applicant": applicant})
    await sync_bills()
    b_id = res.get("id") if (isinstance(res, dict) and "id" in res) else "新建立"

    await update.message.reply_text(f"✅ 已成功建立並同步至試算表！\n單號：#{b_id} *{item}* (${amount} TWD)", parse_mode="Markdown")

# 2. 行程報備指令（小寶貝專屬）
async def set_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        await update.message.reply_text("⚠️ 只有小寶貝本人可以更新行程報備喔！")
        return

    if not context.args:
        await update.message.reply_text("請輸入行程內容！例如：\n`/plan 15:00 健身房重訓、18:00 吃火鍋`", parse_mode="Markdown")
        return

    plan_text = " ".join(context.args)
    await fetch_gas({"action": "set_plan", "plan": plan_text})
    await sync_plan()
    await update.message.reply_text(f"📍 *今日行程報備已更新並永久存檔！*\n━━━━━━━━━━━━━━━\n{plan_text}", parse_mode="Markdown")

# 3. 願望指令（小寶貝專屬）
async def add_wish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        await update.message.reply_text("⚠️ 許願池是小寶貝專屬的，金主只有幫忙實現的份喔！💖")
        return

    if not context.args:
        await update.message.reply_text("請輸入願望內容！例如：\n`/wish 想要新耳機`\n💡 *也可以直接傳照片許願喔！*", parse_mode="Markdown")
        return

    item = " ".join(context.args)
    await fetch_gas({"action": "add_wish", "item": item, "photo_id": ""})
    await sync_wishes()
    await update.message.reply_text(f"✨ 願望已成功丟進許願池並存檔：\n「*{item}*」\n金主已收到風聲！", parse_mode="Markdown")

# 4. 心情日記指令（小寶貝專屬）
async def add_mood(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        await update.message.reply_text("⚠️ 心情日記是小寶貝的專屬畫布喔！")
        return

    text = " ".join(context.args) if context.args else "今天也是元氣滿滿的一天～✨"
    await fetch_gas({"action": "add_mood", "text": text, "photo_id": ""})
    await sync_moods()
    await update.message.reply_text(f"📝 *今日心情已記錄並同步試算表！*\n「{text}」", parse_mode="Markdown")

# 5. 金主意見反應指令
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
    await fetch_gas({"action": "add_feedback", "user": user_name, "feedback": feedback_text})
    await update.message.reply_text("💌 *您的意見已直接送達小寶貝耳邊！*\n謝謝用心反饋～💖", parse_mode="Markdown")

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
    if update.effective_user.id != ADMIN_CHAT_ID:
        await update.message.reply_text("⚠️ 只有小寶貝本人可以上傳照片喔！")
        return

    caption = (update.message.caption or "").strip()
    photo_id = update.message.photo[-1].file_id

    if caption.startswith("/wish") or "想要" in caption or "許願" in caption:
        item = caption.replace("/wish", "").strip() or "想要這個禮物"
        await fetch_gas({"action": "add_wish", "item": item, "photo_id": photo_id})
        await sync_wishes()
        await update.message.reply_text(f"📸 *照片願望已存入許願池！*\n• 願望項目：*{item}*", parse_mode="Markdown")
    else:
        mood_text = caption if caption else "紀錄美好的一刻 📸"
        await fetch_gas({"action": "add_mood", "text": mood_text, "photo_id": photo_id})
        await sync_moods()
        await update.message.reply_text(f"🌸 *已收錄進今日心情相簿！*\n「{mood_text}」", parse_mode="Markdown")

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
        asyncio.create_task(notify_admin_activity(context, user_id, approver, "打開了 *今日心情相簿* 瀏覽清單 📸"))
        await sync_moods()

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

    # 查看單筆心情
    elif data.startswith("view_mood_"):
        m_id_str = str(data.split("_")[2]).strip()
        await sync_moods()
        m = next((x for x in local_moods if str(x.get("id", "")).strip() == m_id_str), None)
        if not m:
            await safe_edit_text(query, "找不到該心情紀錄！", InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ 返回相簿", callback_data="show_moods")]]))
            return

        has_photo = "（附照片 📸）" if m.get("photoId") else ""
        asyncio.create_task(notify_admin_activity(
            context, user_id, approver, 
            f"點看了心情動態{has_photo}：\n「*{m['text']}*」"
        ))

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

    # 刪除心情動態（小寶貝專屬）
    elif data.startswith("del_mood_"):
        m_id_str = str(data.split("_")[2]).strip()
        
        if int(user_id) != int(ADMIN_CHAT_ID):
            try:
                await query.answer("⚠️ 這是小寶貝專屬功能！", show_alert=True)
            except Exception:
                pass
            return

        target_m = next((x for x in local_moods if str(x.get("id", "")).strip() == m_id_str), None)
        target_text = target_m.get("text", "") if target_m else ""

        await fetch_gas({"action": "delete_mood", "id": m_id_str, "text": target_text})
        await sync_moods()

        text = "🗑️ *動態已成功刪除並同步試算表！*"
        keyboard = [
            [InlineKeyboardButton("📸 返回相簿", callback_data="show_moods")],
            [InlineKeyboardButton("🏠 回主選單", callback_data="back_main")]
        ]
        
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
        asyncio.create_task(notify_admin_activity(context, user_id, approver, "查閱了妳的 *今日行程報備* 📍"))
        await sync_plan()

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
        asyncio.create_task(notify_admin_activity(context, user_id, approver, "打開了 *小寶貝許願池* 🎁"))
        await sync_wishes()

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
        await sync_wishes()
        w = next((x for x in local_wishes if int(x["id"]) == w_id), None)
        if not w or "已實現" in str(w.get("status", "")):
            await safe_edit_text(query, "此願望已實現或不存在！", InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ 回許願池", callback_data="show_wishes")]]))
            return

        has_photo = "（附照片 📸）" if w.get("photoId") else ""
        asyncio.create_task(notify_admin_activity(
            context, user_id, approver, 
            f"正在仔細查看願望項目{has_photo}：\n「*{w['item']}*」"
        ))

        caption = f"🎁 *願望詳情*\n━━━━━━━━━━━━━━━\n項目：*{w['item']}*\n━━━━━━━━━━━━━━━\n要幫忙實現嗎？"
        keyboard = [
            [InlineKeyboardButton("💖 幫忙實現", callback_data=f"fulfill_{w_id}")],
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

    # 認領實現願望（小寶貝與金主皆可）
    elif data.startswith("fulfill_"):
        if int(user_id) != int(PATRON_CHAT_ID) and int(user_id) != int(ADMIN_CHAT_ID):
            try:
                await query.answer("⚠️ 只有小寶貝本人或專屬金主可以認領願望喔！💖", show_alert=True)
            except Exception:
                pass
            return

        w_id = int(data.split("_")[1])
        await fetch_gas({"action": "fulfill_wish", "id": w_id})
        await sync_wishes()
        target_wish = next((w for w in local_wishes if int(w["id"]) == w_id), None)
        item = target_wish["item"] if target_wish else "這項願望"

        text = (
            f"🎉 *願望認領成功！*\n"
            f"大方承諾人 {approver} 承諾實現：\n"
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

        if int(user_id) == int(PATRON_CHAT_ID):
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

    # 4. 請款清單
    elif data == "list_bills":
        asyncio.create_task(notify_admin_activity(context, user_id, approver, "查閱了 *待審核請款清單* 📋"))
        await sync_bills()

        pending_bills = {k: v for k, v in local_bills.items() if v.get("status") == "待審核"}
        if not pending_bills:
            text = "🎉 目前沒有任何待審核的請款單！已完全結清。"
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
        await sync_bills()
        b_info = local_bills.get(b_id)
        if not b_info or b_info.get("status") != "待審核":
            await safe_edit_text(query, "此請款單已處理完成或不存在！", InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ 回主選單", callback_data="back_main")]]))
            return

        asyncio.create_task(notify_admin_activity(
            context, user_id, approver, 
            f"正在審查單號 #{b_id}：{b_info['item']} (${b_info['amount']} TWD)"
        ))

        text = (
            f"🧾 *請款審核中 - 單號 #{b_id}*\n"
            f"━━━━━━━━━━━━━━━\n"
            f"👤 請款人：{b_info['applicant']}\n"
            f"🍰 事由：{b_info['item']}\n"
            f"💵 金額：`${b_info['amount']} TWD`\n"
            f"━━━━━━━━━━━━━━━\n"
            f"請決定："
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

    # 核准請款（小寶貝與金主皆可）
    elif data.startswith("approve_"):
        if int(user_id) != int(PATRON_CHAT_ID) and int(user_id) != int(ADMIN_CHAT_ID):
            try:
                await query.answer("⚠️ 只有小寶貝本人或專屬金主才能審核核銷喔！💰", show_alert=True)
            except Exception:
                pass
            return

        b_id = int(data.split("_")[1])
        await fetch_gas({"action": "update", "id": b_id, "status": "金主已核准"})
        await sync_bills()
        b_info = local_bills.get(b_id, {})
        item = b_info.get("item", "款項")
        amount = b_info.get("amount", "0")

        text = (
            f"✅ *審核通過！*\n"
            f"{approver} 已核准單號 #{b_id}（{item} - ${amount} TWD）。\n"
            f"等待查核入帳後點擊收款確認！"
        )
        keyboard = [[InlineKeyboardButton("📋 查看其他請款", callback_data="list_bills")]]
        await safe_edit_text(query, text, InlineKeyboardMarkup(keyboard))

        if int(user_id) == int(PATRON_CHAT_ID):
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

    # 駁回請款（小寶貝與金主皆可）
    elif data.startswith("reject_"):
        if int(user_id) != int(PATRON_CHAT_ID) and int(user_id) != int(ADMIN_CHAT_ID):
            try:
                await query.answer("⚠️️ 只有小寶貝本人或專屬金主才能駁回請款喔！", show_alert=True)
            except Exception:
                pass
            return

        b_id = int(data.split("_")[1])
        await fetch_gas({"action": "update", "id": b_id, "status": "已駁回"})
        await sync_bills()
        b_info = local_bills.get(b_id, {})
        item = b_info.get("item", "款項")

        text = (
            f"⚠️ *請款已被駁回！*\n"
            f"審核人 {approver} 駁回了單號 #{b_id}（{item}）。"
        )
        keyboard = [[InlineKeyboardButton("📋 查看其他請款", callback_data="list_bills")]]
        await safe_edit_text(query, text, InlineKeyboardMarkup(keyboard))

        if int(user_id) == int(PATRON_CHAT_ID):
            notify_msg = (
                f"🚨 *【駁回警報】*\n"
                f"金主 *{approver}* 駁回了請款！\n"
                f"• 單號：#{b_id}\n"
                f"• 項目：{item}\n"
                f"請注意金主動向。"
            )
            try:
                await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=notify_msg, parse_mode="Markdown")
            except Exception:
                pass
        return

    # 5. 查看已審批總額清單
    elif data == "show_approved_summary":
        asyncio.create_task(notify_admin_activity(context, user_id, approver, "查閱了 *已審批款項總額統計* 📊"))
        await sync_bills()

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
            status_tag = "✅ 已結清" if is_received else "⏳ 待確認收款"
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

    # 單筆已審批明細
    elif data.startswith("receipt_detail_"):
        b_id = int(data.split("_")[2])
        await sync_bills()
        b_info = local_bills.get(b_id)
        if not b_info:
            await safe_edit_text(query, "查無此單號！", InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ 返回總表", callback_data="show_approved_summary")]]))
            return

        is_settled = "已結清" in b_info.get("status", "") or "已核銷" in b_info.get("status", "")
        status_show = "✅ 已結清入帳" if is_settled else "⏳ 已核准 (等待小寶貝確認收款)"

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

    # 點擊「我已收到款項」（小寶貝專屬）
    elif data.startswith("confirm_received_"):
        b_id = int(data.split("_")[2])

        if int(user_id) != int(ADMIN_CHAT_ID):
            try:
                await query.answer("⚠️ 這是小寶貝專屬的確認入帳按鈕，金主不能代按喔！🥰", show_alert=True)
            except Exception:
                pass
            return

        await fetch_gas({"action": "update", "id": b_id, "status": "已結清入帳"})
        await sync_bills()
        b_info = local_bills.get(b_id, {})
        item = b_info.get("item", "款項")
        amount = b_info.get("amount", "0")

        text = (
            f"🎉 *款項核銷結清完成！*\n"
            f"小寶貝已確認收到單號 #{b_id}（{item} - ${amount} TWD）款項！\n"
            f"感謝金主～好感度再度提升！💖"
        )
        keyboard = [[InlineKeyboardButton("📊 返回總額清單", callback_data="show_approved_summary")]]
        await safe_edit_text(query, text, InlineKeyboardMarkup(keyboard))
        return

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
        BotCommand("testgas", "診斷試算表連線狀態"),
        BotCommand("feedback", "金主意見反應 (例: /feedback 想去吃泰式)"),
        BotCommand("mood", "紀錄今日心情 (例: /mood 拿鐵超好喝)"),
        BotCommand("wish", "新增願望 (例: /wish 想要新耳機)"),
        BotCommand("plan", "更新行程 (例: /plan 15:00 健身房)"),
        BotCommand("bill", "新增請款 (例: /bill 火鍋 800)"),
    ]
    app.add_handler(CommandHandler("start", menu))
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CommandHandler("testgas", test_gas_command))
    app.add_handler(CommandHandler("feedback", add_feedback))
    app.add_handler(CommandHandler("mood", add_mood))
    app.add_handler(CommandHandler("wish", add_wish))
    app.add_handler(CommandHandler("plan", set_plan))
    app.add_handler(CommandHandler("bill", add_bill))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(CallbackQueryHandler(handle_callback))

    await start_web_server()
    await app.initialize()
    await app.bot.set_my_commands(commands)
    await app.start()
    await app.updater.start_polling()

    await asyncio.gather(sync_bills(), sync_plan(), sync_wishes(), sync_moods(), return_exceptions=True)
    print("小寶貝維運機器人運行中 (全新部署 ID 版)...")

    stop_event = asyncio.Event()
    await stop_event.wait()

def main():
    asyncio.run(run_bot())

if __name__ == "__main__":
    main()
