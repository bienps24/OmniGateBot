📌 Gatekeeper Telegram Bot
A Professional Group & Channel Join Manager (Auto-Approve + Filters)

Gatekeeper is a smart Telegram join manager bot that automatically approves or filters join requests in groups and channels.
It supports admin commands, spam protection, filters, and per-chat configuration.

Perfect for:

Public communities

Channels na maraming join attempts

Anti-bot filtering

Automated moderation

🚀 Features (Option C – Pro Version)
✅ Core Features

Auto-approve join requests

Works for both groups and channels

Sends welcome DM in simple, universal English

Error reporting to global admin

Professional logging (timestamped)

🔥 Smart Filters

Block bots (ON/OFF)

Require username (ON/OFF)

Minimum username length

Filter mode vs. auto mode

Decline notifications sent to admin

🛠 Admin Commands
Command	Function
/start	Shows intro
/help	List of commands
/status	Stats (today + total)
/settings	Current chat configuration
/set_mode auto/filtered/off	Set join handling mode
/set_require_username on/off	Require username
/set_block_bots on/off	Block accounts marked as bots
/set_min_username_length <n>	Username length rule
/test_join	Simulate how a join would be processed
🧠 Configuration Modes
AUTO

Bot approves all join requests.

FILTERED

Bot checks:

if user is bot

if username required

if username meets length requirement

Approves or declines depending on rules.

OFF

Bot will not handle requests.
(Useful if owner wants to manually check joiners.)

🗂 Project Structure
├─ main.py
├─ requirements.txt
├─ Procfile
└─ README.md

🔧 Installation & Setup
1. Clone the Repo
git clone https://github.com/<your-username>/<repo-name>.git

2. Install Requirements Locally (Optional)
pip install -r requirements.txt

🔐 Environment Variables (Required in Railway)

Go to:
Railway → Project → Variables → Add Variable

Name	Description
BOT_TOKEN	Token from BotFather
ADMIN_ID	Telegram user ID of the bot owner
🚀 Deploy to Railway

Push project to GitHub

Go to Railway → New Project → Deploy from GitHub

Add environment variables

Railway will auto-detect Python

Bot will start automatically

Use logs to monitor:

railway logs

🤝 Contributing

Pull requests are welcome.

📜 License

MIT License (optional)

🏁 Done!

The bot is now fully ready to run 24/7 on Railway.

🟢 3. Paano i-setup sa GitHub (Step-by-Step)

Gawa ka muna ng folder sa PC mo
Example: gatekeeper-bot

Ilagay ang files:

main.py

requirements.txt

Procfile

README.md

Sa folder, right-click → Open in Terminal

Mga command:

git init
git add .
git commit -m "Initial commit - Gatekeeper Bot"


Sa GitHub:

Create ➝ New Repository

Name: gatekeeper-telegram-bot

DO NOT add README (meron ka na)

I-link local folder to GitHub repo:

git remote add origin https://github.com/<your-username>/<repo-name>.git
git push -u origin main


Tapos i-connect sa Railway:

Railway → New Project

Deploy GitHub Repo

Add env vars

Start service
