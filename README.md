## Quickstart

```bash
git clone https://github.com/santiago-vl/jira-time-shooter jira-time-shooter
cd jira-time-shooter

python3 -m venv venv
./venv/bin/pip install -U pip
./venv/bin/pip install requests python-dotenv pytz


# Copy the example config and edit .env with your own values
cp .env.example .env

### Jira API Token

You need a Jira API token to authenticate.

1. Go to [Atlassian API Tokens](https://id.atlassian.com/manage-profile/security/api-tokens).
2. Click **Create API token**.
3. Give it a label (for example: "jira-time-shooter").
4. Copy the generated token.
5. Paste it into your `.env` file as the value for `JIRA_API_TOKEN`.

# Install cron: runs every weekday (Mon–Fri) at 10:00
( crontab -l 2>/dev/null; echo "0 10 * * 1-5 /bin/bash -lc 'cd \"$(pwd)\" && \"$(pwd)/venv/bin/python\" main.py >> \"$(pwd)/cron.log\" 2>&1'" ) | crontab -

# Remove the cron job (if you need to disable it)
crontab -r

# Test now:
./venv/bin/python main.py