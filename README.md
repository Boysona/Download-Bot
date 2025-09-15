 Download-Bot
 Here’s a clean README.md in English for your Telegram bot:

⸻

Telegram Video Downloader Bot

Short Description
This is a Telegram bot that allows you to download videos from multiple platforms (YouTube, Instagram, TikTok, X/Twitter, Facebook, Reddit, and more).
It is built with yt-dlp, pyTelegramBotAPI (telebot), and Flask for webhook handling.

⸻

Supported Platforms

The bot can handle video links from:
	•	youtube.com, youtu.be
	•	instagram.com, instagr.am
	•	tiktok.com
	•	twitter.com, x.com
	•	facebook.com, fb.watch
	•	reddit.com
	•	pinterest.com
	•	likee.video
	•	snapchat.com
	•	threads.net

⸻

⚠️ Important: Do not hardcode the token

Never commit your BOT_TOKEN directly in the code. Instead, use environment variables to store BOT_TOKEN and WEBHOOK_URL.

⸻

Project Structure Example
	•	bot.py → Main bot code
	•	requirements.txt → Python dependencies
	•	Procfile or Dockerfile → For deployment (Heroku/Render/VPS)
	•	.gitignore → Ignore sensitive and temporary files

Example requirements.txt

Flask==2.3.2
pyTelegramBotAPI==4.12.0
yt_dlp==2025.06.01
requests==2.31.0

Example .gitignore

downloads/
__pycache__/
*.pyc
.env


⸻

Local Setup
	1.	Clone the repository:

git clone <repo-url>
cd repo


	2.	Create a virtual environment and install dependencies:

python -m venv venv
source venv/bin/activate   # Linux/macOS
venv\Scripts\activate      # Windows
pip install -r requirements.txt


	3.	Set environment variables:

export BOT_TOKEN="YOUR_TELEGRAM_BOT_TOKEN"
export WEBHOOK_URL="https://your-domain.com/$BOT_TOKEN"

On Windows PowerShell:

$env:BOT_TOKEN="YOUR_TELEGRAM_BOT_TOKEN"
$env:WEBHOOK_URL="https://your-domain.com/$env:BOT_TOKEN"


	4.	Run the bot locally:

python bot.py

(If running locally, you need a tunneling service like ngrok so Telegram can reach your server via HTTPS.)

⸻

Deployment
	•	Render/Heroku: Connect your repo, create a web service, and set BOT_TOKEN + WEBHOOK_URL in environment variables.
	•	Docker: Create a Dockerfile, build and run the container with environment variables.

Example Procfile

web: python bot.py


⸻

Security Improvement

Update your code to read the token from environment variables instead of hardcoding:

import os

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is required")

WEBHOOK_URL = os.environ.get("WEBHOOK_URL")


⸻

Usage
	1.	Send the bot a link from one of the supported platforms.
	2.	The bot will return video details (title, duration, uploader).
	3.	It will then download and send the video back to you in Telegram.
	4.	Use /start or /help to see the welcome message.

⸻

Troubleshooting
	•	Video download failed → Check if the link is public and supported by yt-dlp.
	•	Webhook error (500) → Verify that your WEBHOOK_URL is correct, HTTPS-enabled, and that your BOT_TOKEN is valid.
	•	Storage issues → Videos are saved in downloads/. The bot deletes files after sending, but ensure your server has enough space.

