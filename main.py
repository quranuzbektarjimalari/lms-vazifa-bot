import nest_asyncio, asyncio, requests, pytz
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from datetime import datetime, time
GROUP_CHAT_ID = -1001899369217  # shu yerga o‘z guruh chat_id sini yozing


nest_asyncio.apply()

# === ⚙️ Sozlamalar ===
BOT_TOKEN = "8086716853:AAEKBw48xkLITfBQabZVt7iOzL_JaTBAVo8"
GLOBAL_EXECUTOR = ThreadPoolExecutor(max_workers=10)
TASHKENT_TZ = pytz.timezone("Asia/Tashkent")

# === Sana uchun yordamchi ===
def get_today_info():
    now = datetime.now(TASHKENT_TZ)
    weekdays_uz = ["Dushanba","Seshanba","Chorshanba","Payshanba","Juma","Shanba","Yakshanba"]
    return now.strftime("%d-%m-%Y"), weekdays_uz[now.weekday()]

# === 1. LMS tizimiga kirish ===
def login_to_lms(username, password):
    try:
        session = requests.Session()
        login_url = "https://lms.iiau.uz/auth/login"

        # 1️⃣ Sahifadan CSRF tokenni olamiz
        resp = session.get(login_url, timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")
        token_tag = soup.find("input", {"name": "_token"})
        token = token_tag["value"] if token_tag else ""

        # 2️⃣ Yuboriladigan ma'lumotlar
        payload = {
            "_token": token,
            "login": username,
            "password": password
        }

        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": login_url
        }

        # 3️⃣ POST so‘rov — tizimga kirish
        r = session.post(login_url, data=payload, headers=headers, timeout=10)

        # 4️⃣ Tekshirish
        if "logout" in r.text or "Chiqish" in r.text or "/auth/logout" in r.text:
            return session, "Foydalanuvchi", None
        else:
            return None, None, "Login yoki parol noto‘g‘ri."
    except Exception as e:
        return None, None, str(e)

SUBJECT_LINKS = {
    "826-27-uz": "Kalom ilmi tarixi va nazariyasi II",
    "827-27-uz": "Islom manbashunosligi",
    "828-27-uz": "Moturidiya ta’limotiga oid manbalar",
    "829-27-uz": "Tasavvuf II",
    "830-27-uz": "Islom falsafasi",
    "831-27-uz": "Arab tilining nazariy grammatikasi",
    "832-27-uz": "Mantiq ilmi asoslari"
}

def extract_subject_fast(soup):
    """
    Sahifadagi fan nomini aniqlash: 'Orqaga' tugmasidagi link orqali
    """
    try:
        # Orqaga tugmasini qidiramiz, faqat text bo'yicha
        back_link = None
        for a in soup.find_all("a", href=True):
            if "Orqaga" in a.get_text(strip=True):
                back_link = a
                break

        if back_link:
            href = back_link["href"]
            for key, name in SUBJECT_LINKS.items():
                if key in href:
                    return name
        return "❓ Fani aniqlanmadi"
    except Exception:
        return "❓ Fani aniqlanmadi"




# === 2. HEAD bilan mavjudlikni tekshirish (ishonchliroq) ===
def fast_check_exists(session, url, retries=3):
    for attempt in range(retries):
        try:
            r = session.head(url, timeout=7)
            if r.status_code == 200:
                return True
            else:
                # HEAD ishlamasa GET
                r = session.get(url, timeout=7)
                if r.status_code == 200:
                    return True
        except Exception as e:
            
            if attempt == retries - 1:
                return False
            continue
    return False



# === 4 va 5: check_test va check_assignment exceptionlarni loglash ===
def check_test(session, url, retries=3):
    for attempt in range(retries):
        try:
            if not fast_check_exists(session, url):
                return None
            r = session.get(url, timeout=10)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")

            title_tag = soup.find("h3", class_="page-title")
            title = title_tag.get_text(strip=True) if title_tag else "Noma’lum test"

            strong = soup.find("strong", string=lambda s: s and "Tugallanish vaqti" in s)
            deadline = "-"
            if strong:
                span = strong.find_next("span", class_="text-primary")
                if span:
                    deadline = span.get_text(strip=True)

            subject = extract_subject_fast(soup)
            return (title, subject, deadline, url)
        except Exception as e:
            
            if attempt == retries - 1:
                return None
            continue

# === check_assignment + retry ===
def check_assignment(session, url, retries=3):
    for attempt in range(retries):
        try:
            if not fast_check_exists(session, url):
                return None
            r = session.get(url, timeout=10)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")

            title = "Noma’lum topshiriq"
            for p in soup.find_all("p", class_="header-title"):
                span = p.find("span")
                if span and "Topshiriq nomi" in span.get_text(strip=True):
                    title = p.get_text(" ", strip=True).replace("Topshiriq nomi:", "").strip()

            deadline = "-"
            for p in soup.find_all("p", class_="header-title"):
                span = p.find("span")
                if span and "Topshiriq muddati" in span.get_text(strip=True):
                    deadline = p.get_text(" ", strip=True).replace("Topshiriq muddati", "").strip()

            subject = extract_subject_fast(soup)
            return (title, subject, deadline, url)
        except Exception as e:
            
            if attempt == retries - 1:
                return None
            continue



# === 6. Bugungi sana bilan solishtirish ===
def is_today(deadline_str):
    try:
        # Har xil belgilardan tozalaymiz
        s = deadline_str.strip().replace(".", "-").replace("–", "-").replace("—", "-")
        parts = s.split()
        if len(parts) < 2:
            return False

        date_part, time_part = parts[0], parts[1]

        # Sana formatini aniqlaymiz
        for fmt in ["%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M"]:
            try:
                dt = datetime.strptime(s, fmt)
                return dt.date() == datetime.now(TASHKENT_TZ).date()
            except:
                continue
        return False
    except:
        return False



# === 7. Bugungi testlarni topish ===
def find_today_tests(session, start_id=1004, end_id=1304):
    base_url = "https://lms.iiau.uz/student/my-course/calendar/resource/test/"
    results = []
    urls = [f"{base_url}{i}" for i in range(start_id, end_id + 1)]
    futures = [GLOBAL_EXECUTOR.submit(check_test, session, url) for url in urls]
    for fut in as_completed(futures):
        res = fut.result()
        if res and is_today(res[2]):
            results.append(res)
    return results


# === 8. Bugungi topshiriqlarni topish ===
def find_today_assignments(session, start_id=6343, end_id=6643):
    base_url = "https://lms.iiau.uz/student/my-course/calendar/resource/activity/standard-"
    results = []
    urls = [f"{base_url}{i}" for i in range(start_id, end_id + 1)]
    futures = [GLOBAL_EXECUTOR.submit(check_assignment, session, url) for url in urls]
    for fut in as_completed(futures):
        res = fut.result()
        if res and is_today(res[2]):
            results.append(res)
    return results


# === 9. Telegram xabar yuborish ===
async def send_today_deadlines(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    # 🔹 1️⃣ Vaqtinchalik xabar yuborish
    temp_msg = await context.bot.send_message(chat_id=chat.id, text="🙋‍♂️ Bugungi deadlinelar tekshirilmoqda..."
    )
    
    # 👇 Bu joyda o‘z login-parolingizni kiriting
    session, _, err = login_to_lms("user2200420", "70386881")
    if not session:
        await context.bot.send_message(chat_id=chat.id, text=f"❌ LMS ga kirishda xato: {err}")
        return

    tests = find_today_tests(session)
    assignments = find_today_assignments(session)
    # 🔹 Temp xabarni o'chirish
    await temp_msg.delete()

    if not tests and not assignments:
        now = datetime.now(TASHKENT_TZ)
        weekdays_uz = ["Dushanba","Seshanba","Chorshanba","Payshanba","Juma","Shanba","Yakshanba"]
        bugungi_sana = now.strftime("%d-%m-%Y")
        bugungi_kun = weekdays_uz[now.weekday()]

        await context.bot.send_message(
            chat_id=chat.id, 
            text=f"✅ Bugun tugaydigan test yoki topshiriq yo‘q! \n({bugungi_sana}, {bugungi_kun})"
            )

        return

    msg = f"❗️ *Bugun quyidagi vazifalar vaqti tugaydi*:\n\n"

    if tests:
        
        for title, subject, deadline, link in tests:
            msg += f"📘 *Test:* *{title}* ([ko‘rish]({link}))\n🕒 Tugash: {deadline}\n👉 {subject}\n\n"

    if assignments:
       
        for title, subject, deadline, link in assignments:
            msg += f"📕 *Topshiriq:* *{title}* ([ko‘rish]({link}))\n🕒 Tugash: {deadline}\n👉 {subject}\n\n"

    await context.bot.send_message(chat_id=chat.id, text=msg, parse_mode="Markdown", disable_web_page_preview=True)


# === Har kuni 05:00 da avtomatik yuborish ===
async def auto_send_daily(context: ContextTypes.DEFAULT_TYPE):
    # Shu yerda yangiliklarni avtomatik tekshiradi va yuboradi
    class DummyUpdate:
        effective_chat = type("Chat", (), {"id": GROUP_CHAT_ID})()
    await send_today_deadlines(DummyUpdate(), context)


# === Botni ishga tushirish ===
async def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    job_queue = app.job_queue

    # /bugun komandasi uchun handler
    app.add_handler(CommandHandler("bugun", send_today_deadlines))

    # Har kuni 05:00 da avtomatik yuborish (sinov uchun vaqtni o‘zgartir)
    job_queue.run_daily(auto_send_daily, time=time(hour=5, minute=0, tzinfo=TASHKENT_TZ))

    print("✅ Bot ishga tushdi. /bugun komandasi ishlaydi va har kuni 05:00 da avtomatik xabar yuboradi.")
    await app.run_polling()



if __name__ == "__main__":
    asyncio.run(main())
