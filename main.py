import asyncio, aiohttp, re, sqlite3, logging
from bs4 import BeautifulSoup
from aiohttp import ClientSession, ClientError
from config import webhook_url, print_status_changes, apps, ping_role

testflight_url = 'https://testflight.apple.com/join/'
app_name_pattern = r'Join the (.+) beta - TestFlight - Apple'
open_text = r'To join the (.+) beta, open the link on your iPhone, iPad, or Mac after you install TestFlight.'
full_text = 'This beta is full.'
closed_text = 'This beta isn\'t accepting any new testers right now.'

conn = sqlite3.connect('testflight.db')
cursor = conn.cursor()
cursor.execute('''
    CREATE TABLE IF NOT EXISTS status_changes (
        id TEXT PRIMARY KEY,
        status TEXT
    )
''')
conn.commit()

if len(apps) > 0:
    print(f'Online and watching {len(apps)} apps\nPrinting updates: {"True" if print_status_changes else "False"}\n')
else:
    print('No apps to watch. Exiting...')
    exit()

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

async def fetch_data(app_id, session):
    url = testflight_url + app_id
    try:
        async with session.get(url, headers={"Accept-Language": "en-us"}, timeout=10) as response:
            response.raise_for_status()
            return await response.text(encoding='utf-8')
    except (ClientError, asyncio.TimeoutError) as e:
        log_level = logging.WARNING if isinstance(e, asyncio.TimeoutError) or getattr(e, 'status', 0) == 429 else logging.ERROR
        error_type = ("Timeout" if isinstance(e, asyncio.TimeoutError) else 
                      "Rate limited" if getattr(e, 'status', 0) == 429 else 
                      "Error")
        log_message = f"{error_type} fetching {url}. {str(e)}"
        logging.log(log_level, log_message)
        await asyncio.sleep(300 if getattr(e, 'status', 0) == 429 else 120)
        return None

async def send_discord_webhook(message):
    async with ClientSession() as session:
        payload = {'content': message}
        try:
            async with session.post(webhook_url, json=payload) as response:
                response.raise_for_status()
        except ClientError as e:
            logging.error(f"Failed to send webhook. Error: {str(e)}")

async def track_status(id, new_status):
    cursor.execute('SELECT status FROM status_changes WHERE id = ?', (id,))
    previous_status = cursor.fetchone()

    if previous_status is None:
        cursor.execute('INSERT INTO status_changes (id, status) VALUES (?, ?)', (id, new_status))
        conn.commit()
        return(new_status)

    if previous_status is None or previous_status[0] != new_status:
        cursor.execute('INSERT OR REPLACE INTO status_changes (id, status) VALUES (?, ?)', (id, new_status))
        conn.commit()

        if previous_status is not None:
            return(new_status)
    
    else:
        return None

async def process_apps():
    async with ClientSession() as session:
        while True:
            tasks = [fetch_data(app_id, session) for app_id in apps]
            results = await asyncio.gather(*tasks)

            for app_id, result in zip(apps, results):
                if result is None:
                    continue

                soup = BeautifulSoup(result, 'html.parser')

                app_name = re.search(app_name_pattern, soup.title.string).group(1) if re.search(app_name_pattern, soup.title.string) else 'Unknown (name unavailable when closed)'
                beta_status = soup.find(class_="beta-status").get_text()

                status = "CLOSED" if closed_text in beta_status else "FULL" if full_text in beta_status else "OPEN"
                emoji_status = "🔴" if status == "CLOSED" else "🟠" if status == "FULL" else "🟢"

                console_status = f'[{status}] {app_name} {testflight_url + app_id}'
                pretty_status = f'{emoji_status} [{app_name}](<{testflight_url + app_id}>)'

                status_change = await track_status(app_id, status)
                if status_change is not None:
                    await send_discord_webhook(f'{pretty_status} {ping_role if ping_role and status == "OPEN" else ""}')
                if print_status_changes:
                    logging.info(console_status)

            await asyncio.sleep(60)

if __name__ == '__main__':
    asyncio.run(process_apps())
